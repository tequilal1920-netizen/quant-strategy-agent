from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP


ROOT = Path(__file__).resolve().parents[2]
AGENT_ROOT = ROOT if (ROOT / "ai-models").exists() else ROOT / "agent"
CATALOG_PATH = ROOT / "mcp" / "model_catalog.json"

mcp = FastMCP("中信建投量化策略Agent")


def _load_catalog() -> dict[str, Any]:
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


@mcp.tool()
def list_modules() -> dict[str, Any]:
    """列出可用模型、二级页面、源码位置和查询示例。"""
    return _load_catalog()


@mcp.tool()
def module_status(module: str) -> dict[str, Any]:
    """读取单个模型包的 PACKAGE.json 和 SKILL.md 摘要。"""
    package_dir = AGENT_ROOT / "ai-models" / module
    package_file = package_dir / "PACKAGE.json"
    skill_file = package_dir / "SKILL.md"
    if not package_dir.exists():
        return {"ok": False, "error": f"未知模型: {module}"}
    package = json.loads(package_file.read_text(encoding="utf-8")) if package_file.exists() else {}
    skill_head = ""
    if skill_file.exists():
        skill_head = "\n".join(skill_file.read_text(encoding="utf-8").splitlines()[:80])
    return {"ok": True, "module": module, "package": package, "skill_head": skill_head}


@mcp.tool()
def query_model(module: str, operation: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """调用统一 AI 模型查询脚本。params 会转成 key=value 参数。"""
    script = AGENT_ROOT / "ai-models" / module / "scripts" / "query.py"
    if not script.exists():
        return {"ok": False, "error": f"查询脚本不存在: {script}"}
    args = [sys.executable, str(script), operation]
    for key, value in (params or {}).items():
        args.append(f"{key}={value}")
    env = os.environ.copy()
    proc = subprocess.run(args, cwd=str(AGENT_ROOT), env=env, text=True, capture_output=True, timeout=180)
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
