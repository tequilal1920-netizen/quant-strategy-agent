from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP


ROOT = Path(__file__).resolve().parents[3]
AGENT_ROOT = ROOT if (ROOT / "ai-models").exists() else ROOT / "agent"
CATALOG_PATH = ROOT / "mcp" / "model_catalog.json"
TEXT_SUFFIXES = {".md", ".py", ".json", ".yml", ".yaml", ".txt", ".html", ".js", ".css", ".sql", ".ps1"}

mcp = FastMCP("中信建投量化策略Agent")


def _load_catalog() -> dict[str, Any]:
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def _module_entry(module: str) -> dict[str, Any] | None:
    catalog = _load_catalog()
    for item in catalog.get("modules", []):
        if item.get("id") == module or item.get("ai_model", "").split("/")[-1] == module or item.get("title") == module:
            return item
    return None


def _read_text(path: Path, max_chars: int = 20000) -> dict[str, Any]:
    resolved = path.resolve()
    root = AGENT_ROOT.resolve()
    if not str(resolved).lower().startswith(str(root).lower()):
        return {"ok": False, "error": "path_outside_agent_root"}
    if not resolved.exists() or resolved.suffix.lower() not in TEXT_SUFFIXES:
        return {"ok": False, "error": f"不可读取或非文本文件: {path}"}
    text = resolved.read_text(encoding="utf-8", errors="replace")
    truncated = len(text) > max_chars
    return {
        "ok": True,
        "path": str(resolved.relative_to(root)).replace("\\", "/"),
        "truncated": truncated,
        "content": text[:max_chars],
    }


@mcp.tool()
def list_modules() -> dict[str, Any]:
    """列出可用模型、二级页面、源码位置和查询示例。"""
    return _load_catalog()


@mcp.tool()
def learning_path() -> dict[str, Any]:
    """给新 AI 返回从 GitHub 学习整个量化 Agent 的建议顺序和必读文件。"""
    catalog = _load_catalog()
    modules = []
    for item in catalog.get("modules", []):
        ai_model = item.get("ai_model", "")
        modules.append(
            {
                "title": item.get("title"),
                "secondary": item.get("secondary", []),
                "coverage": item.get("coverage", []),
                "must_read": [
                    f"{ai_model}/SKILL.md",
                    f"{ai_model}/README.md",
                    f"{ai_model}/PACKAGE.json",
                    f"{ai_model}/references/module-map.md",
                ],
                "source_roots": item.get("sources", []),
                "query_examples": item.get("query_examples", []),
            }
        )
    return {
        "ok": True,
        "principle": "先读 model_catalog，再读每个 ai-model 的 SKILL/README/PACKAGE/references，最后按 query.py 只读查询；数据库和凭据只在本机环境变量或私有库中接入。",
        "modules": modules,
        "public_site": catalog.get("public_site"),
        "data_policy": catalog.get("data_policy"),
    }


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
def read_model_doc(module: str, document: str = "skill", max_chars: int = 20000) -> dict[str, Any]:
    """读取白名单模型文档，供新 AI 学习模型公式、边界和调用入口。"""
    entry = _module_entry(module)
    if not entry:
        return {"ok": False, "error": f"未知模型: {module}"}
    base = AGENT_ROOT / entry["ai_model"]
    docs = {
        "skill": base / "SKILL.md",
        "readme": base / "README.md",
        "package": base / "PACKAGE.json",
        "module-map": base / "references" / "module-map.md",
        "source-readme": base / "source" / "README.md",
    }
    path = docs.get(document)
    if path is None:
        return {"ok": False, "error": f"未知文档类型: {document}", "available": sorted(docs)}
    return _read_text(path, max_chars=max(1000, min(int(max_chars), 60000)))


@mcp.tool()
def search_model_text(module: str, keyword: str, limit: int = 20) -> dict[str, Any]:
    """在单个模型公开源码和文档中搜索关键词，返回命中位置和短片段。"""
    entry = _module_entry(module)
    if not entry:
        return {"ok": False, "error": f"未知模型: {module}"}
    if not keyword or len(keyword.strip()) < 2:
        return {"ok": False, "error": "keyword_too_short"}
    roots = [AGENT_ROOT / entry["ai_model"]]
    for source in entry.get("sources", []):
        candidate = AGENT_ROOT / source
        if candidate.exists():
            roots.append(candidate)
    hits: list[dict[str, Any]] = []
    root_resolved = AGENT_ROOT.resolve()
    for root in roots:
        for path in root.rglob("*"):
            if len(hits) >= max(1, min(int(limit), 80)):
                break
            if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            lower_text = text.lower()
            lower_keyword = keyword.lower()
            idx = lower_text.find(lower_keyword)
            if idx < 0:
                continue
            start = max(0, idx - 140)
            end = min(len(text), idx + len(keyword) + 220)
            hits.append(
                {
                    "path": str(path.resolve().relative_to(root_resolved)).replace("\\", "/"),
                    "snippet": text[start:end].replace("\r", ""),
                }
            )
    return {"ok": True, "module": module, "keyword": keyword, "hits": hits}


@mcp.tool()
def public_health(site: str | None = None) -> dict[str, Any]:
    """检查公网看板 healthz，确认当前部署版本和关键字段。"""
    catalog = _load_catalog()
    base = (site or catalog.get("public_site") or "").rstrip("/")
    if not base:
        return {"ok": False, "error": "public_site_missing"}
    url = base + "/healthz"
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            raw = response.read().decode("utf-8", errors="replace")
        try:
            payload: Any = json.loads(raw)
        except json.JSONDecodeError:
            payload = raw[:2000]
        return {"ok": True, "url": url, "payload": payload}
    except (urllib.error.URLError, TimeoutError) as exc:
        return {"ok": False, "url": url, "error": str(exc)}


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
