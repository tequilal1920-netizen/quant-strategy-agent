"""Build the canonical per-industry trend and score-history snapshot."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pandas as pd

import build_snapshot
import engine


def main() -> int:
    build_snapshot.configure()
    snapshot = json.loads(engine.OUTPUT.read_text(encoding="utf-8"))
    frames = engine._load_cmb_sheets()
    contracts = engine._build_contracts(frames)
    close = engine._load_closes()
    aligned, diagnostics = engine._align_features(contracts, close.index)
    candidates = engine._candidate_scores(contracts, aligned, diagnostics, close.index)
    selected = snapshot["industry"]["frequencies"]["monthly"]["selected_candidate"]
    score = candidates[selected]
    signal_dates = engine._signal_dates(close.index, "monthly")[-48:]
    ranking = {
        row["name"]: row
        for row in snapshot["industry"]["frequencies"]["monthly"]["ranking"]
    }
    benchmark = close.mean(axis=1)
    payload = {
        "schema_version": "2.0",
        "generated_at": datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z"),
        "as_of": close.index.max().strftime("%Y-%m-%d"),
        "selected_candidate": selected,
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
        for day in signal_dates:
            value = score.at[day, industry] if day in score.index else None
            if value is None or pd.isna(value):
                continue
            history.append(
                {
                    "date": day.strftime("%Y-%m-%d"),
                    "score": round(float(value), 6),
                    "components": {},
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
    output = engine.DATA_DIR / "rotation_tracking.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    temporary.replace(output)
    print(json.dumps({"industries": len(payload["industries"]), "as_of": payload["as_of"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
