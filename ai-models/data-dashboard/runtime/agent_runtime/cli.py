from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from .core import QueryError, catalog, query
from .remote import RemoteModelClient
from .server import serve


def _parse_params(values: list[str]) -> dict[str, Any]:
    params: dict[str, Any] = {}
    for value in values:
        if "=" not in value:
            raise QueryError(f"参数必须使用 名称=值：{value}")
        key, raw = value.split("=", 1)
        key = key.strip()
        raw = raw.strip()
        if not key:
            raise QueryError("参数名称不能为空")
        try:
            params[key] = json.loads(raw)
        except json.JSONDecodeError:
            params[key] = raw
    return params


def _print(payload: Any, compact: bool = False) -> None:
    print(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=None if compact else 2,
            separators=(",", ":") if compact else None,
        )
    )


def _query_for_skill(skill: str, args: list[str]) -> int:
    parser = argparse.ArgumentParser(prog=f"{skill} query")
    parser.add_argument("operation")
    parser.add_argument("params", nargs="*")
    parser.add_argument("--compact", action="store_true")
    parsed = parser.parse_args(args)
    _print(
        query(skill, parsed.operation, _parse_params(parsed.params)),
        compact=parsed.compact,
    )
    return 0


def main_for_skill(skill: str) -> int:
    try:
        return _query_for_skill(skill, sys.argv[1:])
    except QueryError as exc:
        _print({"状态": "失败", "原因": str(exc)})
        return 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m agent_runtime",
        description="量化策略 Agent 的本地查询与远程模型调用入口",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("catalog", help="列出八个一级模型 Skill")

    query_parser = sub.add_parser("query", help="查询模型快照或治理结果")
    query_parser.add_argument("skill")
    query_parser.add_argument("operation")
    query_parser.add_argument("params", nargs="*")
    query_parser.add_argument("--compact", action="store_true")

    doctor = sub.add_parser("doctor", help="检查模型快照、数据库和输出目录")
    doctor.add_argument("--strict", action="store_true")

    remote = sub.add_parser("remote", help="调用已部署统一模型服务")
    remote.add_argument("method", choices=["GET", "POST", "get", "post"])
    remote.add_argument("path")
    remote.add_argument("--json", default="")
    remote.add_argument("--payload-file")
    remote.add_argument("--base-url")

    server = sub.add_parser("serve", help="启动本机只读 JSON 查询服务")
    server.add_argument("--host", default="127.0.0.1")
    server.add_argument("--port", type=int, default=8091)
    return parser


def _doctor(strict: bool) -> int:
    checks = []
    snapshot = os.environ.get("QUANT_AGENT_SNAPSHOT_ROOT", "")
    roots = [
        Path(snapshot) if snapshot else None,
        Path(os.environ.get("RESEARCH_WAREHOUSE_DB", ""))
        if os.environ.get("RESEARCH_WAREHOUSE_DB")
        else None,
        Path(os.environ.get("FACTOR_STATE_DB", ""))
        if os.environ.get("FACTOR_STATE_DB")
        else None,
        Path(os.environ.get("QUANT_AGENT_OUTPUT_ROOT", ""))
        if os.environ.get("QUANT_AGENT_OUTPUT_ROOT")
        else None,
    ]
    names = ["模型快照", "研究数据库", "因子状态库", "模型输出"]
    for name, path in zip(names, roots):
        checks.append(
            {
                "项目": name,
                "路径": str(path) if path else None,
                "存在": bool(path and path.exists()),
                "已配置": path is not None,
            }
        )
    try:
        sample = query("asset-allocation", "cycle", {})
        checks.append(
            {
                "项目": "资产配置查询",
                "路径": sample.get("数据来源"),
                "存在": True,
                "已配置": True,
            }
        )
    except QueryError as exc:
        checks.append(
            {
                "项目": "资产配置查询",
                "存在": False,
                "已配置": False,
                "原因": str(exc),
            }
        )
    failed = [
        row
        for row in checks
        if not row.get("存在")
        and (strict or row["项目"] in {"模型快照", "资产配置查询"})
    ]
    _print({"状态": "正常" if not failed else "受阻", "检查": checks})
    return 0 if not failed else 2


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    try:
        if args.command == "catalog":
            _print(catalog())
            return 0
        if args.command == "query":
            _print(
                query(args.skill, args.operation, _parse_params(args.params)),
                compact=args.compact,
            )
            return 0
        if args.command == "doctor":
            return _doctor(args.strict)
        if args.command == "remote":
            payload = None
            if args.payload_file:
                payload = json.loads(
                    Path(args.payload_file).read_text(encoding="utf-8")
                )
            elif args.json:
                payload = json.loads(args.json)
            client = RemoteModelClient(args.base_url)
            _print(client.request(args.method, args.path, payload))
            return 0
        if args.command == "serve":
            serve(args.host, args.port)
            return 0
    except (QueryError, json.JSONDecodeError, OSError) as exc:
        _print({"状态": "失败", "原因": str(exc)})
        return 2
    parser.error("未知命令")
    return 2
