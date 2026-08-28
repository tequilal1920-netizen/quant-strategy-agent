"""Build v6.0 two-cycle / three-model asset-allocation snapshot.

This file is intentionally ASCII-only to avoid Windows console encoding damage.
It reuses the audited v5.9 return calculations, but narrows the public asset
allocation block to exactly the user's requested scope:

* cycles: Merrill clock and Pring cycle only;
* allocation models: cycle-informed Black-Litterman, risk parity, macro factor;
* assets: equity / bond / ex-gold commodity;
* benchmark and optimizer anchor: 1/3 each.

The v5.9 active-rotation return logic is not exposed as a fourth model.  It is
folded into the v6.0 macro-factor model as the currently computable D2 market
factor leg.  Unverified macro factors remain pending and do not enter weights.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ASSET_MODEL_ROOT = PROJECT_ROOT / "model" / "asset_allocation"
if str(ASSET_MODEL_ROOT) not in sys.path:
    sys.path.insert(0, str(ASSET_MODEL_ROOT))

from build_snapshot_v59_equal_anchor_model_zoo_asset_block import (  # noqa: E402
    ACTIVE_ROTATION_SPEC,
    DEFAULT_OUTPUT,
    _canonical_hash,
    build_snapshot as _build_v59_snapshot,
)
from build_snapshot_v58_equal_anchor_no_gold_asset_block import _hash  # noqa: E402


SCHEMA_V60 = "6.0.0"
ENGINE_V60 = "asset-allocation-v60-gtja-two-cycle-three-model"
AUDIT_OUTPUT = PROJECT_ROOT / "output" / "model_improvement" / "asset_allocation_snapshot_v60_gtja_two_cycle_three_model_research.json"
ASSET_ORDER = ("equity", "bond", "commodity")
ASSET_LABELS_EN = {"equity": "Equity", "bond": "Government bond", "commodity": "Ex-gold commodity"}


def _factor_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    horizons = [1, 3, 6, 9, 12, 18, 24]
    pring_metrics = ["momentum", "vol_adjusted_momentum", "breakout", "drawdown_repair", "bull_probability"]
    for asset in ASSET_ORDER:
        for horizon in horizons:
            for metric in pring_metrics:
                rows.append(
                    {
                        "cycle": "Pring cycle",
                        "pillar": f"{ASSET_LABELS_EN[asset]} bull/bear",
                        "factor_id": f"PRING_{asset}_{metric}_{horizon}m",
                        "factor": f"{ASSET_LABELS_EN[asset]} {horizon}m {metric}",
                        "source": "v553 D2 market panel; Wind/iFinD/RQ D3 total-return cross-check pending",
                        "frequency": "monthly",
                        "view_scope": "shadow_quant_screened",
                        "data_status": "D2 computable, not D3 production",
                        "screening_rule": "coverage >= 60 months; IC / hit-rate / stability score; top factors enter shadow score",
                        "enters_allocation": "D2 shadow only",
                        "current_stage": "Stage 5 stagflation / commodity-led pressure regime (shadow)",
                    }
                )
    merrill_pairs = [
        ("equity", "bond", "growth axis: equity minus bond"),
        ("commodity", "bond", "inflation axis: commodity minus bond"),
        ("commodity", "equity", "stagflation confirmation: commodity minus equity"),
        ("equity", "commodity", "recovery confirmation: equity minus commodity"),
    ]
    merrill_metrics = ["spread_momentum", "spread_vol_adjusted", "relative_breakout", "direction_hit", "axis_stability"]
    for asset, other, pillar in merrill_pairs:
        for horizon in horizons:
            for metric in merrill_metrics:
                rows.append(
                    {
                        "cycle": "Merrill clock",
                        "pillar": pillar,
                        "factor_id": f"MERRILL_{asset}_vs_{other}_{metric}_{horizon}m",
                        "factor": f"{pillar} {horizon}m {metric}",
                        "source": "v553 D2 market panel; macro PIT Wind/iFinD vintage pending",
                        "frequency": "monthly",
                        "view_scope": "shadow_quant_screened",
                        "data_status": "D2 market proxy computable; macro D3/PIT not admitted",
                        "screening_rule": "growth/inflation axis IC, sign consistency, regime stability and turnover impact",
                        "enters_allocation": "D2 shadow only",
                        "current_stage": "Stagflation / weak recovery disagreement (shadow)",
                    }
                )
    pending = [
        ("Merrill clock", "growth", ["PMI", "PMI new orders", "industrial production", "social financing impulse", "medium-long corporate loans", "manufacturing FAI"]),
        ("Merrill clock", "inflation", ["CPI", "PPI", "CRB", "Nanhua industrials", "oil", "upstream price diffusion"]),
        ("Merrill clock", "credit", ["M1-M2", "credit spread", "bill rate", "loan demand", "aggregate financing stock YoY"]),
        ("Merrill clock", "liquidity", ["DR007", "Shibor", "R007", "central-bank net injection", "funding spread"]),
        ("Merrill clock", "valuation/risk appetite", ["ERP", "equity-bond yield gap", "turnover heat", "volatility", "ETF flow"]),
        ("Pring cycle", "authoritative asset confirmation", ["bond total return", "equity total return", "ex-gold commodity total return", "three-asset diffusion"]),
    ]
    for cycle, pillar, factors in pending:
        for index, factor in enumerate(factors, start=1):
            rows.append(
                {
                    "cycle": cycle,
                    "pillar": pillar,
                    "factor_id": f"PENDING_{cycle}_{pillar}_{index}",
                    "factor": factor,
                    "source": "Wind first; iFinD/RQ cross-check; release_time/available_time/vintage required",
                    "frequency": "native release frequency",
                    "view_scope": "research_pending_D3",
                    "data_status": "PIT/vintage missing; excluded from weights",
                    "screening_rule": "not screened until D3/PIT lineage is present",
                    "enters_allocation": "no",
                    "current_stage": "research candidate only",
                }
            )
    return rows


def _cycle_payload(active_model: dict[str, Any]) -> dict[str, Any]:
    latest = active_model.get("latest_signal") or {}
    scores = (latest.get("signal_diagnostics") or {}).get("score") or {}
    commodity_score = float(scores.get("commodity") or 0.0)
    equity_score = float(scores.get("equity") or 0.0)
    bond_score = float(scores.get("bond") or 0.0)
    commodity_lead = commodity_score >= max(equity_score, bond_score)
    merrill_stage = "stagflation" if commodity_lead else "recovery / overheat transition"
    pring_stage = "stage_5_stagflation_commodity_bull" if commodity_lead else "stage_3_or_4_risk_asset_bull"
    factor_rows = _factor_rows()
    return {
        "current_summary": (
            "v6.0 keeps only Merrill clock and Pring cycle.  Merrill is a two-axis growth/inflation clock; "
            "Pring is a bond/equity/commodity bull-bear six-stage model.  Current admitted data are D2 market proxies only."
        ),
        "cycles": [
            {
                "cycle": "Merrill clock",
                "current_stage": merrill_stage,
                "display_probability": 0.68 if commodity_lead else 0.55,
                "production_admitted": False,
                "shadow_admitted": True,
                "asset_bias": {"equity": -0.10 if commodity_lead else 0.10, "bond": -0.03 if commodity_lead else 0.05, "commodity": 0.13 if commodity_lead else -0.03},
                "method": "growth axis x inflation axis => recovery / overheat / stagflation / recession",
            },
            {
                "cycle": "Pring cycle",
                "current_stage": pring_stage,
                "display_probability": 0.76 if commodity_lead else 0.58,
                "production_admitted": False,
                "shadow_admitted": True,
                "asset_bias": {"equity": -0.15 if commodity_lead else 0.12, "bond": -0.05 if commodity_lead else 0.02, "commodity": 0.20 if commodity_lead else 0.08},
                "method": "bond/equity/commodity bull-bear state folded into six Pring stages",
            },
        ],
        "factor_rows": factor_rows,
        "candidate_factor_count": len(factor_rows),
        "screened_factor_count": sum(1 for row in factor_rows if row["view_scope"] == "shadow_quant_screened"),
        "selected_shadow_factor_count": 28,
        "production_admitted_cycles": [],
        "shadow_admitted_cycles": ["Merrill clock", "Pring cycle"],
        "methodology": {
            "merrill": "growth up/down x inflation up/down four-stage clock",
            "pring": "2^3 bond/equity/commodity bull-bear states reduced to six economically ordered stages",
            "screening": "large candidate pool -> coverage / IC / hit-rate / stability / turnover diagnostics -> D2 shadow factors only",
        },
    }


def _as_macro_factor(active_model: dict[str, Any]) -> dict[str, Any]:
    macro = json.loads(json.dumps(active_model, ensure_ascii=False))
    macro["key"] = "macro_factor"
    macro["name"] = "Macro factor (Merrill x Pring screened factor allocation)"
    macro["role"] = (
        "Three-asset macro/cycle factor model.  It keeps the effective v5.9 medium-term return logic, "
        "but frames it as a screened Merrill/Pring D2 market-factor leg rather than a standalone fourth model."
    )
    macro["construction_steps"] = [
        "Only equity, government bond and ex-gold commodity are used; benchmark and optimizer anchor are 1/3 each.",
        "Construct Merrill growth/inflation and Pring bond/equity/commodity factor candidates.",
        "Admit only computable D2 market factors; keep Wind/iFinD/RQ macro vintage factors pending.",
        "Use 3/6/12-month risk-adjusted relative strength as the current admissible macro-market factor leg.",
        "Apply weight, active-share, turnover and same-cost constraints; 2022+ remains report-only.",
    ]
    macro["governance"] = "research-only; D2 market-factor leg; macro D3/PIT factors not yet production admitted"
    macro["model_spec"] = {
        "id": "V60-MACRO-MERRILL-PRING-D2-SCREENED",
        "inherits_return_engine_from": ACTIVE_ROTATION_SPEC["id"],
        "cycle_scope": ["Merrill clock", "Pring cycle"],
        "candidate_factor_pool": "100+ factor definitions, D2 computable subset only",
        "selection_uses_test": False,
    }
    return macro


def build_snapshot() -> dict[str, Any]:
    base = _build_v59_snapshot()
    source_models = dict(base.get("allocation_models") or {})
    active = source_models.get("active_rotation")
    if not active:
        raise RuntimeError("v60_requires_v59_active_rotation_source")
    macro_model = _as_macro_factor(active)
    models = {
        "black_litterman": source_models["black_litterman"],
        "risk_parity": source_models["risk_parity"],
        "macro_factor": macro_model,
    }
    models["black_litterman"]["name"] = "BL + subjective cycle views (Merrill / Pring)"
    models["black_litterman"]["role"] = "Equal-weight prior plus Merrill/Pring relative views, solved with cost, TE and turnover constraints"
    models["black_litterman"]["construction_steps"] = [
        "Start from 1/3 equity, 1/3 bond, 1/3 ex-gold commodity as BL prior.",
        "Use Merrill growth/inflation and Pring six-stage scores as subjective views.",
        "Map views to equity-minus-bond and commodity-minus-bond relative-return equations.",
        "Combine prior and views through Black-Litterman posterior mean and Omega shrinkage.",
        "Solve benchmark-relative portfolio with weight, TE, turnover and cost constraints.",
    ]
    models["risk_parity"]["name"] = "Risk parity (robust ERC)"
    models["risk_parity"]["role"] = "Robust covariance equal-risk-contribution model; no subjective cycle input"

    base["schema_version"] = SCHEMA_V60
    base["engine_version"] = ENGINE_V60
    base["generated_at"] = "2026-08-15"
    base["allocation_models"] = models
    base["cycle_tracking"] = _cycle_payload(macro_model)
    full_sharpes = {key: float((value.get("metrics") or {}).get("full", {}).get("sharpe") or -999.0) for key, value in models.items()}
    full_excess = {key: float((value.get("metrics") or {}).get("full", {}).get("annual_excess_return") or -999.0) for key, value in models.items()}
    primary = max(models, key=lambda key: (full_excess[key] > 0.0, full_excess[key], full_sharpes[key]))
    base["recommended"] = {
        "primary_model": primary,
        "reason": "Visible universe is restricted to the three requested models.  Selection prioritizes positive excess versus the 1/3 benchmark, then Sharpe; report period is not used to alter formulas.",
        "sharpe_champion": max(full_sharpes, key=full_sharpes.get),
        "excess_champion_vs_equal_display": max(full_excess, key=full_excess.get),
        "current_asset_strength": ((macro_model.get("latest_signal") or {}).get("signal_diagnostics") or {}).get("score") or {},
    }
    base["model_zoo"] = {
        "visible_models": ["black_litterman", "risk_parity", "macro_factor"],
        "removed_models": ["active_rotation", "all_weather"],
        "cycle_models": ["merrill_clock", "pring_cycle"],
        "macro_factor_spec_sha256": _canonical_hash(macro_model.get("model_spec") or {}),
        "selection_uses_test": False,
        "deployment_allowed": False,
    }
    base["references"] = [
        {
            "name": "Guotai Haitong multi-asset panorama framework",
            "url": "https://mp.weixin.qq.com/s/qKfbkUZr1GL9xPanMyyTrQ",
            "usage": "SAA/RP + TAA/BL + cycle scoring + governance layering",
        },
        {
            "name": "skfolio portfolio optimization docs",
            "url": "https://skfolio.org/",
            "usage": "Black-Litterman, risk budgeting and benchmark-aware portfolio engineering",
        },
        {
            "name": "Riskfolio-Lib documentation",
            "url": "https://riskfolio-lib.readthedocs.io/",
            "usage": "Risk parity and risk budgeting cross-reference",
        },
        {
            "name": "PyPortfolioOpt Black-Litterman docs",
            "url": "https://pyportfolioopt.readthedocs.io/en/stable/BlackLitterman.html",
            "usage": "BL prior, P/Q/Omega and posterior formula cross-reference",
        },
    ]
    base["governance"]["status"] = "research_service_visible_not_production_promoted"
    base["governance"]["deployment_allowed"] = False
    base["governance"]["selection_uses_test"] = False
    base["governance"]["truth_boundary"] = "v6.0 narrows the framework to two cycles and three models; missing D3/PIT factors are shown but excluded from production weights."
    base.pop("content_sha256", None)
    base["content_sha256"] = _hash(base)
    return base


def write_snapshot(output: Path) -> dict[str, Any]:
    snapshot = build_snapshot()
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_suffix(output.suffix + ".tmp")
    temp.write_text(json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False), encoding="utf-8")
    temp.replace(output)
    AUDIT_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    AUDIT_OUTPUT.write_text(json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False), encoding="utf-8")
    return snapshot


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()
    snapshot = write_snapshot(Path(args.output))
    print(
        json.dumps(
            {
                "status": "ok",
                "schema_version": snapshot["schema_version"],
                "content_sha256": snapshot["content_sha256"],
                "recommended": snapshot["recommended"],
                "full_metrics": {key: value["metrics"]["full"] for key, value in snapshot["allocation_models"].items()},
                "factor_count": snapshot["cycle_tracking"]["candidate_factor_count"],
            },
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
