"""Dashboard backend for the LLM K-line learning research panel.

The UI layer should be lightweight, so this module turns the existing durable
K-line research outputs into small JSON payloads:

1. style-size domain rule memories learned from the full event cache
2. Harness-style LLM/reflection metadata from V28 outputs
3. on-demand single-stock timing curves from the already validated V27 executor

No API key is stored here.  LLM credentials are read only from process
environment by the training scripts.
"""

from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

AGENT_ROOT = Path(__file__).resolve().parents[2]
if str(AGENT_ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(AGENT_ROOT))

from model.kline_memory_learning.run_wyckoff_memory_batch import (  # noqa: E402
    DEFAULT_COST_RATE,
    DEFAULT_DB,
    _annual_stats,
    _load_stock,
    _metrics,
    _position_label,
    _safe_name,
)
from model.kline_memory_learning.run_wyckoff_style_size_domain_rulebook_v26_event_memory_evolver import (  # noqa: E402
    DEFAULT_CACHE,
    _learn_rulebook,
    _latest_domain_map,
    _load_events,
)
from model.kline_memory_learning.run_wyckoff_style_size_domain_rulebook_v27_shared_teacher_lowfreq import (  # noqa: E402
    DEFAULT_OUTPUT_DIR as DEFAULT_V27_OUTPUT_DIR,
    _domain_members,
    _event_pressure_for_tape,
    _profile_variants,
    _raw_target,
    _replay,
    _select_domain_profile,
)
from model.kline_memory_learning.run_wyckoff_style_size_domain_rulebook_v25_domain_teacher_evolver_lowfreq import (  # noqa: E402
    _make_tape,
)
from model.kline_memory_learning.run_wyckoff_style_size_memory_batch import DOMAIN_COL, STYLE_SIZE_ORDER  # noqa: E402
from model.kline_memory_learning.run_kline_llm_harness_v28 import (  # noqa: E402
    DEFAULT_OUTPUT_DIR as DEFAULT_V28_OUTPUT_DIR,
    _candidate_conditions,
    _condition_text,
    _evaluate_events,
    _humanize_rule,
    _metric_score,
    _rule_family,
    _safe_pct,
)


BOARD_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = AGENT_ROOT.parent
DATA_DIR = BOARD_DIR / "data"
SNAPSHOT_PATH = DATA_DIR / "kline_llm_learning_snapshot.json"
STOCK_CACHE_DIR = DATA_DIR / "kline_llm_stock_cache"


def _resolve_runtime_path(env_name: str, default: Path | str) -> Path:
    raw = os.environ.get(env_name)
    path = Path(raw) if raw else Path(default)
    if path.is_absolute():
        return path.resolve()
    parts = path.parts
    if parts and parts[0].lower() == "agent":
        return (PROJECT_ROOT / path).resolve()
    return (AGENT_ROOT / path).resolve()


V28_DIR = _resolve_runtime_path("KLINE_LLM_V28_DIR", DEFAULT_V28_OUTPUT_DIR)
V27_DIR = _resolve_runtime_path("KLINE_LLM_V27_DIR", DEFAULT_V27_OUTPUT_DIR)
EVENTS_CACHE = _resolve_runtime_path("KLINE_LLM_EVENTS_CACHE", DEFAULT_CACHE)
EVENTS_CSV = _resolve_runtime_path(
    "KLINE_LLM_EVENTS_CSV",
    EVENTS_CACHE.with_name(EVENTS_CACHE.stem + ".runtime.csv.gz"),
)
DB_PATH = _resolve_runtime_path("KLINE_LLM_DB", DEFAULT_DB)


