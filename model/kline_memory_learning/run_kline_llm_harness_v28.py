"""V28 K-line domain memory harness with optional LLM rule evolution.

This layer deliberately keeps the V27 strategy backbone unchanged.  It adds the
missing Harness-style memory loop requested by the user:

K-line shape library -> domain split -> memory retrieval -> performance audit
-> trend learning -> evolve/patch decision -> single-stock timing explanation.

If an OpenAI-compatible API key is configured, the rule reflection step calls an
LLM and requires JSON output.  Without a key it uses the same JSON schema with a
deterministic offline reviewer and records that fact in the manifest.  Accepted
patches are written as memories, but they are not applied to V27 positions unless
future runs explicitly opt in, so this script cannot degrade the current model
performance.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from model.kline_memory_learning.run_wyckoff_memory_batch import DEFAULT_DB, _load_stock, _safe_name  # noqa: E402
from model.kline_memory_learning.run_wyckoff_style_size_domain_rulebook_v26_event_memory_evolver import (  # noqa: E402
    DEFAULT_CACHE,
    DEFAULT_CODES,
    _learn_rulebook,
    _load_events,
)
from model.kline_memory_learning.run_wyckoff_style_size_memory_batch import DOMAIN_COL  # noqa: E402


DEFAULT_V27_DIR = Path("agent/output/kline_memory_learning/v27_shared_teacher_lowfreq")
DEFAULT_OUTPUT_DIR = Path("agent/output/kline_memory_learning/v28_llm_harness_evolver")

NUMERIC_CONTEXT_COLS = [
    "ret20",
    "ret60",
    "vol20",
    "range20",
    "volume_ratio",
    "range_ratio",
    "amount_ratio",
    "close_position",
]


@dataclass
class RuleAudit:
    domain: str
    rule_id: str
    metrics: Dict[str, Any]
    base_score: float
    candidate_conditions: List[Dict[str, Any]]
    patch_metrics: Dict[str, Any]
    patch_accepted: bool
    llm_review: Dict[str, Any]
    cases: Dict[str, List[Dict[str, Any]]]


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
        return out if math.isfinite(out) else default
    except Exception:
        return default


def _safe_pct(value: float) -> str:
    if not math.isfinite(value):
        return "NA"
    return f"{value:.2%}"


def _rule_family(rule_id: str) -> str:
    if "CANDLE" in rule_id or "GAP" in rule_id:
        return "蜡烛图/跳空"
    if "MA_STACK" in rule_id or "GOLDEN_CROSS" in rule_id or "DEATH_CROSS" in rule_id:
        return "均线趋势"
    if "BREAKOUT" in rule_id or "BREAKDOWN" in rule_id:
        return "突破/跌破/假突破"
    if "VOL_CONTRACT" in rule_id or "VOLUME_PRICE" in rule_id or "VOLUME" in rule_id:
        return "量价结构"
    if "MOMENTUM" in rule_id:
        return "动量趋势"
    if "PANIC" in rule_id or "EXHAUST" in rule_id:
        return "恐慌/衰竭"
    if "CLOSE_POS" in rule_id:
        return "收盘位置"
    return "复合形态"


def _humanize_rule(rule_id: str) -> str:
    text = rule_id
    replacements = {
        "TECH_": "",
        "_BULL": " 看多",
        "_BEAR": " 看空",
        "MOMENTUM_UP": "动量上行",
        "MOMENTUM_DOWN": "动量下行",
        "MA_STACK": "均线排列",
        "GOLDEN_CROSS": "金叉",
        "DEATH_CROSS": "死叉",
        "VOL_CONTRACT_BREAKOUT": "缩量突破",
        "VOL_CONTRACT_BREAKDOWN": "缩量跌破",
        "VOLUME_PRICE_UP": "量价上行",
        "BREAKOUT_HIGH": "突破高点",
        "BREAKDOWN_LOW": "跌破低点",
        "FALSE_BREAKOUT_HIGH": "高位假突破",
        "FALSE_BREAKDOWN_LOW": "低位假跌破",
        "PANIC_DOWN": "恐慌下跌",
        "EXHAUST_UP": "上涨衰竭",
        "CANDLE_BULL_ENGULF_LOW": "低位阳包阴",
        "CANDLE_BEAR_ENGULF_HIGH": "高位阴包阳",
        "GAP_UP_CONT": "向上跳空延续",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text.replace("_", " ")


def _evaluate_events(frame: pd.DataFrame) -> Dict[str, Any]:
    signed = pd.to_numeric(frame.get("signed_return"), errors="coerce").dropna().clip(-0.35, 0.35)
    if signed.empty:
        return {
            "n_events": 0,
            "n_stocks": 0,
            "mean_signed_return": 0.0,
            "hit_rate": 0.0,
            "volatility": 0.0,
            "payoff": 0.0,
            "t_stat": 0.0,
            "coverage_rate": 0.0,
        }
    n = int(len(signed))
    n_stocks = int(frame["ts_code"].nunique()) if "ts_code" in frame.columns else 0
    mean_edge = float(signed.mean())
    hit = float((signed > 0.0).mean())
    vol = float(signed.std(ddof=0))
    pos = signed[signed > 0]
    neg = signed[signed < 0]
    payoff = float(pos.mean() / max(abs(neg.mean()), 1e-6)) if len(pos) and len(neg) else float(3.0 if len(pos) else 0.0)
    n_eff = max(1, min(n, max(1, n_stocks) * 12))
    t_stat = float(mean_edge / max(vol, 1e-6) * math.sqrt(n_eff))
    return {
        "n_events": n,
        "n_stocks": n_stocks,
        "mean_signed_return": mean_edge,
        "hit_rate": hit,
        "volatility": vol,
        "payoff": payoff,
        "t_stat": t_stat,
        "coverage_rate": 0.0,
    }


def _metric_score(metrics: Dict[str, Any]) -> float:
    return (
        _as_float(metrics.get("mean_signed_return")) * 4.0
        + (_as_float(metrics.get("hit_rate")) - 0.5) * 0.8
        + min(_as_float(metrics.get("t_stat")), 8.0) * 0.035
        + math.tanh((_as_float(metrics.get("payoff")) - 1.0) * 0.8) * 0.08
    )


def _condition_mask(frame: pd.DataFrame, condition: Dict[str, Any]) -> pd.Series:
    col = str(condition["column"])
    op = str(condition["op"])
    value = _as_float(condition["value"])
    series = pd.to_numeric(frame[col], errors="coerce")
    if op == ">=":
        return series >= value
    if op == "<=":
        return series <= value
    raise ValueError(f"unsupported op: {op}")


def _condition_text(condition: Dict[str, Any]) -> str:
    names = {
        "ret20": "过去20日收益",
        "ret60": "过去60日收益",
        "vol20": "20日波动率",
        "range20": "20日振幅",
        "volume_ratio": "量比",
        "range_ratio": "振幅比",
        "amount_ratio": "成交额比",
        "close_position": "收盘价在日内区间位置",
    }
    col = str(condition["column"])
    value = _as_float(condition["value"])
    if col.startswith("ret"):
        value_text = _safe_pct(value)
    else:
        value_text = f"{value:.3f}"
    return f"{names.get(col, col)} {condition['op']} {value_text}"


def _candidate_conditions(frame: pd.DataFrame, base_metrics: Dict[str, Any]) -> tuple[List[Dict[str, Any]], Dict[str, Any], bool]:
    base_score = _metric_score(base_metrics)
    domain_stocks = max(int(frame["ts_code"].nunique()), 1)
    min_events = max(40, int(len(frame) * 0.25))
    min_stocks = max(8, int(domain_stocks * 0.20))
    candidates: List[Dict[str, Any]] = []
    for col in NUMERIC_CONTEXT_COLS:
        if col not in frame.columns:
            continue
        values = pd.to_numeric(frame[col], errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
        if len(values) < min_events:
            continue
        thresholds = []
        for q in (0.35, 0.50, 0.65):
            thresholds.append(float(values.quantile(q)))
        if col in ("ret20", "ret60"):
            thresholds.extend([0.0])
        if col == "close_position":
            thresholds.extend([0.35, 0.50, 0.65])
        for threshold in sorted(set(round(x, 6) for x in thresholds if math.isfinite(x))):
            for op in (">=", "<="):
                cond = {"column": col, "op": op, "value": float(threshold)}
                mask = _condition_mask(frame, cond)
                sub = frame.loc[mask.fillna(False)].copy()
                if len(sub) < min_events or sub["ts_code"].nunique() < min_stocks:
                    continue
                metrics = _evaluate_events(sub)
                retention = len(sub) / max(len(frame), 1)
                improvement = _metric_score(metrics) - base_score
                if improvement <= 0:
                    continue
                candidates.append(
                    {
                        **cond,
                        "condition_text": _condition_text(cond),
                        "n_events": int(len(sub)),
                        "n_stocks": int(sub["ts_code"].nunique()),
                        "retention": float(retention),
                        "metrics": metrics,
                        "improvement": float(improvement),
                    }
                )
    candidates.sort(key=lambda item: (item["improvement"], item["metrics"]["hit_rate"], item["retention"]), reverse=True)
    selected: List[Dict[str, Any]] = []
    used_cols: set[str] = set()
    for cand in candidates:
        if cand["column"] in used_cols:
            continue
        selected.append(cand)
        used_cols.add(str(cand["column"]))
        if len(selected) >= 2:
            break
    if not selected:
        return [], base_metrics, False
    mask = pd.Series(True, index=frame.index)
    for cond in selected:
        mask &= _condition_mask(frame, cond).fillna(False)
    patched = frame.loc[mask].copy()
    if len(patched) < min_events or patched["ts_code"].nunique() < min_stocks:
        return selected, base_metrics, False
    patch_metrics = _evaluate_events(patched)
    accepted = _metric_score(patch_metrics) >= base_score and patch_metrics["hit_rate"] >= base_metrics["hit_rate"]
    return selected, patch_metrics, bool(accepted)


def _descriptive_conditions(frame: pd.DataFrame, base_metrics: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Infer non-executable memory conditions by contrasting good/bad cases.

    These conditions are Harness scenario hypotheses. They are separate from
    the execution gate, so writing them cannot degrade the V27 trading backbone.
    """
    signed = pd.to_numeric(frame.get("signed_return"), errors="coerce")
    success = frame.loc[signed > 0]
    failure = frame.loc[signed <= 0]
    if len(success) < 20 or len(failure) < 20:
        return []
    base_score = _metric_score(base_metrics)
    candidates: List[Dict[str, Any]] = []
    for col in NUMERIC_CONTEXT_COLS:
        if col not in frame.columns:
            continue
        s = pd.to_numeric(success[col], errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
        f = pd.to_numeric(failure[col], errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
        all_values = pd.to_numeric(frame[col], errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
        if len(s) < 20 or len(f) < 20 or len(all_values) < 40:
            continue
        s_med = float(s.median())
        f_med = float(f.median())
        spread = float(all_values.quantile(0.75) - all_values.quantile(0.25))
        if not math.isfinite(spread) or spread <= 1e-9:
            continue
        op = ">=" if s_med >= f_med else "<="
        threshold = float((s_med + f_med) / 2.0)
        cond = {"column": col, "op": op, "value": threshold}
        mask = _condition_mask(frame, cond).fillna(False)
        sub = frame.loc[mask].copy()
        metrics = _evaluate_events(sub) if len(sub) else base_metrics
        separation = abs(s_med - f_med) / spread
        candidates.append(
            {
                **cond,
                "condition_text": _condition_text(cond),
                "source": "success_failure_contrast",
                "n_events": int(len(sub)),
                "n_stocks": int(sub["ts_code"].nunique()) if len(sub) and "ts_code" in sub.columns else 0,
                "retention": float(len(sub) / max(len(frame), 1)),
                "metrics": metrics,
                "improvement": float(_metric_score(metrics) - base_score),
                "separation": float(separation),
            }
        )
    candidates.sort(
        key=lambda item: (
            item["improvement"],
            item["metrics"].get("hit_rate", 0.0),
            item["separation"],
            item["retention"],
        ),
        reverse=True,
    )
    selected: List[Dict[str, Any]] = []
    used_cols: set[str] = set()
    for cand in candidates:
        if cand["column"] in used_cols:
            continue
        selected.append(cand)
        used_cols.add(str(cand["column"]))
        if len(selected) >= 2:
            break
    return selected


def _load_v27_rulebooks(v27_dir: Path) -> Dict[str, pd.DataFrame]:
    out: Dict[str, pd.DataFrame] = {}
    for path in sorted(v27_dir.glob("V27_rulebook_*.csv")):
        domain = path.stem.replace("V27_rulebook_", "")
        out[domain] = pd.read_csv(path)
    if not out:
        raise RuntimeError(f"no V27 rulebooks found in {v27_dir}")
    return out


def _date_key(value: Any) -> str:
    return str(value).replace("-", "").replace("/", "")[:8]


def _load_full_events(cache_path: Path) -> pd.DataFrame:
    """Load the full event memory table for V28 scenario learning.

    V26/V27's loader intentionally trims the table for fast execution. V28 needs
    the full context columns so the Harness layer can learn valid/invalid
    conditions from trend, volatility, volume and candle-position context.
    """
    events = pd.read_pickle(cache_path)
    required = ["ts_code", "date", DOMAIN_COL, "rule_id", "direction", "strength", "signed_return"]
    missing = [col for col in required if col not in events.columns]
    if missing:
        raise RuntimeError(f"events cache missing columns: {missing}")
    events = events.copy()
    events = events.dropna(subset=["ts_code", "date", DOMAIN_COL, "rule_id", "direction", "signed_return"])
    events["ts_code"] = events["ts_code"].astype(str)
    events["date_key"] = events["date"].map(_date_key)
    events[DOMAIN_COL] = events[DOMAIN_COL].astype(str)
    events["rule_id"] = events["rule_id"].astype(str)
    events["direction"] = pd.to_numeric(events["direction"], errors="coerce").fillna(0.0).astype(float)
    events["strength"] = pd.to_numeric(events["strength"], errors="coerce").fillna(1.0).clip(0.1, 2.0)
    for col in ["signed_return", "forward_return"] + NUMERIC_CONTEXT_COLS:
        if col in events.columns:
            events[col] = pd.to_numeric(events[col], errors="coerce")
    events = events.dropna(subset=["signed_return"])
    return events


def _select_case_rows(frame: pd.DataFrame, max_each: int = 2) -> Dict[str, pd.DataFrame]:
    local = frame.copy()
    local["signed_return"] = pd.to_numeric(local["signed_return"], errors="coerce")
    success = local.loc[local["signed_return"] > 0].sort_values("signed_return", ascending=False).head(max_each)
    failure = local.loc[local["signed_return"] <= 0].sort_values("signed_return", ascending=True).head(max_each)
    return {"passed": success, "failed": failure}


def _series_window(series: Any, event_date: str, radius: int = 60) -> Dict[str, Any]:
    dates = [str(d).replace("-", "")[:8] for d in series.dates]
    if event_date not in dates:
        return {"available": False, "reason": "event date not in stock series"}
    idx = dates.index(event_date)
    start = max(0, idx - radius)
    end = min(len(dates) - 1, idx + radius)
    close = np.asarray(series.close, dtype=float)
    volume = np.asarray(series.volume, dtype=float)
    anchor = max(float(close[idx]), 1e-9)
    base_vol = float(np.nanmean(volume[max(0, idx - 20) : idx + 1])) if idx > 0 else float(np.nanmean(volume[: idx + 1]))
    base_vol = max(base_vol, 1e-9)
    points = []
    for i in range(start, end + 1):
        points.append(
            {
                "offset": int(i - idx),
                "date": dates[i],
                "close_nav": round(float(close[i] / anchor), 6),
                "volume_rel": round(float(volume[i] / base_vol), 6),
            }
        )
    post = close[idx : end + 1] / anchor - 1.0
    pre = close[start : idx + 1] / max(float(close[start]), 1e-9) - 1.0
    return {
        "available": True,
        "event_date": event_date,
        "pre60_return": float(pre[-1]) if len(pre) else 0.0,
        "post20_return": float(close[min(idx + 20, len(close) - 1)] / anchor - 1.0),
        "post60_return": float(close[end] / anchor - 1.0),
        "post60_max_up": float(np.nanmax(post)) if len(post) else 0.0,
        "post60_max_down": float(np.nanmin(post)) if len(post) else 0.0,
        "points": points,
    }


def _case_payloads(conn: sqlite3.Connection, rows: pd.DataFrame, series_cache: Dict[str, Any]) -> List[Dict[str, Any]]:
    payloads: List[Dict[str, Any]] = []
    for row in rows.itertuples(index=False):
        code = str(getattr(row, "ts_code"))
        event_date = str(getattr(row, "date")).replace("-", "")[:8]
        if code not in series_cache:
            try:
                series_cache[code] = _load_stock(conn, code, "latest")
            except Exception:
                series_cache[code] = None
        window = _series_window(series_cache[code], event_date) if series_cache.get(code) is not None else {"available": False}
        payloads.append(
            {
                "ts_code": code,
                "stock_name": str(getattr(row, "stock_name", "")),
                "date": event_date,
                "future_date": str(getattr(row, "future_date", "")),
                "forward_return": _as_float(getattr(row, "forward_return", 0.0)),
                "signed_return": _as_float(getattr(row, "signed_return", 0.0)),
                "context": {
                    col: _as_float(getattr(row, col, 0.0))
                    for col in NUMERIC_CONTEXT_COLS
                    if hasattr(row, col)
                },
                "window_60d": window,
            }
        )
    return payloads


def _offline_review(
    domain: str,
    rule_id: str,
    metrics: Dict[str, Any],
    conditions: List[Dict[str, Any]],
    patch_metrics: Dict[str, Any],
    accepted: bool,
    cases: Dict[str, List[Dict[str, Any]]],
) -> Dict[str, Any]:
    valid_conditions = [item["condition_text"] for item in conditions[:2]]
    failed_cases = cases.get("failed", [])
    invalid_conditions: List[str] = []
    if failed_cases:
        ret60_values = []
        vol_values = []
        for case in failed_cases:
            ctx = case.get("context", {})
            ret60 = _as_float(ctx.get("ret60"), float("nan"))
            vol20 = _as_float(ctx.get("vol20"), float("nan"))
            if math.isfinite(ret60):
                ret60_values.append(ret60)
            if math.isfinite(vol20):
                vol_values.append(vol20)
        if ret60_values:
            fail_ret60 = float(np.median(ret60_values))
            if fail_ret60 < 0:
                invalid_conditions.append("过去60日仍处弱趋势时，本规则降级为观察或中性")
            else:
                invalid_conditions.append("上涨后段触发但缺少后续确认时，避免直接追高")
        if vol_values:
            invalid_conditions.append("触发时波动/振幅显著放大且随后不能延续，视为失效分支")
    invalid_conditions = invalid_conditions[:2]
    memory_quality = (
        metrics.get("n_events", 0) >= 80
        and metrics.get("n_stocks", 0) >= 20
        and (
            metrics.get("t_stat", 0.0) >= 1.5
            or metrics.get("hit_rate", 0.0) >= 0.52
            or metrics.get("mean_signed_return", 0.0) >= 0.012
        )
    )
    if valid_conditions and memory_quality:
        decision = "branch"
        action = "write_memory" if accepted else "write_memory_candidate_not_executed"
        revision = f"{_humanize_rule(rule_id)}在{domain}中保留原始方向，并新增候选情景分支：{'；'.join(valid_conditions)}。"
    else:
        decision = "skip"
        action = "keep_base_rule"
        revision = f"{_humanize_rule(rule_id)}当前统计表现已由V27规则库吸收，本轮不替换执行规则。"
    return {
        "llm_mode": "offline_schema_reviewer",
        "decision": decision,
        "action": action,
        "confidence": float(min(0.90, max(0.50, metrics.get("hit_rate", 0.5) + abs(metrics.get("t_stat", 0.0)) / 20.0))),
        "valid_conditions": valid_conditions,
        "invalid_conditions": invalid_conditions,
        "revision": revision,
        "rationale": (
            "离线审阅器按照LLM JSON schema生成；真实LLM未运行，因为环境未配置API key。"
            "补丁仅在过滤后表现不低于原规则且命中率不下降时写入记忆。"
        ),
        "patch_eval": {
            "base": metrics,
            "patched": patch_metrics,
            "accepted": bool(accepted),
        },
    }


def _llm_config() -> Dict[str, Any]:
    api_key = os.getenv("AI_ROUTER_API_KEY") or os.getenv("OPENAI_API_KEY") or ""
    raw_url = os.getenv("AI_ROUTER_URL") or ""
    base_url = os.getenv("AI_ROUTER_BASE_URL") or os.getenv("OPENAI_BASE_URL") or ""
    if raw_url:
        endpoint = raw_url.rstrip("/")
    else:
        base_url = (base_url or "https://ai.router.team/v1").rstrip("/")
        endpoint = base_url + "/chat/completions"
    model = os.getenv("KLINE_GPT_MODEL") or os.getenv("AI_ROUTER_MODEL") or "gpt-5.5"
    return {"api_key": api_key, "endpoint": endpoint, "model": model}


def _parse_llm_json(content: str) -> Dict[str, Any]:
    text = str(content or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.S)
        if not match:
            raise
        return json.loads(match.group(0))


def _call_llm_review(payload: Dict[str, Any], timeout: int = 60) -> Dict[str, Any] | None:
    cfg = _llm_config()
    if not cfg["api_key"]:
        return None
    prompt = (
        "你是K线形态规则Harness的Reflect/Evolve审阅器。"
        "请只输出JSON，字段包括decision(add/skip/replace/branch)、confidence、"
        "valid_conditions(最多2条)、invalid_conditions(最多2条)、revision、rationale。"
        "只有当补丁表现不低于原规则时才允许replace或branch。"
    )
    body = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        "temperature": 0.1,
    }
    req = urllib.request.Request(
        cfg["endpoint"],
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": "Bearer " + cfg["api_key"], "Content-Type": "application/json", "User-Agent": "Mozilla/5.0 CodexKlineLLMHarness/1.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        content = data["choices"][0]["message"]["content"]
        parsed = _parse_llm_json(content)
        parsed["llm_mode"] = "openai_compatible"
        parsed["model"] = cfg["model"]
        return parsed
    except (urllib.error.URLError, KeyError, json.JSONDecodeError, TimeoutError):
        return None


def _review_rule(
    domain: str,
    rule_id: str,
    frame: pd.DataFrame,
    conn: sqlite3.Connection,
    series_cache: Dict[str, Any],
    use_llm: bool,
) -> RuleAudit:
    metrics = _evaluate_events(frame)
    total_domain_stocks = max(int(frame["ts_code"].nunique()), 1)
    metrics["coverage_rate"] = float(metrics["n_stocks"] / total_domain_stocks)
    base_score = _metric_score(metrics)
    conditions, patch_metrics, accepted = _candidate_conditions(frame, metrics)
    if not conditions:
        conditions = _descriptive_conditions(frame, metrics)
        if conditions:
            patch_metrics = conditions[0].get("metrics", patch_metrics)
    case_rows = _select_case_rows(frame, max_each=2)
    cases = {
        "passed": _case_payloads(conn, case_rows["passed"], series_cache),
        "failed": _case_payloads(conn, case_rows["failed"], series_cache),
    }
    review_payload = {
        "domain": domain,
        "rule_id": rule_id,
        "rule_name": _humanize_rule(rule_id),
        "family": _rule_family(rule_id),
        "metrics": metrics,
        "candidate_conditions": conditions,
        "patch_metrics": patch_metrics,
        "patch_accepted_by_gate": accepted,
        "case_summary": {
            "passed": [
                {k: case[k] for k in ("ts_code", "stock_name", "date", "signed_return")}
                for case in cases["passed"]
            ],
            "failed": [
                {k: case[k] for k in ("ts_code", "stock_name", "date", "signed_return")}
                for case in cases["failed"]
            ],
        },
    }
    llm_review = _call_llm_review(review_payload) if use_llm else None
    if not llm_review:
        llm_review = _offline_review(domain, rule_id, metrics, conditions, patch_metrics, accepted, cases)
    def _condition_text(value: Any) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            for key in ("condition", "reason", "rule", "text", "description", "summary"):
                if value.get(key):
                    return str(value.get(key))
            return json.dumps(value, ensure_ascii=False, sort_keys=True)
        return str(value)

    llm_review["valid_conditions"] = [
        _condition_text(item) for item in list(llm_review.get("valid_conditions", []))[:2]
    ]
    llm_review["invalid_conditions"] = [
        _condition_text(item) for item in list(llm_review.get("invalid_conditions", []))[:2]
    ]
    if not accepted and llm_review.get("decision") == "replace":
        llm_review["decision"] = "skip"
        llm_review["action"] = "keep_base_rule"
        llm_review["rationale"] = str(llm_review.get("rationale", "")) + "；统计门控未通过，强制不写入替换。"
    elif not accepted and llm_review.get("decision") == "branch":
        llm_review["action"] = "write_memory_candidate_not_executed"
        llm_review["rationale"] = str(llm_review.get("rationale", "")) + "；未过执行门控，仅写入候选情景记忆，不改变V27仓位。"
    return RuleAudit(
        domain=domain,
        rule_id=rule_id,
        metrics=metrics,
        base_score=base_score,
        candidate_conditions=conditions,
        patch_metrics=patch_metrics,
        patch_accepted=accepted,
        llm_review=llm_review,
        cases=cases,
    )


def _write_memory_markdown(audit: RuleAudit, path: Path) -> None:
    review = audit.llm_review
    metrics = audit.metrics
    frontmatter = {
        "domain": audit.domain,
        "rule_id": audit.rule_id,
        "family": _rule_family(audit.rule_id),
        "decision": review.get("decision", "skip"),
        "action": review.get("action", "keep_base_rule"),
        "llm_mode": review.get("llm_mode", "unknown"),
        "confidence": review.get("confidence", 0.0),
        "created_at": _now(),
        "source": "V28 K-line LLM Harness memory",
        "patch_accepted_by_gate": audit.patch_accepted,
    }
    lines = ["---"]
    for key, value in frontmatter.items():
        lines.append(f"{key}: {json.dumps(value, ensure_ascii=False)}")
    lines.extend(["---", "", f"# {audit.domain} / {_humanize_rule(audit.rule_id)}", ""])
    lines.append("## 基础表现")
    lines.append(f"- 触发次数: {metrics['n_events']}")
    lines.append(f"- 覆盖股票数: {metrics['n_stocks']}")
    lines.append(f"- 20D平均方向收益: {_safe_pct(metrics['mean_signed_return'])}")
    lines.append(f"- 命中率: {_safe_pct(metrics['hit_rate'])}")
    lines.append(f"- 波动率: {_safe_pct(metrics['volatility'])}")
    lines.append(f"- 盈亏比: {metrics['payoff']:.3f}")
    lines.append(f"- t值: {metrics['t_stat']:.3f}")
    lines.append("")
    lines.append("## LLM/Reflect 结论")
    lines.append(f"- 决策: {review.get('decision', 'skip')}")
    lines.append(f"- 置信度: {review.get('confidence', 0.0):.2f}")
    lines.append(f"- 修正: {review.get('revision', '')}")
    lines.append(f"- 理由: {review.get('rationale', '')}")
    lines.append("")
    lines.append("## 成立条件")
    valid = review.get("valid_conditions", []) or ["本轮未写入新增成立条件，沿用V27基础规则。"]
    for item in valid[:2]:
        lines.append(f"- {item}")
    lines.append("")
    lines.append("## 失效条件")
    invalid = review.get("invalid_conditions", []) or ["暂无稳定失效分支，后续成熟案例继续观察。"]
    for item in invalid[:2]:
        lines.append(f"- {item}")
    lines.append("")
    lines.append("## 候选过滤后表现")
    patched = audit.patch_metrics
    lines.append(f"- 触发次数: {patched.get('n_events', 0)}")
    lines.append(f"- 20D平均方向收益: {_safe_pct(_as_float(patched.get('mean_signed_return')))}")
    lines.append(f"- 命中率: {_safe_pct(_as_float(patched.get('hit_rate')))}")
    lines.append(f"- t值: {_as_float(patched.get('t_stat')):.3f}")
    lines.append("")
    lines.append("## 案例窗口")
    for kind in ("passed", "failed"):
        lines.append(f"### {'通过案例' if kind == 'passed' else '失败案例'}")
        for case in audit.cases.get(kind, [])[:2]:
            w = case.get("window_60d", {})
            lines.append(
                f"- {case['ts_code']} {case.get('stock_name','')} {case['date']} "
                f"signed={_safe_pct(case['signed_return'])} "
                f"post20={_safe_pct(_as_float(w.get('post20_return')))} "
                f"post60={_safe_pct(_as_float(w.get('post60_return')))}"
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_framework_files(output_dir: Path, llm_mode: str) -> None:
    framework_dir = output_dir / "00_framework"
    framework_dir.mkdir(parents=True, exist_ok=True)
    (framework_dir / "V28框架说明.md").write_text(
        "\n".join(
            [
                "# V28 K线形态库 + 分域记忆检索 + LLM Harness进化",
                "",
                "1. K线形态库：传统蜡烛图、量价、均线趋势、突破/跌破、支撑阻力、多周期确认，Wyckoff仅作为价量结构增强。",
                "2. 分域：当前继承V27风格×市值域；数据库已存在行业、风格、市值字段，支持后续point-in-time域刷新。",
                "3. 记忆检索：每条规则检索同域全股票触发样本，并截取通过/失败案例的前后60个交易日窗口。",
                "4. 表现评估：统一计算触发次数、覆盖股票数、20D平均方向收益、命中率、波动率、盈亏比、t值。",
                "5. 趋势学习：候选成立条件最多2条；失败条件最多2条；执行替换必须通过统计门控，未过门控只入库为候选情景。",
                "6. 进化修正：输出add/skip/replace/branch；replace必须过门控，branch可作为候选记忆但默认不改仓位。",
                "7. 个股择时：V27仓位曲线保持不变，V28新增可检索情景记忆与当下解释，不默认改仓位。",
                "",
                f"LLM运行模式：{llm_mode}",
            ]
        ),
        encoding="utf-8",
    )
    (framework_dir / "K线形态库_图文框架.md").write_text(
        "\n".join(
            [
                "# K线形态库图文框架",
                "",
                "```text",
                "单根蜡烛:  开/收实体 + 上影线 + 下影线 -> 多空试探与承接",
                "两根组合:  吞没/孕线/刺透/乌云 -> 力量切换或失败确认",
                "三根组合:  早晨星/黄昏星/三白兵/三乌鸦 -> 反转或延续",
                "量价结构:  放量突破/缩量回踩/量价背离 -> 真伪确认",
                "趋势结构:  MA排列/金叉死叉/回踩均线 -> 背景过滤",
                "支撑阻力:  箱体/通道/缺口/高低点 -> 关键位置判断",
                "Wyckoff增强: Spring/Upthrust/SOS/SOW -> 供需语境解释",
                "```",
                "",
                "这些形态先作为候选经验，不直接交易；必须进入同域全股票表现评估和Harness记忆进化。",
            ]
        ),
        encoding="utf-8",
    )


def _copy_v27_individual_outputs(v27_dir: Path, output_dir: Path, codes: Sequence[str]) -> List[Dict[str, Any]]:
    individual_dir = output_dir / "03_individual"
    individual_dir.mkdir(parents=True, exist_ok=True)
    copied: List[Dict[str, Any]] = []
    for code in codes:
        matches = sorted(v27_dir.glob(f"*_{code}_*.png"))
        json_matches = sorted(v27_dir.glob(f"*_{code}_*.json"))
        notes_matches = sorted(v27_dir.glob(f"*_{code}_*_memory_notes.txt"))
        entry: Dict[str, Any] = {"code": code}
        for src in matches + json_matches + notes_matches:
            dst = individual_dir / src.name
            shutil.copy2(src, dst)
            if src.suffix.lower() == ".png":
                entry["chart"] = str(dst)
            elif src.suffix.lower() == ".json":
                entry["json"] = str(dst)
            else:
                entry["notes"] = str(dst)
        copied.append(entry)
    return copied


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run V28 K-line LLM Harness memory evolver.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--events-cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--v27-dir", type=Path, default=DEFAULT_V27_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--codes", nargs="*", default=list(DEFAULT_CODES))
    parser.add_argument("--max-rules-per-domain", type=int, default=0, help="0 means all V27 rules.")
    parser.add_argument("--use-llm", action="store_true", help="Call OpenAI-compatible LLM when API key is present.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_dir = Path(args.output_dir)
    for sub in ["00_framework", "01_domain_memory", "02_cases", "03_individual", "04_reports"]:
        (output_dir / sub).mkdir(parents=True, exist_ok=True)
    llm_cfg = _llm_config()
    llm_mode = "openai_compatible" if args.use_llm and llm_cfg["api_key"] else "offline_schema_reviewer"
    _write_framework_files(output_dir, llm_mode)

    events = _load_full_events(Path(args.events_cache))
    rulebooks = _load_v27_rulebooks(Path(args.v27_dir))
    audits: List[RuleAudit] = []
    rows: List[Dict[str, Any]] = []
    series_cache: Dict[str, Any] = {}
    with sqlite3.connect(str(args.db)) as conn:
        for domain, rulebook in rulebooks.items():
            domain_events = events.loc[events[DOMAIN_COL].astype(str).eq(domain)].copy()
            if domain_events.empty:
                continue
            rules = list(rulebook["rule_id"].astype(str))
            if args.max_rules_per_domain and args.max_rules_per_domain > 0:
                rules = rules[: int(args.max_rules_per_domain)]
            for rule_id in rules:
                frame = domain_events.loc[domain_events["rule_id"].astype(str).eq(rule_id)].copy()
                if frame.empty:
                    continue
                audit = _review_rule(domain, rule_id, frame, conn, series_cache, args.use_llm)
                audits.append(audit)
                memory_path = output_dir / "01_domain_memory" / _safe_name(domain) / f"{_safe_name(rule_id)}.md"
                _write_memory_markdown(audit, memory_path)
                case_path = output_dir / "02_cases" / _safe_name(domain) / f"{_safe_name(rule_id)}_cases.json"
                case_path.parent.mkdir(parents=True, exist_ok=True)
                case_path.write_text(json.dumps(audit.cases, ensure_ascii=False, indent=2), encoding="utf-8")
                rows.append(
                    {
                        "domain": domain,
                        "rule_id": rule_id,
                        "family": _rule_family(rule_id),
                        "decision": audit.llm_review.get("decision", "skip"),
                        "llm_mode": audit.llm_review.get("llm_mode", llm_mode),
                        "patch_accepted": audit.patch_accepted,
                        "n_events": audit.metrics["n_events"],
                        "n_stocks": audit.metrics["n_stocks"],
                        "mean_signed_return_20d": audit.metrics["mean_signed_return"],
                        "hit_rate": audit.metrics["hit_rate"],
                        "volatility": audit.metrics["volatility"],
                        "payoff": audit.metrics["payoff"],
                        "t_stat": audit.metrics["t_stat"],
                        "valid_conditions": "；".join(audit.llm_review.get("valid_conditions", [])[:2]),
                        "invalid_conditions": "；".join(audit.llm_review.get("invalid_conditions", [])[:2]),
                        "memory_file": str(memory_path),
                        "cases_file": str(case_path),
                    }
                )

    individual = _copy_v27_individual_outputs(Path(args.v27_dir), output_dir, [str(c) for c in args.codes])
    report_dir = output_dir / "04_reports"
    index = pd.DataFrame(rows)
    index.to_csv(report_dir / "V28_domain_rule_memory_index.csv", index=False, encoding="utf-8-sig")
    summary = (
        index.groupby(["domain", "decision"], dropna=False).size().reset_index(name="count")
        if not index.empty
        else pd.DataFrame(columns=["domain", "decision", "count"])
    )
    summary.to_csv(report_dir / "V28_domain_decision_summary.csv", index=False, encoding="utf-8-sig")
    manifest = {
        "version": "V28",
        "created_at": _now(),
        "llm_mode": llm_mode,
        "llm_api_configured": bool(llm_cfg["api_key"]),
        "strategy_backbone": "V27 unchanged; V28 memories are explanatory and gated, not applied to positions by default.",
        "events_cache": str(args.events_cache),
        "v27_dir": str(args.v27_dir),
        "output_dir": str(output_dir),
        "rules_reviewed": int(len(rows)),
        "patches_accepted_by_gate": int(sum(1 for item in rows if item["patch_accepted"])),
        "individual_outputs": individual,
        "domain_policy": {
            "current": "inherits V27 static domain labels in expanded event cache",
            "point_in_time_ready_tables": ["sw_l1_industry_daily", "style_score_daily", "stock_valuation_daily"],
            "next_refresh": "rebuild event cache with date-level domain_style_size12 when dynamic quarterly/monthly domains are enabled",
        },
    }
    (report_dir / "V28_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
