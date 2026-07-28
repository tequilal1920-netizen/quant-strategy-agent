"""Refreshers for authoritative public data published outside Wind."""

from __future__ import annotations

import base64
import calendar
import io
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urljoin


AMAC_PRIVATE_MONTHLY_URL = (
    "https://www.amac.org.cn/portal/front/pri/priFundData/"
    "getPriFundData?timeType=1&productType=2"
)
AMAC_REFERER = (
    "https://www.amac.org.cn/sjtj/datastatistics/"
    "privategravefundindustrydata/"
)


def _month_end(raw: str) -> str:
    year_text, month_text = raw.split("M", 1)
    year, month = int(year_text), int(month_text)
    return date(year, month, calendar.monthrange(year, month)[1]).isoformat()


def refresh_amac_private_aum(
    cache: Any,
    run_id: str,
    selected: set[str] | None,
    contracts: Mapping[str, Any],
    finite_float: Callable[[Any], float],
) -> dict[str, Any]:
    series_id = "private.aum"
    if selected is not None and series_id not in selected:
        return {"refreshed": [], "errors": {}}
    request = urllib.request.Request(
        AMAC_PRIVATE_MONTHLY_URL,
        headers={
            "Accept": "application/json",
            "Referer": AMAC_REFERER,
            "User-Agent": "QuantStrategyAgent-Liquidity/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if payload.get("code") != 200:
            raise ValueError(f"AMAC response code {payload.get('code')}")
        data = payload["data"]["data"]
        rows = data["fundSizeList"]
        values = {
            _month_end(str(row["excelTime"])): finite_float(
                row["priSecurityInvestFund"]
            )
            for row in rows
            if row.get("excelTime") and row.get("priSecurityInvestFund") is not None
        }
        item = contracts[series_id]
        cache.replace_series(
            series_id,
            values,
            item.preferred_provider,
            AMAC_PRIVATE_MONTHLY_URL,
            run_id,
        )
        return {"refreshed": [series_id], "errors": {}}
    except Exception as error:
        return {
            "refreshed": [],
            "errors": {"amac_private_aum": f"{type(error).__name__}: {error}"},
        }

CSDATA_ROOT = "https://www.csdata.cn"
CSDATA_MARGIN_MONTHLY_INDEX = (
    CSDATA_ROOT + "/cmsmc/cgfb/rzrqsj/ydtj/index.html"
)
CSDATA_MARGIN_MONTHLY_HISTORY = (
    CSDATA_ROOT
    + "/cmsmc/articleFileDir/2025-02/21/"
    + "f5e5b98788af4aa08ae2d860679ba3ab.xls"
)
CREFI_MONTHLY_LIST = (
    "https://www.crctrust.com/rcms-external-rest/trust/monthlyPdf"
    "?siteId=8142&channelId=52423&pageSize=100&pageNo=1"
)
CREFI_ROOT = "https://www.crctrust.com"


def _chrome_executable() -> str:
    configured = os.environ.get("LIQUIDITY_CHROME_PATH", "").strip()
    candidates = [
        configured,
        shutil.which("chrome") or "",
        shutil.which("msedge") or "",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise RuntimeError(
        "CREFI refresh requires Chrome/Edge or LIQUIDITY_CHROME_PATH"
    )


def _cdp_command(connection: Any, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    message_id = 1
    connection.send(
        json.dumps(
            {"id": message_id, "method": method, "params": params or {}},
            ensure_ascii=False,
        )
    )
    while True:
        message = json.loads(connection.recv())
        if message.get("id") == message_id:
            if "error" in message:
                raise RuntimeError(f"Chrome DevTools error: {message['error']}")
            return message.get("result", {})


def _crefi_browser_session(
    url: str,
    download_urls: tuple[str, ...] = (),
) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Execute the publisher's challenge and optionally download PDFs in-browser."""

    from websocket import create_connection

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
    creation_flags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
    with tempfile.TemporaryDirectory(
        prefix="liquidity-crefi-", ignore_cleanup_errors=True
    ) as profile:
        process = subprocess.Popen(
            [
                _chrome_executable(),
                "--headless=new",
                "--disable-gpu",
                "--no-first-run",
                "--no-default-browser-check",
                "--remote-allow-origins=*",
                f"--remote-debugging-port={port}",
                f"--user-data-dir={profile}",
                url,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creation_flags,
        )
        connection = None
        try:
            deadline = time.monotonic() + 60
            target: dict[str, Any] | None = None
            while time.monotonic() < deadline:
                try:
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/json/list", timeout=2
                    ) as response:
                        pages = json.loads(response.read().decode("utf-8"))
                    target = next(
                        (page for page in pages if page.get("type") == "page"),
                        None,
                    )
                    if target:
                        break
                except Exception:
                    time.sleep(0.2)
            if not target:
                raise TimeoutError("Chrome DevTools target did not become ready")
            connection = create_connection(
                target["webSocketDebuggerUrl"],
                timeout=120,
                origin=f"http://127.0.0.1:{port}",
            )
            while time.monotonic() < deadline:
                evaluated = _cdp_command(
                    connection,
                    "Runtime.evaluate",
                    {
                        "expression": (
                            "JSON.stringify({text:document.body.innerText,"
                            "userAgent:navigator.userAgent})"
                        ),
                        "returnByValue": True,
                    },
                )
                raw = evaluated.get("result", {}).get("value")
                if raw:
                    state = json.loads(raw)
                    body_text = str(state.get("text", "")).strip()
                    if body_text.startswith("{"):
                        payload = json.loads(body_text)
                        downloads: dict[str, bytes] = {}
                        for download_url in download_urls:
                            encoded_url = json.dumps(download_url, ensure_ascii=False)
                            expression = f"""
                                (async () => {{
                                    const response = await fetch({encoded_url}, {{credentials:'include'}});
                                    const bytes = new Uint8Array(await response.arrayBuffer());
                                    let binary = '';
                                    const chunk = 32768;
                                    for (let offset = 0; offset < bytes.length; offset += chunk) {{
                                        binary += String.fromCharCode(...bytes.subarray(offset, offset + chunk));
                                    }}
                                    return JSON.stringify({{
                                        status: response.status,
                                        contentType: response.headers.get('content-type') || '',
                                        body: btoa(binary)
                                    }});
                                }})()
                            """
                            downloaded = _cdp_command(
                                connection,
                                "Runtime.evaluate",
                                {
                                    "expression": expression,
                                    "awaitPromise": True,
                                    "returnByValue": True,
                                },
                            )
                            raw_download = downloaded.get("result", {}).get("value")
                            if not raw_download:
                                raise RuntimeError(f"CREFI browser returned no body: {download_url}")
                            result = json.loads(raw_download)
                            pdf = base64.b64decode(result.get("body", ""))
                            if int(result.get("status", 0)) != 200 or not pdf.startswith(b"%PDF"):
                                raise RuntimeError(
                                    "CREFI browser download failed "
                                    f"({result.get('status')}, {result.get('contentType')}): "
                                    f"{download_url}"
                                )
                            downloads[download_url] = pdf
                        return payload, downloads
                time.sleep(0.25)
            raise TimeoutError("CREFI browser challenge did not resolve to JSON")
        finally:
            if connection is not None:
                connection.close()
            if os.name == "nt":
                subprocess.run(
                    ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    creationflags=creation_flags,
                )
            else:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def _fetch_bytes(
    url: str,
    *,
    referer: str | None = None,
    extra_headers: Mapping[str, str] | None = None,
) -> bytes:
    headers = {
        "Accept": "*/*",
        "User-Agent": "QuantStrategyAgent-Liquidity/1.0",
    }
    if referer:
        headers["Referer"] = referer
    if extra_headers:
        headers.update(extra_headers)
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def _latest_csdata_monthly_url() -> str:
    html = _fetch_bytes(CSDATA_MARGIN_MONTHLY_INDEX).decode("utf-8", "replace")
    matches = re.findall(
        r"href=[\"']([^\"']+\.xls)[\"']",
        html,
        flags=re.IGNORECASE,
    )
    if not matches:
        raise ValueError("CSData monthly page exposed no XLS download")
    return urljoin(CSDATA_MARGIN_MONTHLY_INDEX, matches[0])


def _parse_margin_month(raw: Any) -> str | None:
    text = str(raw).strip()
    match = re.fullmatch(r"(\d{4})\.(\d{1,2})", text)
    if not match:
        return None
    year, month = int(match.group(1)), int(match.group(2))
    if not 1 <= month <= 12:
        return None
    return date(year, month, calendar.monthrange(year, month)[1]).isoformat()


def _read_csdata_margin_xls(
    payload: bytes,
    finite_float: Callable[[Any], float],
) -> dict[str, dict[str, float]]:
    import pandas as pd

    frame = pd.read_excel(io.BytesIO(payload), header=None, engine="xlrd")
    headers = frame.iloc[:4]

    def find_column(predicate: Callable[[str], bool]) -> int:
        for column in frame.columns:
            for value in headers[column].tolist():
                if not pd.isna(value) and predicate(str(value).strip()):
                    return int(column)
        raise ValueError("CSData monthly table header changed")

    cash_column = find_column(lambda value: value == "担保资金")
    securities_column = find_column(lambda value: value == "小计")
    ratio_column = find_column(lambda value: "全市场平均担保比例" in value)
    values = {
        "margin.guarantee_ratio": {},
        "margin.collateral_cash": {},
        "margin.collateral_securities": {},
    }
    for _, row in frame.iterrows():
        when = _parse_margin_month(row.iloc[0])
        if not when:
            continue
        raw_values = (
            row.iloc[ratio_column],
            row.iloc[cash_column],
            row.iloc[securities_column],
        )
        if any(pd.isna(value) for value in raw_values):
            continue
        values["margin.guarantee_ratio"][when] = finite_float(raw_values[0])
        values["margin.collateral_cash"][when] = finite_float(raw_values[1])
        values["margin.collateral_securities"][when] = finite_float(raw_values[2])
    return values


def refresh_csdata_margin_monthly(
    cache: Any,
    run_id: str,
    start: date,
    selected: set[str] | None,
    contracts: Mapping[str, Any],
    finite_float: Callable[[Any], float],
) -> dict[str, Any]:
    target_ids = {
        "margin.guarantee_ratio",
        "margin.collateral_cash",
        "margin.collateral_securities",
    }
    active = target_ids if selected is None else target_ids & selected
    if not active:
        return {"refreshed": [], "errors": {}}
    try:
        latest_url = _latest_csdata_monthly_url()
        history = _read_csdata_margin_xls(
            _fetch_bytes(CSDATA_MARGIN_MONTHLY_HISTORY), finite_float
        )
        current = _read_csdata_margin_xls(_fetch_bytes(latest_url), finite_float)
        refreshed: list[str] = []
        quality: dict[str, Any] = {}
        for series_id in sorted(active):
            values = {**history[series_id], **current[series_id]}
            values = {
                when: value
                for when, value in values.items()
                if start <= date.fromisoformat(when) <= date.today()
            }
            ordered = sorted(values)
            for previous, current_date in zip(ordered, ordered[1:]):
                left, right = date.fromisoformat(previous), date.fromisoformat(current_date)
                month_gap = (right.year - left.year) * 12 + right.month - left.month
                if month_gap != 1:
                    raise ValueError(
                        f"{series_id}: non-contiguous monthly observations "
                        f"between {previous} and {current_date}"
                    )
            item = contracts[series_id]
            cache.replace_series(
                series_id,
                values,
                item.preferred_provider,
                f"{CSDATA_MARGIN_MONTHLY_HISTORY} + {latest_url}",
                run_id,
            )
            refreshed.append(series_id)
            quality[series_id] = {
                "observations": len(ordered),
                "start": ordered[0],
                "end": ordered[-1],
            }
        return {"refreshed": refreshed, "errors": {}, "quality": quality}
    except Exception as error:
        return {
            "refreshed": [],
            "errors": {"csdata_margin": f"{type(error).__name__}: {error}"},
        }


def _existing_official_series(
    cache: Any,
    series_id: str,
    provider: str,
) -> dict[str, float]:
    cache.initialize()
    with cache.connect(read_only=True) as connection:
        rows = connection.execute(
            "SELECT observation_date,value FROM observations "
            "WHERE series_id=? AND provider=? ORDER BY observation_date",
            (series_id, provider),
        ).fetchall()
    return {str(row[0]): float(row[1]) for row in rows}


def _extract_crefi_position(pdf_payload: bytes) -> float:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(pdf_payload))
    text_parts: list[str] = []
    for page in reader.pages[: min(7, len(reader.pages))]:
        text_parts.append(page.extract_text() or "")
        compact = re.sub(r"\s+", "", "".join(text_parts))
        match = re.search(
            r"平均股票仓位(?:为|约为|约)?([0-9]+(?:\.[0-9]+)?)%",
            compact,
        )
        if match:
            value = float(match.group(1))
            if not 0 <= value <= 100:
                raise ValueError(f"CREFI position outside 0-100: {value}")
            return value
    raise ValueError("CREFI PDF did not expose average stock position")


def refresh_crefi_position(
    cache: Any,
    run_id: str,
    start: date,
    selected: set[str] | None,
    contracts: Mapping[str, Any],
) -> dict[str, Any]:
    series_id = "private.stock_long_position"
    if selected is not None and series_id not in selected:
        return {"refreshed": [], "errors": {}}
    provider = contracts[series_id].preferred_provider
    try:
        payload, _ = _crefi_browser_session(CREFI_MONTHLY_LIST)
        if payload.get("code") != "S1A00000":
            raise ValueError(f"CREFI list response code {payload.get('code')}")
        reports = payload["data"]["list"]
        total_count = int(payload["data"].get("totalCount", len(reports)))
        if len(reports) != total_count:
            raise ValueError(
                f"CREFI list incomplete: {len(reports)} of {total_count} reports"
            )
        values = _existing_official_series(cache, series_id, provider)
        missing_reports: list[str] = []
        pending: list[tuple[str, str, str]] = []
        for report in reports:
            name = str(report.get("fileName", ""))
            match = re.search(r"(20\d{2})\.(\d{1,2})", name)
            if not match:
                continue
            year, month = int(match.group(1)), int(match.group(2))
            when = date(year, month, calendar.monthrange(year, month)[1])
            key = when.isoformat()
            if when < start or when > date.today() or key in values:
                continue
            path = str(report.get("filePath", ""))
            if not path:
                missing_reports.append(name)
                continue
            pending.append((name, key, urljoin(CREFI_ROOT, path)))
        pdf_payloads: dict[str, bytes] = {}
        for offset in range(0, len(pending), 5):
            chunk = pending[offset : offset + 5]
            chunk_urls = tuple(item[2] for item in chunk)
            try:
                _, downloaded = _crefi_browser_session(
                    CREFI_MONTHLY_LIST,
                    chunk_urls,
                )
                pdf_payloads.update(downloaded)
            except Exception:
                for _, _, pdf_url in chunk:
                    for _attempt in range(3):
                        try:
                            _, downloaded = _crefi_browser_session(
                                CREFI_MONTHLY_LIST,
                                (pdf_url,),
                            )
                            pdf_payloads.update(downloaded)
                            break
                        except Exception:
                            continue
        for name, key, pdf_url in pending:
            try:
                values[key] = _extract_crefi_position(pdf_payloads[pdf_url])
            except Exception:
                missing_reports.append(name)
        if missing_reports:
            raise ValueError(
                "unparsed CREFI reports: " + ", ".join(missing_reports[:5])
            )
        ordered = sorted(values)
        for previous, current_date in zip(ordered, ordered[1:]):
            left, right = date.fromisoformat(previous), date.fromisoformat(current_date)
            if (right.year - left.year) * 12 + right.month - left.month != 1:
                raise ValueError(
                    f"CREFI monthly gap between {previous} and {current_date}"
                )
        cache.replace_series(
            series_id,
            values,
            provider,
            CREFI_MONTHLY_LIST,
            run_id,
        )
        return {
            "refreshed": [series_id],
            "errors": {},
            "quality": {
                "observations": len(ordered),
                "start": ordered[0],
                "end": ordered[-1],
            },
        }
    except Exception as error:
        return {
            "refreshed": [],
            "errors": {"crefi_position": f"{type(error).__name__}: {error}"},
        }