PATTERN_LIBRARY = [
    {
        "category": "蜡烛反转",
        "logic": "单根/两根K线识别低位反包、长阳、长影与高位衰竭。",
        "patterns": ["低位阳包阴", "低位大阳线", "中位阳包阴", "高位阴包阳", "长上影衰竭"],
    },
    {
        "category": "跳空与缺口",
        "logic": "识别向上跳空延续、跳空回补、低位跳空反转。",
        "patterns": ["向上跳空延续", "向下跳空反转", "缺口不回补", "跳空后缩量确认"],
    },
    {
        "category": "均线趋势",
        "logic": "用多周期均线排列、金叉死叉和均线支撑刻画趋势状态。",
        "patterns": ["MA5/20金叉", "MA20/120金叉", "MA20支撑", "MA60支撑", "多头排列"],
    },
    {
        "category": "突破/跌破",
        "logic": "用N日高低点突破、假突破、跌破后收回衡量支撑压力变化。",
        "patterns": ["10日放量突破", "20日突破", "60日突破", "低位假跌破", "下沿收回"],
    },
    {
        "category": "量价结构",
        "logic": "结合成交量、成交额、振幅和收盘位置确认价量同步或背离。",
        "patterns": ["量价齐升", "缩量突破", "缩量跌破", "放量滞涨", "低位缩量企稳"],
    },
    {
        "category": "动量趋势",
        "logic": "用20/40/120日收益斜率刻画主升、修复和趋势加速。",
        "patterns": ["20日动量上行", "40日动量上行", "120日动量上行", "趋势加速"],
    },
    {
        "category": "恐慌/衰竭",
        "logic": "识别急跌后的恐慌释放和上涨末端的动能衰竭。",
        "patterns": ["20日恐慌下跌", "40日恐慌下跌", "上涨衰竭", "高位放量回落"],
    },
    {
        "category": "Wyckoff价量结构",
        "logic": "作为价量结构增强，用吸筹、弹簧、突破确认解释规则是否成立。",
        "patterns": ["Spring", "SOS", "LPS", "UTAD", "吸筹/派发确认"],
    },
]


def _json_default(value: Any) -> Any:
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        out = float(value)
        return out if math.isfinite(out) else None
    if isinstance(value, (pd.Timestamp,)):
        return value.strftime("%Y-%m-%d")
    return str(value)


