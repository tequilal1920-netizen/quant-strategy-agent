"""V4.1 research release with pre-defined, validation-selected robust candidates."""

from __future__ import annotations

import numpy as np
import pandas as pd

import engine as worker
import event_overrides as release4


_original_feature = worker._feature
_original_candidate_scores = worker._candidate_scores


def _feature(contract):
    if contract.source_kind != "event":
        return _original_feature(contract)
    raw = pd.to_numeric(contract.raw, errors="coerce").dropna().sort_index()
    if raw.empty:
        return raw
    activity = np.log1p(raw.rolling(13, min_periods=4).sum())
    mean = activity.rolling(104, min_periods=52).mean()
    std = activity.rolling(104, min_periods=52).std(ddof=0).replace(0, np.nan)
    return activity.sub(mean).div(std).clip(-4, 4).fillna(0.0)


def _candidate_scores(contracts, aligned, diagnostics, index):
    outputs = _original_candidate_scores(contracts, aligned, diagnostics, index)
    direct_dominant = pd.DataFrame(index=index, columns=list(worker.INDUSTRY_CODES), dtype=float)
    for industry, items in contracts.items():
        frame = pd.DataFrame(aligned[industry])
        signs = pd.Series({item.variable: (1.0 if diagnostics[industry].get(item.variable, 0.0) >= 0 else -1.0) for item in items})
        weights = pd.Series({item.variable: (1.0 if item.source_kind == "direct" else 0.20) for item in items})
        signed = frame.mul(signs, axis=1)
        numerator = signed.mul(weights, axis=1).sum(axis=1, min_count=4)
        denominator = signed.notna().mul(weights, axis=1).sum(axis=1).replace(0, np.nan)
        direct_dominant[industry] = numerator.div(denominator)
    outputs["C4_direct_dominant"] = direct_dominant.rank(axis=1, pct=True, method="average").where(direct_dominant.notna())
    outputs["C5_ic_quarter_smooth"] = outputs["C3_train_ic"].rolling(63, min_periods=20).mean().rank(axis=1, pct=True)
    outputs["C6_direct_month_smooth"] = outputs["C4_direct_dominant"].rolling(21, min_periods=8).mean().rank(axis=1, pct=True)
    consensus = outputs["C1_equal"].add(outputs["C3_train_ic"], fill_value=np.nan).add(outputs["C4_direct_dominant"], fill_value=np.nan)
    outputs["C7_consensus"] = consensus.div(3.0).rank(axis=1, pct=True)
    return outputs


def configure() -> None:
    unique_event_industries = {
        "农林牧渔", "基础化工", "钢铁", "交通运输", "建筑装饰",
        "商贸零售", "传媒", "通信", "石油石化", "纺织服饰",
        "轻工制造", "机械设备", "煤炭", "美容护理",
    }
    worker.EVENT_BLUEPRINTS = {
        **release4.EVENTS,
        **{name: worker.EVENT_BLUEPRINTS[name] for name in unique_event_industries},
    }
    release4.GAP_INDUSTRIES.update(unique_event_industries)
    worker._event_rows = release4._event_rows
    worker._select_direct_contracts = release4._select
    worker._feature = _feature
    worker._candidate_scores = _candidate_scores


def main() -> int:
    configure()
    return worker.main()


if __name__ == "__main__":
    raise SystemExit(main())
