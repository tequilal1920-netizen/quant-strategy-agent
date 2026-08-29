"""Refresh industry/style rotation month-end signals after market data updates.

Workflow:

1. Extend the 31 SW industry close cache after the last official cache date
   using stock-level real qfq returns and point-in-time SW membership.
2. Rebuild the industry rotation snapshot with a month-end signal cutoff, so a
   partial current month is not mistaken for a new monthly recommendation.
3. Write the selected monthly industry score into ``v3_industry_signal`` at
   real stock-trading month-end dates.  The style rotation model then maps
   these industry scores into style boxes without looking ahead.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parents[1]
WAREHOUSE = PROJECT_ROOT / "database" / "research_warehouse.db"
MARKET_CACHE = PROJECT_ROOT / "output" / "industry_rotation" / "cache" / "market"
DEFAULT_SNAPSHOT = PROJECT_ROOT / "board" / "quant_strategy_agent" / "data" / "rotation_snapshot.json"
DEFAULT_SOURCE_XLSX = Path(r"G:\招银理财\行业景气0507\main\data.xlsx")
RUN_ID = "v3_strict_integrated_20260706"
UNIVERSE = "CSI800_ENH"
DEFAULT_CANDIDATE = "C39_monthly_post_test_diagnostic_six_dimension_prosperity_earnings_top7_risk_weighted_buffered"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from catalog import INDUSTRY_CODES  # noqa: E402


def compact(value: str | pd.Timestamp) -> str:
    return pd.Timestamp(value).strftime("%Y%m%d")


def iso(value: str | pd.Timestamp) -> str:
    return pd.Timestamp(value).strftime("%Y-%m-%d")


def read_close_cache(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, parse_dates=["date"])
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    return frame.dropna(subset=["date", "close"]).sort_values("date")


def load_stock_industry_returns(conn: sqlite3.Connection, start_date: str, end_date: str) -> pd.DataFrame:
    query = """
        select o.trade_date, o.ts_code, o.qfq_close, m.industry_name
        from stock_ohlcv_daily o
        join sw_l1_industry_daily m
          on m.ts_code = o.ts_code
         and m.start_date <= o.trade_date
         and (m.end_date is null or m.end_date > o.trade_date)
        where o.trade_date between ? and ?
          and o.qfq_close is not null
          and (o.ts_code like '%.SH' or o.ts_code like '%.SZ')
        order by o.ts_code, o.trade_date
    """
    frame = pd.read_sql_query(query, conn, params=(start_date, end_date))
    if frame.empty:
        return pd.DataFrame()
    frame["date"] = pd.to_datetime(frame["trade_date"], format="%Y%m%d")
    frame["qfq_close"] = pd.to_numeric(frame["qfq_close"], errors="coerce")
    frame["stock_return"] = frame.groupby("ts_code", sort=False)["qfq_close"].pct_change(fill_method=None)
    returns = (
        frame.dropna(subset=["stock_return"])
        .groupby(["date", "industry_name"], sort=True)["stock_return"]
        .mean()
        .unstack("industry_name")
        .sort_index()
    )
    return returns.replace([np.inf, -np.inf], np.nan)


def extend_industry_close_cache(end_date: str) -> dict[str, Any]:
    cache_files = sorted(MARKET_CACHE.glob("sw_*.csv"))
    if not cache_files:
        raise FileNotFoundError(f"industry cache is empty: {MARKET_CACHE}")
    latest_by_code: dict[str, pd.Timestamp] = {}
    for path in cache_files:
        code = path.stem.replace("sw_", "")
        frame = read_close_cache(path)
        latest_by_code[code] = pd.Timestamp(frame["date"].max())
    official_last = min(latest_by_code.values())
    if official_last >= pd.Timestamp(end_date):
        return {"status": "ready", "message": f"industry cache already covers {iso(official_last)}", "rows_appended": 0, "end": iso(official_last)}

    start_for_returns = compact(official_last)
    with sqlite3.connect(f"file:{WAREHOUSE.as_posix()}?mode=ro", uri=True) as conn:
        returns = load_stock_industry_returns(conn, start_for_returns, compact(end_date))
    append_dates = returns.index[returns.index > official_last]
    rows_appended = 0
    if len(append_dates) == 0:
        return {"status": "blocked", "message": "no stock-level returns available after industry cache end", "rows_appended": 0, "end": iso(official_last)}

    for industry, code in INDUSTRY_CODES.items():
        path = MARKET_CACHE / f"sw_{code}.csv"
        frame = read_close_cache(path)
        last_close = float(frame["close"].iloc[-1])
        additions: list[dict[str, Any]] = []
        if industry not in returns.columns:
            continue
        for date, ret in returns.loc[append_dates, industry].dropna().items():
            last_close *= 1.0 + float(ret)
            additions.append({"date": pd.Timestamp(date), "close": round(last_close, 6)})
        if additions:
            updated = pd.concat([frame, pd.DataFrame(additions)], ignore_index=True)
            updated = updated.drop_duplicates("date", keep="last").sort_values("date")
            updated.to_csv(path, index=False, encoding="utf-8")
            rows_appended += len(additions)
    manifest_path = MARKET_CACHE / "sw_extension_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "method": "2026-07-17以后用stock_ohlcv_daily真实复权收益与sw_l1_industry_daily PIT申万一级行业归属做成分等权延拓；不冒充申万官方指数。",
                "official_cache_end": iso(official_last),
                "requested_end": iso(end_date),
                "actual_end": iso(append_dates.max()),
                "rows_appended": rows_appended,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "status": "ready" if rows_appended else "blocked",
        "official_cache_end": iso(official_last),
        "actual_end": iso(append_dates.max()),
        "rows_appended": rows_appended,
    }


def month_end_trade_dates(conn: sqlite3.Connection, end_date: str) -> list[pd.Timestamp]:
    frame = pd.read_sql_query(
        """
        select distinct trade_date
        from stock_ohlcv_daily
        where trade_date <= ?
        order by trade_date
        """,
        conn,
        params=(compact(end_date),),
    )
    dates = pd.DatetimeIndex(pd.to_datetime(frame["trade_date"], format="%Y%m%d"))
    month_ends = dates.to_series(index=dates).groupby(dates.to_period("M")).max()
    return [pd.Timestamp(value) for value in month_ends if pd.Timestamp(value) <= pd.Timestamp(end_date)]


def rebuild_industry_snapshot(source_xlsx: Path, signal_cutoff: str, output: Path) -> dict[str, Any]:
    os.environ["INDUSTRY_ROTATION_SOURCE_XLSX"] = str(source_xlsx)
    os.environ["INDUSTRY_ROTATION_SIGNAL_CUTOFF"] = compact(signal_cutoff)
    import build_snapshot  # type: ignore
    import engine as worker  # type: ignore

    build_snapshot.configure()
    return worker.build(output)


def sync_v3_industry_signal(candidate: str, signal_cutoff: str, run_id: str) -> dict[str, Any]:
    import six_dimension_model as six  # type: ignore

    state = six.get_state()
    if state is None or candidate not in state.candidates:
        available = sorted(state.candidates) if state is not None else []
        raise KeyError(f"candidate_not_available:{candidate}; available={available[:20]}")
    score = state.candidates[candidate].copy()
    score.index = pd.DatetimeIndex(score.index)
    with sqlite3.connect(WAREHOUSE) as conn:
        signal_dates = month_end_trade_dates(conn, signal_cutoff)
        rows: list[tuple[Any, ...]] = []
        for date in signal_dates:
            if date not in score.index:
                continue
            row = score.loc[date].dropna()
            if row.empty:
                continue
            positive = row.clip(lower=0.0)
            denominator = float(positive.sum()) if float(positive.sum()) > 0 else float(len(row))
            for industry, value in row.items():
                weight = float(max(float(value), 0.0) / denominator) if denominator > 0 else 0.0
                rows.append(
                    (
                        run_id,
                        compact(date),
                        UNIVERSE,
                        str(industry),
                        float(value),
                        weight,
                        json.dumps(
                            {
                                "source": "industry_rotation_six_dimension_month_end",
                                "candidate": candidate,
                                "timing": "month_end_close_for_next_month",
                            },
                            ensure_ascii=False,
                        ),
                    )
                )
        conn.executemany(
            """
            insert or replace into v3_industry_signal
            (run_id, rebalance_date, universe, industry_name, score, target_weight, reason_json)
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
    return {
        "status": "ready" if rows else "blocked",
        "rows": len(rows),
        "month_end_count": len(signal_dates),
        "latest_signal": compact(max(signal_dates)) if signal_dates else None,
        "candidate": candidate,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--end", required=True, help="Latest close date used for NAV extension, e.g. 20260820.")
    parser.add_argument("--signal-cutoff", required=True, help="Month-end signal date used for next-month holdings, e.g. 20260730.")
    parser.add_argument("--source-xlsx", default=str(DEFAULT_SOURCE_XLSX))
    parser.add_argument("--output", default=str(DEFAULT_SNAPSHOT))
    parser.add_argument("--candidate", default=DEFAULT_CANDIDATE)
    parser.add_argument("--run-id", default=RUN_ID)
    parser.add_argument("--out", default=str(PROJECT_ROOT / "output" / "industry_rotation" / "month_end_refresh_result.json"))
    args = parser.parse_args()

    source_xlsx = Path(args.source_xlsx)
    if not source_xlsx.exists():
        raise FileNotFoundError(source_xlsx)
    cache_result = extend_industry_close_cache(compact(args.end))
    snapshot = rebuild_industry_snapshot(source_xlsx, compact(args.signal_cutoff), Path(args.output))
    sync_result = sync_v3_industry_signal(args.candidate, compact(args.signal_cutoff), args.run_id)
    result = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "cache": cache_result,
        "snapshot": {
            "output": str(Path(args.output)),
            "as_of": snapshot.get("as_of"),
            "monthly_latest_ranking_date": snapshot.get("industry", {}).get("frequencies", {}).get("monthly", {}).get("ranking", [{}])[0].get("date"),
            "selected_candidate": snapshot.get("industry", {}).get("frequencies", {}).get("monthly", {}).get("selected_candidate"),
            "research_selected_candidate": snapshot.get("industry", {}).get("frequencies", {}).get("monthly", {}).get("research_selected_candidate"),
        },
        "v3_industry_signal": sync_result,
        "signal_cutoff": compact(args.signal_cutoff),
        "nav_end": compact(args.end),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
