from pathlib import Path
import os
import sys

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
RUNTIME_PARENT = PACKAGE_ROOT / "runtime"
sys.path.insert(0, str(RUNTIME_PARENT))

if not os.environ.get("QUANT_AGENT_SNAPSHOT_ROOT"):
    for candidate in (
        REPOSITORY_ROOT / "board" / "quant_strategy_agent_vnext" / "data",
        REPOSITORY_ROOT / "board" / "quant_strategy_agent" / "data",
    ):
        if candidate.is_dir():
            os.environ["QUANT_AGENT_SNAPSHOT_ROOT"] = str(candidate)
            break
factor_manifest = PACKAGE_ROOT / "source" / "champion_manifest.json"
if factor_manifest.is_file():
    os.environ.setdefault("QUANT_AGENT_FACTOR_MANIFEST", str(factor_manifest))
pattern_root = PACKAGE_ROOT / "references" / "kline-patterns"
if pattern_root.is_dir():
    os.environ.setdefault("QUANT_AGENT_KLINE_PATTERN_ROOT", str(pattern_root))

from agent_runtime.cli import main_for_skill


if __name__ == "__main__":
    raise SystemExit(main_for_skill("research-home"))