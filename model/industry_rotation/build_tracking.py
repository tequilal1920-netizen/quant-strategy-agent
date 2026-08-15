"""Build the canonical per-industry trend and score-history snapshot."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import pandas as pd

import build_snapshot
import engine
import six_dimension_model as six


DEFAULT_OUTPUT = engine.DATA_DIR / "rotation_tracking.json"


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build industry trend and six-dimension score history.",
    )
    parser.add_argument(
        "--snapshot",
        type=Path,
        default=engine.OUTPUT,
        help="Candidate rotation snapshot used for model selection and ranking.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Destination tracking JSON file.",
    )
    return parser.parse_args(argv)


def _tracking_components(
    components: dict[str, float | None],
) -> dict[str, float | None]:
    """Expose only the six model dimensions and the derived low-crowding score."""
    return {
        name: components.get(name)
        for name in (
            "prosperity",
            "fundamental",
            "technical",
            "valuation",
            "funds",
            "crowding",
            "anti_crowding",
        )
    }


def build(snapshot_path: Path, output: Path) -> dict[str, Any]:
    build_snapshot.configure()
    snapshot_path = Path(snapshot_path)
    output = Path(output)
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    frames = engine._load_cmb_sheets()
    contracts = engine._build_contracts(frames)
    close = engine._load_closes()
    snapshot_as_of = snapshot.get("as_of")
    if snapshot_as_of:
        close = close.loc[: pd.Timestamp(snapshot_as_of)]
    if close.empty:
        raise ValueError(f"No close data available through snapshot as_of={snapshot_as_of!r}")
    engine._CLOSE_CACHE = close
    aligned, diagnostics = engine._align_features(contracts, close.index)
    candidates = engine._candidate_scores(contracts, aligned, diagnostics, close.index)
    frequency = snapshot["industry"]["frequencies"]["monthly"]
    production_candidate = frequency["selected_candidate"]
    selected = production_candidate
    if "six_dimension" in str(selected):
        raise ValueError("tracking_snapshot_requires_governed_production_champion")
    if selected not in candidates:
        raise KeyError(
            f"Snapshot display candidate {selected!r} is unavailable in the current engine"
        )
    score = candidates[selected]
    latest_score_date = pd.Timestamp(score.dropna(how="all").index.max())
    six_as_of = pd.Timestamp(snapshot.get("six_dimension", {}).get("data_as_of", latest_score_date))
    model_as_of = min(latest_score_date, six_as_of, pd.Timestamp(close.index.max()))
    audit = next(
        (row for row in frequency.get("candidate_audit", []) if row.get("candidate") == selected),
        {},
    )
    policy = audit.get("target_policy", engine._candidate_target_policy(selected))
    targets = engine._targets(
        score,
        "monthly",
        close=close,
        **{
            key: policy[key]
            for key in ("buffer_size", "risk_weighted", "risk_overlay", "top_n")
        },
    )
    target_dates = [date for date in targets if date <= model_as_of]
    latest_target = targets[max(target_dates)] if target_dates else pd.Series(0.0, index=close.columns)
    latest_scores = score.loc[model_as_of].dropna().sort_values(ascending=False)
    ranking = {
        industry: {
            "rank": rank,
            "selected": float(latest_target.get(industry, 0.0)) > 0.0,
            "score": round(float(value), 6),
        }
        for rank, (industry, value) in enumerate(latest_scores.items(), start=1)
    }
    close = close.loc[:model_as_of]
    signal_dates = [date for date in engine._signal_dates(close.index, "monthly")[-48:] if date <= model_as_of]
    benchmark = close.mean(axis=1)
    payload = {
        "schema_version": "2.0",
        "generated_at": datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z"),
        "as_of": model_as_of.strftime("%Y-%m-%d"),
        "selected_candidate": selected,
        "production_candidate": production_candidate,
        "model_scope": "production_champion",
        "industries": {},
    }
    for industry in close.columns:
        local = close[industry].dropna().iloc[-520:]
        local_benchmark = benchmark.reindex(local.index).dropna()
        common = local.index.intersection(local_benchmark.index)
        local = local.reindex(common)
        local_benchmark = local_benchmark.reindex(common)
        normalized = local / local.iloc[0] * 100.0
        benchmark_normalized = local_benchmark / local_benchmark.iloc[0] * 100.0
        relative = normalized / benchmark_normalized * 100.0
        history = []
        component_history = six.component_history("monthly", signal_dates, industry)
        for day in signal_dates:
            value = score.at[day, industry] if day in score.index else None
            if value is None or pd.isna(value):
                continue
            date_key = day.strftime("%Y-%m-%d")
            history.append(
                {
                    "date": date_key,
                    "score": round(float(value), 6),
                    "components": _tracking_components(
                        component_history.get(date_key, {})
                    ),
                }
            )
        rank = ranking[industry]
        payload["industries"][industry] = {
            "rank": rank["rank"],
            "selected": rank["selected"],
            "score": rank["score"],
            "trend": [
                {
                    "date": day.strftime("%Y-%m-%d"),
                    "industry": round(float(normalized.at[day]), 4),
                    "equal_weight": round(float(benchmark_normalized.at[day]), 4),
                    "relative": round(float(relative.at[day]), 4),
                }
                for day in common
            ],
            "score_history": history,
        }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    temporary.replace(output)
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    payload = build(args.snapshot, args.output)
    print(
        json.dumps(
            {
                "industries": len(payload["industries"]),
                "as_of": payload["as_of"],
                "snapshot": str(args.snapshot),
                "output": str(args.output),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