def _json_clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_clean(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        out = float(value)
        return out if math.isfinite(out) else None
    if isinstance(value, (pd.Timestamp,)):
        return value.strftime("%Y-%m-%d")
    if value is pd.NA:
        return None
    return value


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
        return out if math.isfinite(out) else default
    except Exception:
        return default


def _pct(value: Any, digits: int = 2) -> str:
    val = _finite(value, float("nan"))
    if not math.isfinite(val):
        return "NA"
    return f"{val * 100:.{digits}f}%"


def _date_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    digits = re.sub(r"\D", "", text)
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    if len(text) >= 10 and text[4:5] == "-":
        return text[:10]
    return text


def _normalize_code(raw: str) -> str:
    text = str(raw or "").strip().upper()
    if not text:
        return ""
    match = re.search(r"(\d{6})(?:\.(SH|SZ|BJ))?", text)
    if not match:
        return text
    code = match.group(1)
    suffix = match.group(2)
    if not suffix:
        suffix = "SH" if code.startswith(("5", "6", "9")) else "SZ"
    return f"{code}.{suffix}"


@lru_cache(maxsize=1)
def _name_map() -> dict[str, str]:
    path = DATA_DIR / "all_a_stocks.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out: dict[str, str] = {}
    for row in payload.get("rows", []):
        display = str(row.get("display_code") or "").upper()
        code = _normalize_code(display or row.get("code") or "")
        name = str(row.get("name") or "").strip()
        if code and name:
            out[code] = name
    return out


def _stock_name(code: str) -> str:
    return _name_map().get(str(code).upper(), "")


@lru_cache(maxsize=1)
def _events_full() -> pd.DataFrame:
    if EVENTS_CSV.exists():
        events = pd.read_csv(EVENTS_CSV, compression="infer")
    else:
        events = pd.read_pickle(EVENTS_CACHE)
    required = ["ts_code", "date", DOMAIN_COL, "rule_id", "direction", "signed_return"]
    missing = [col for col in required if col not in events.columns]
    if missing:
        raise RuntimeError(f"K线事件缓存缺少字段: {missing}")
    keep = required + [
        col
        for col in [
            "forward_return",
            "strength",
            "ret20",
            "ret60",
            "vol20",
            "range20",
            "volume_ratio",
            "range_ratio",
            "amount_ratio",
            "close_position",
        ]
        if col in events.columns
    ]
    local = events.loc[:, keep].copy()
    local = local.dropna(subset=["ts_code", "date", DOMAIN_COL, "rule_id", "direction", "signed_return"])
    local["ts_code"] = local["ts_code"].astype(str)
    local[DOMAIN_COL] = local[DOMAIN_COL].astype(str)
    local["rule_id"] = local["rule_id"].astype(str)
    local["date"] = local["date"].astype(str).str.slice(0, 10)
    date_digits = local["date"].str.replace("-", "", regex=False).str.slice(0, 8)
    local["date_key"] = pd.to_numeric(date_digits, errors="coerce")
    for col in ["direction", "signed_return", "forward_return", "strength", "ret20", "ret60", "vol20", "range20", "volume_ratio", "range_ratio", "amount_ratio", "close_position"]:
        if col in local.columns:
            local[col] = pd.to_numeric(local[col], errors="coerce")
    local = local[local[DOMAIN_COL].isin(STYLE_SIZE_ORDER)]
    return local.dropna(subset=["signed_return"]).reset_index(drop=True)


@lru_cache(maxsize=1)
def _events_light() -> pd.DataFrame:
    return _events_full()


def _family_logic(rule_id: str) -> str:
    family = _rule_family(rule_id)
    if family == "蜡烛图/跳空":
        return "把K线实体、影线、缺口和收盘位置转成布尔触发；低位/高位由60日分位与20日回撤限定。"
    if family == "均线趋势":
        return "计算MA5/10/20/60/120/250排列、交叉和支撑，触发后看20日方向收益。"
    if family == "突破/跌破/假突破":
        return "比较收盘价与N日高低点、突破日量能和次日回收情况，区分真突破与假突破。"
    if family == "量价结构":
        return "同步检查收益、成交量/成交额比、振幅比和收盘位置，过滤无量上攻或放量回落。"
    if family == "动量趋势":
        return "用20/40/120日收益与均线方向描述趋势强弱，并按域内历史胜率收缩权重。"
    if family == "恐慌/衰竭":
        return "识别快速下跌后的超跌修复和上涨末端的衰竭反转，结合波动率与成交确认。"
    return "把图形文字规则解析成可复算事件，再用域内全股票历史样本评估边际有效性。"


def _pattern_summary(rule_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for row in rule_rows:
        counts[row["family"]] = counts.get(row["family"], 0) + 1
    display_map = {
        "蜡烛反转": ["蜡烛反转", "蜡烛图/跳空"],
        "跳空与缺口": ["跳空与缺口", "蜡烛图/跳空"],
        "均线趋势": ["均线趋势"],
        "突破/跌破": ["突破/跌破", "突破/跌破/假突破"],
        "量价结构": ["量价结构"],
        "动量趋势": ["动量趋势"],
        "恐慌/衰竭": ["恐慌/衰竭"],
        "Wyckoff价量结构": ["Wyckoff价量结构", "量价结构", "突破/跌破/假突破"],
    }
    rows = []
    for item in PATTERN_LIBRARY:
        families = display_map.get(item["category"], [item["category"]])
        count = sum(counts.get(fam, 0) for fam in families)
        rows.append({**item, "count": int(count), "examples": "、".join(item["patterns"][:5])})
    return rows


def _rule_metrics_table(events: pd.DataFrame) -> pd.DataFrame:
    local = events.copy()
    local["signed_clip"] = pd.to_numeric(local["signed_return"], errors="coerce").clip(-0.35, 0.35)
    local["hit"] = local["signed_clip"] > 0
    local["pos_return"] = local["signed_clip"].where(local["signed_clip"] > 0)
    local["neg_return"] = local["signed_clip"].where(local["signed_clip"] < 0)
    grouped = local.groupby([DOMAIN_COL, "rule_id"], observed=True)
    table = grouped.agg(
        n_events=("signed_clip", "size"),
        n_stocks=("ts_code", "nunique"),
        mean_signed_return_20d=("signed_clip", "mean"),
        hit_rate=("hit", "mean"),
        volatility=("signed_clip", "std"),
        pos_mean=("pos_return", "mean"),
        neg_mean=("neg_return", "mean"),
        direction=("direction", "median"),
    ).reset_index()
    domain_stocks = local.groupby(DOMAIN_COL)["ts_code"].nunique().to_dict()
    table["coverage_rate"] = table.apply(lambda r: _finite(r["n_stocks"]) / max(_finite(domain_stocks.get(r[DOMAIN_COL]), 1.0), 1.0), axis=1)
    table["payoff"] = table.apply(lambda r: (_finite(r["pos_mean"]) / max(abs(_finite(r["neg_mean"])), 1e-6)) if pd.notna(r["pos_mean"]) and pd.notna(r["neg_mean"]) else 0.0, axis=1)
    n_eff = np.maximum(1.0, np.minimum(table["n_events"].astype(float), table["n_stocks"].astype(float).clip(lower=1.0) * 12.0))
    table["t_stat"] = table["mean_signed_return_20d"].fillna(0.0) / table["volatility"].fillna(0.0).clip(lower=1e-6) * np.sqrt(n_eff)
    raw_score = (
        table["mean_signed_return_20d"].fillna(0.0) * 4.0
        + (table["hit_rate"].fillna(0.5) - 0.5) * 0.8
        + table["t_stat"].fillna(0.0).clip(upper=8.0) * 0.035
        + np.tanh((table["payoff"].fillna(0.0) - 1.0) * 0.8) * 0.08
    )
    breadth = np.minimum(1.0, table["n_events"].astype(float) / 120.0) * np.minimum(1.0, table["n_stocks"].astype(float) / 30.0)
    sparse_penalty = np.where((table["n_events"] < 40) | (table["n_stocks"] < 8), 0.20, 0.0)
    table["score"] = raw_score * (0.35 + 0.65 * breadth) - sparse_penalty
    table["quality_gate"] = np.where((table["n_events"] >= 40) & (table["n_stocks"] >= 8), "通过", "低覆盖")
    table["family"] = table["rule_id"].map(_rule_family)
    table["rule_name"] = table["rule_id"].map(_humanize_rule)
    table["quant_logic"] = table["rule_id"].map(_family_logic)
    table = table.rename(columns={DOMAIN_COL: "domain"})
    return table.sort_values(["domain", "score"], ascending=[True, False]).reset_index(drop=True)


def _conversion_rows(rule_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best: dict[str, dict[str, Any]] = {}
    for row in rule_rows:
        family = row.get("family") or "复合形态"
        if family not in best or _finite(row.get("score")) > _finite(best[family].get("score")):
            best[family] = row
    out = []
    for family, row in sorted(best.items(), key=lambda kv: _finite(kv[1].get("score")), reverse=True):
        out.append(
            {
                "形态分类": family,
                "原始形态": row.get("rule_name"),
                "量化触发": row.get("quant_logic"),
                "检验窗口": "触发日前后60D案例；核心评价为触发后20D方向收益",
                "当前域内表现": f"均值{_pct(row.get('mean_signed_return_20d'))} / 命中{_pct(row.get('hit_rate'))} / t={_finite(row.get('t_stat')):.2f}",
            }
        )
    return out


def _manifest() -> dict[str, Any]:
    path = V28_DIR / "04_reports" / "V28_manifest.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    manifest["domain_policy"] = {
        "current": "执行层使用事件缓存中的逐事件 domain_style_size12；该字段来自历史风格与市值标签合成的12域，不做单股单独拟合。",
        "primary_axis": "风格4类 × 市值3类 = 12域",
        "available_axes": [
            "domain_industry",
            "domain_size3",
            "domain_style4",
            "domain_style_size12",
            "domain_industry_size",
            "domain_industry_style",
            "domain_board",
            "domain_liquidity3",
            "domain_behavior_ds",
        ],
        "label_source": "agent/output/kline_memory_learning/domain_memory_audit/style_box_quarterly_labels.pkl 与 expanded_pattern_events_v9_compact.pkl",
    }
    return manifest


def _memory_review_map() -> dict[tuple[str, str], dict[str, Any]]:
    path = V28_DIR / "04_reports" / "V28_domain_rule_memory_index.csv"
    if not path.exists():
        return {}
    try:
        frame = pd.read_csv(path)
    except Exception:
        return {}
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for row in frame.to_dict("records"):
        out[(str(row.get("domain")), str(row.get("rule_id")))] = row
    return out


def _rule_rows() -> pd.DataFrame:
    snapshot = _rule_metrics_table(_events_full())
    reviews = _memory_review_map()
    if reviews:
        snapshot["decision"] = snapshot.apply(lambda r: str(reviews.get((r["domain"], r["rule_id"]), {}).get("decision", "")), axis=1)
        snapshot["valid_conditions"] = snapshot.apply(lambda r: str(reviews.get((r["domain"], r["rule_id"]), {}).get("valid_conditions", "")), axis=1)
        snapshot["invalid_conditions"] = snapshot.apply(lambda r: str(reviews.get((r["domain"], r["rule_id"]), {}).get("invalid_conditions", "")), axis=1)
    else:
        snapshot["decision"] = ""
        snapshot["valid_conditions"] = ""
        snapshot["invalid_conditions"] = ""
    return snapshot


def build_dashboard_snapshot(refresh: bool = False) -> dict[str, Any]:
    if SNAPSHOT_PATH.exists() and not refresh:
        age = time.time() - SNAPSHOT_PATH.stat().st_mtime
        if age < 3600:
            return _json_clean(json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8")))

    table = _rule_rows()
    rules = table.groupby("domain", group_keys=False).head(40).copy()
    rule_rows = rules.to_dict("records")
    domain_rows = []
    for domain, group in table.groupby("domain", sort=False):
        domain_rows.append(
            {
                "domain": domain,
                "rule_count": int(group["rule_id"].nunique()),
                "event_count": int(group["n_events"].sum()),
                "stock_count": int(group["n_stocks"].max()),
                "best_rule": str(group.iloc[0]["rule_name"]),
                "best_score": float(group.iloc[0]["score"]),
            }
        )
    domains = [d for d in STYLE_SIZE_ORDER if d in set(table["domain"])]
    default_domain = "大盘均衡" if "大盘均衡" in domains else (domains[0] if domains else "")
    default_rule = ""
    if default_domain:
        domain_table = table.loc[table["domain"].eq(default_domain)].copy()
        robust = domain_table.loc[(domain_table["n_stocks"] >= 30) & (domain_table["n_events"] >= 120)]
        if robust.empty:
            robust = domain_table.loc[(domain_table["n_stocks"] >= 8) & (domain_table["n_events"] >= 40)]
        if robust.empty:
            robust = domain_table
        default_rule = str(robust.iloc[0]["rule_id"])
    payload = {
        "status": "ok",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "as_of": _latest_trade_date(),
        "domain_axis": "风格×市值12域",
        "domain_logic": "每个交易日按风格成长/均衡/价值/红利与市值大/中/小形成12个域；域内全股票历史K线事件共同学习同一套规则。",
        "llm_manifest": _manifest(),
        "flow": [
            "K线形态库",
            "风格×市值分域",
            "记忆检索",
            "表现评估",
            "趋势学习",
            "进化修正",
            "个股择时",
        ],
        "pattern_library": _pattern_summary(rule_rows),
        "conversion_rows": _conversion_rows(rule_rows),
        "domain_rows": domain_rows,
        "domains": domains,
        "rules": rule_rows,
        "default_domain": default_domain,
        "default_rule": default_rule,
        "stock_universe": _stock_universe(limit=7000),
        "research_boundary": "执行层使用同一域共享规则和执行器；单个股票只检索并执行所属域记忆，不单独学习参数。LLM进化记忆通过统计门控，不通过则只入候选库，不替换执行规则。",
    }
    payload = _json_clean(payload)
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")
    return payload


def _stock_universe(limit: int = 7000) -> list[dict[str, Any]]:
    rows = []
    for code, name in sorted(_name_map().items()):
        rows.append({"code": code, "name": name, "label": f"{code} {name}"})
        if len(rows) >= limit:
            break
    return rows


def _latest_trade_date() -> str:
    try:
        with sqlite3.connect(str(DB_PATH)) as conn:
            return str(conn.execute("select max(trade_date) from stock_ohlcv_daily").fetchone()[0])
    except Exception:
        return ""


def rule_context(domain: str, rule_id: str, stock: str = "") -> dict[str, Any]:
    domain = str(domain or "").strip()
    rule_id = str(rule_id or "").strip()
    stock = _normalize_code(stock)
    events = _events_full()
    if not domain:
        domain = "大盘均衡" if "大盘均衡" in set(events[DOMAIN_COL]) else str(events[DOMAIN_COL].iloc[0])
    if not rule_id:
        rows = _rule_rows()
        rule_id = str(rows.loc[rows["domain"].eq(domain)].iloc[0]["rule_id"])
    frame = events.loc[events[DOMAIN_COL].eq(domain) & events["rule_id"].eq(rule_id)].copy()
    if frame.empty:
        return {"status": "failed", "message": "domain_rule_not_found", "domain": domain, "rule_id": rule_id}
    stock_rows = (
        frame.groupby("ts_code")
        .agg(
            触发次数=("signed_return", "size"),
            平均20D方向收益=("signed_return", "mean"),
            命中率=("signed_return", lambda x: float((pd.to_numeric(x, errors="coerce") > 0).mean())),
            最近触发日=("date", "max"),
        )
        .reset_index()
        .rename(columns={"ts_code": "代码"})
    )
    stock_rows["名称"] = stock_rows["代码"].map(_stock_name)
    stock_rows["最近触发日"] = stock_rows["最近触发日"].map(_date_text)
    stock_rows = stock_rows.sort_values(["触发次数", "平均20D方向收益"], ascending=[False, False]).head(160)
    if not stock and not stock_rows.empty:
        stock = str(stock_rows.iloc[0]["代码"])

    metrics_rows = _rule_rows()
    metrics_rows = metrics_rows.loc[metrics_rows["domain"].eq(domain)].sort_values("score", ascending=False)
    metrics_rows = metrics_rows[
        [
            "rule_id",
            "rule_name",
            "family",
            "n_events",
            "n_stocks",
            "coverage_rate",
            "mean_signed_return_20d",
            "hit_rate",
            "volatility",
            "payoff",
            "t_stat",
            "score",
            "quality_gate",
            "decision",
            "valid_conditions",
            "invalid_conditions",
        ]
    ].rename(
        columns={
            "rule_id": "规则ID",
            "rule_name": "规则名称",
            "family": "形态分类",
            "n_events": "触发次数",
            "n_stocks": "覆盖股票数",
            "coverage_rate": "覆盖率",
            "mean_signed_return_20d": "20D平均方向收益",
            "hit_rate": "命中率",
            "volatility": "波动率",
            "payoff": "盈亏比",
            "t_stat": "t值",
            "score": "综合得分",
            "quality_gate": "样本门控",
            "decision": "进化决策",
            "valid_conditions": "触发依据",
            "invalid_conditions": "失效条件",
        }
    )

    base_metrics = _evaluate_events(frame)
    conditions, patch_metrics, accepted = _candidate_conditions(frame, base_metrics)
    if not conditions:
        conditions = []
    review = _memory_review_map().get((domain, rule_id), {})
    valid = str(review.get("valid_conditions") or "").strip()
    invalid = str(review.get("invalid_conditions") or "").strip()
    evolution = {
        "original_description": f"{_rule_family(rule_id)}：{_humanize_rule(rule_id)}，源于蜡烛图/价量/均线文字形态库。",
        "quant_logic": _family_logic(rule_id),
        "trigger_basis": valid or "；".join([_condition_text(c) for c in conditions[:2]]) or "按域内样本的收益、量能、波动和收盘位置自动归纳成立条件。",
        "failure_conditions": invalid or "过滤后未提升的情境会被保留为失败样本；本轮最多写入2条失效条件。",
        "gate": "通过" if accepted else "未通过执行替换门控",
        "base_metrics": base_metrics,
        "patched_metrics": patch_metrics,
    }
    passed = frame.sort_values("signed_return", ascending=False).head(4)
    failed = frame.sort_values("signed_return", ascending=True).head(4)
    cases = []
    for label, rows in [("通过案例", passed), ("失败案例", failed)]:
        for row in rows.to_dict("records"):
            cases.append(
                {
                    "类型": label,
                    "代码": row.get("ts_code"),
                    "名称": _stock_name(str(row.get("ts_code"))),
                    "触发日": _date_text(row.get("date")),
                    "20D方向收益": row.get("signed_return"),
                }
            )
    stock_records = stock_rows.to_dict("records")
    for item in stock_records:
        if "code" not in item and "代码" in item:
            item["code"] = item.get("代码")
        if "name" not in item and "名称" in item:
            item["name"] = item.get("名称")
    return _json_clean({
        "status": "ok",
        "domain": domain,
        "rule_id": rule_id,
        "stock": stock,
        "rule_name": _humanize_rule(rule_id),
        "family": _rule_family(rule_id),
        "stocks": stock_records,
        "stock_list": stock_records,
        "metrics_rows": metrics_rows.head(120).to_dict("records"),
        "evolution": evolution,
        "cases": cases,
    })


@lru_cache(maxsize=1)
def _v27_summary() -> dict[str, Any]:
    path = V27_DIR / "V27_five_stock_summary.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"profiles": {}}


def _load_v27_rulebook(domain: str, events: pd.DataFrame) -> pd.DataFrame:
    path = V27_DIR / f"V27_rulebook_{_safe_name(domain)}.csv"
    if path.exists():
        return pd.read_csv(path)
    return _learn_rulebook(events.loc[events[DOMAIN_COL].eq(domain)].copy(), max_rules=120)


def _load_v27_profile(domain: str, events: pd.DataFrame, rulebook: pd.DataFrame, cost_rate: float) -> dict[str, Any]:
    profiles = _v27_summary().get("profiles") or {}
    if domain in profiles and profiles[domain].get("params"):
        return profiles[domain]
    # Deterministic fallback: evaluate a bounded representative subset when a
    # domain profile was not already materialized.  The rulebook is still learned
    # from the full domain event pool; this bound only limits expensive replay
    # selection for interactive latency.
    max_members = int(os.environ.get("KLINE_LLM_PROFILE_MAX_MEMBERS", "260"))
    members = _domain_members(events, domain)
    if max_members > 0:
        members = members[:max_members]
    tapes = []
    pressures: dict[str, np.ndarray] = {}
    as_of = _latest_trade_date()
    with sqlite3.connect(str(DB_PATH)) as conn:
        for code in members:
            try:
                tape = _make_tape(_load_stock(conn, code, as_of))
            except Exception:
                tape = None
            if tape is None:
                continue
            stock_events = events.loc[events["ts_code"].eq(code) & events[DOMAIN_COL].eq(domain)]
            pressures[tape.code] = _event_pressure_for_tape(tape, stock_events, rulebook)
            tapes.append(tape)
    if not tapes:
        # Last-resort broad default, still not stock-fitted.
        all_pressure = np.asarray([0.0, 1.0])
        scale = float(np.nanpercentile(np.abs(all_pressure), 85))
        return {"params": next(iter(_profile_variants(domain, max(scale, 0.1)))), "stocks": 0}
    return _select_domain_profile(domain, tapes, pressures, cost_rate)


def stock_timing_payload(code: str, rule_id: str = "", cost_rate: float = DEFAULT_COST_RATE) -> dict[str, Any]:
    code = _normalize_code(code)
    if not code:
        return {"status": "failed", "message": "empty_code"}
    STOCK_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_key = _safe_name(f"{code}_{rule_id or 'auto'}")
    cache_path = STOCK_CACHE_DIR / f"{cache_key}.json"
    if cache_path.exists() and time.time() - cache_path.stat().st_mtime < 1800:
        cached = _json_clean(json.loads(cache_path.read_text(encoding="utf-8")))
        if cached.get("date_format") == "iso":
            return cached

    events = _events_light()
    domain_map = _latest_domain_map(events)
    domain = str(domain_map.get(code, ""))
    if not domain:
        return {"status": "failed", "message": "stock_domain_not_found", "code": code}
    rulebook = _load_v27_rulebook(domain, events)
    profile = _load_v27_profile(domain, events, rulebook, float(cost_rate))
    as_of = _latest_trade_date()
    with sqlite3.connect(str(DB_PATH)) as conn:
        tape = _make_tape(_load_stock(conn, code, as_of))
    if tape is None:
        return {"status": "failed", "message": "insufficient_stock_history", "code": code}
    stock_events = events.loc[events["ts_code"].eq(code) & events[DOMAIN_COL].eq(domain)]
    pressure = _event_pressure_for_tape(tape, stock_events, rulebook)
    replay = _replay(tape, pressure, profile["params"], float(cost_rate))
    metrics = _metrics(replay["strategy_nav"], tape.price_nav, replay["positions"])
    annual = _annual_stats(tape.dates, replay["strategy_nav"], tape.price_nav)
    relative = replay["strategy_nav"] / np.maximum(tape.price_nav, 1e-9)
    buy_idx = [int(i) for i in replay["buy_indices"] if 0 <= int(i) < len(tape.dates)]
    sell_idx = [int(i) for i in replay["sell_indices"] if 0 <= int(i) < len(tape.dates)]
    active_rules = rulebook.head(10).to_dict("records")
    if rule_id:
        chosen_rule = rule_id
    else:
        chosen_rule = str(active_rules[0].get("rule_id")) if active_rules else ""
    dates = [_date_text(d) for d in tape.dates]
    windows = _rule_windows_for_stock(code, chosen_rule, dates)
    payload = {
        "status": "ok",
        "code": code,
        "name": tape.name or _stock_name(code),
        "as_of": tape.dates[-1],
        "start_date": tape.dates[0],
        "domain": domain,
        "domain_axis": "风格×市值12域",
        "rule_id": chosen_rule,
        "rule_name": _humanize_rule(chosen_rule) if chosen_rule else "",
        "date_format": "iso",
        "dates": dates,
        "price_nav": tape.price_nav.tolist(),
        "strategy_nav": replay["strategy_nav"].tolist(),
        "relative_strength": relative.tolist(),
        "positions": replay["positions"].tolist(),
        "buy_points": [{"date": dates[i], "value": float(tape.price_nav[i])} for i in buy_idx],
        "sell_points": [{"date": dates[i], "value": float(tape.price_nav[i])} for i in sell_idx],
        "windows": windows,
        "metrics": metrics,
        "annual_stats": annual,
        "current_position": float(replay["current_position"]),
        "current_position_label": _position_label(float(replay["current_position"])),
        "current_score": float(replay["current_score"]),
        "latest_signal": (
            f"{code} {tape.name}: {domain}；当前{_position_label(float(replay['current_position']))}；"
            f"策略Sharpe {metrics['strategy_sharpe']:.2f} / 原股价Sharpe {metrics['price_sharpe']:.2f}；"
            f"策略年化 {metrics['strategy_annual_return']:.1%} / 原股价年化 {metrics['price_annual_return']:.1%}。"
        ),
        "logic": [
            "先用股票最新风格×市值标签定位域。",
            "调用该域全股票历史事件学习出来的共享规则权重与共享执行器。",
            "个股只提供当日K线事件触发压力，不单独调参。",
            "仓位为0/25/50/75/100五档，买卖点标在原股价净值线上。",
        ],
        "top_domain_rules": active_rules,
        "model_boundary": "域内共享规则；不是单股过拟合；上市一个月后开始净值与买卖点判断。",
    }
    payload = _json_clean(payload)
    cache_path.write_text(json.dumps(payload, ensure_ascii=False, default=_json_default), encoding="utf-8")
    return payload


def _rule_windows_for_stock(code: str, rule_id: str, dates: list[str]) -> list[dict[str, Any]]:
    if not rule_id or not dates:
        return []
    full = _events_full()
    rows = full.loc[full["ts_code"].eq(code) & full["rule_id"].eq(rule_id)].sort_values("date")
    if rows.empty:
        return []
    date_index = {date: idx for idx, date in enumerate(dates)}
    out = []
    for _, row in rows.tail(10).iterrows():
        date = _date_text(row["date"])
        idx = date_index.get(date)
        if idx is None:
            continue
        out.append(
            {
                "event_date": date,
                "x0": dates[max(0, idx - 60)],
                "x1": dates[min(len(dates) - 1, idx + 60)],
                "label": _humanize_rule(rule_id),
            }
        )
    return out
