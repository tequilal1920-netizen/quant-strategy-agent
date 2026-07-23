from __future__ import annotations

import argparse
import os
from pathlib import Path

import nbformat
from nbclient import NotebookClient


def main() -> int:
    parser = argparse.ArgumentParser(description="Execute the dashboard update notebook deterministically")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--kernel", default="research-market-board")
    args = parser.parse_args()
    root = args.root.resolve()
    source = root / "notebooks" / "update_dashboard.ipynb"
    output = root / "notebooks" / "update_dashboard.executed.ipynb"
    if not source.is_file():
        raise SystemExit("update notebook not found")
    os.environ.setdefault("DASHBOARD_DATA_DIR", str(root / "data"))
    os.environ.setdefault("DASHBOARD_CATALOG_PATH", str(root / "data" / "indicator_catalog.seed.json"))
    os.environ.setdefault("DASHBOARD_CACHE_TTL_SECONDS", "72000")
    os.environ.setdefault("DASHBOARD_ENABLE_TUSHARE", "0")
    os.environ.setdefault("DASHBOARD_ENABLE_IFIND", "0")
    os.environ.setdefault("DASHBOARD_ENABLE_WIND", "0")
    notebook = nbformat.read(source, as_version=4)
    client = NotebookClient(notebook, timeout=1800, kernel_name=args.kernel, allow_errors=False)
    client.execute(cwd=str(root))
    temp = output.with_suffix(".tmp.ipynb")
    nbformat.write(notebook, temp)
    os.replace(temp, output)
    print({"executed": str(output), "cells": len(notebook.cells), "errors": 0})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())