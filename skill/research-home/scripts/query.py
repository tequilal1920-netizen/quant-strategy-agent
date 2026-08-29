from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from agent_runtime.cli import main_for_skill


if __name__ == "__main__":
    raise SystemExit(main_for_skill("research-home"))
