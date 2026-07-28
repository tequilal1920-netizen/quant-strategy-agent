from __future__ import annotations

import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .core import QueryError, catalog, query


class Handler(BaseHTTPRequestHandler):
    server_version = "QuantAgentRuntime/1.0"

    def _authorized(self) -> bool:
        token = os.environ.get("QUANT_AGENT_RUNTIME_TOKEN", "")
        if not token:
            return True
        return self.headers.get("Authorization", "") == f"Bearer {token}"

    def _send(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if not self._authorized():
            self._send(HTTPStatus.UNAUTHORIZED, {"状态": "未授权"})
            return
        if self.path == "/health":
            self._send(HTTPStatus.OK, {"状态": "正常", "服务": "量化模型运行层"})
            return
        if self.path == "/v1/catalog":
            self._send(HTTPStatus.OK, catalog())
            return
        self._send(HTTPStatus.NOT_FOUND, {"状态": "不存在"})

    def do_POST(self) -> None:
        if not self._authorized():
            self._send(HTTPStatus.UNAUTHORIZED, {"状态": "未授权"})
            return
        if self.path != "/v1/query":
            self._send(HTTPStatus.NOT_FOUND, {"状态": "不存在"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length) or b"{}")
            result = query(
                str(body.get("skill", "")),
                str(body.get("operation", "")),
                body.get("params") or {},
            )
        except (ValueError, json.JSONDecodeError, QueryError) as exc:
            self._send(HTTPStatus.BAD_REQUEST, {"状态": "失败", "原因": str(exc)})
            return
        self._send(HTTPStatus.OK, result)

    def log_message(self, format: str, *args: Any) -> None:
        if os.environ.get("QUANT_AGENT_RUNTIME_LOG", "") == "1":
            super().log_message(format, *args)


def serve(host: str, port: int) -> None:
    if host not in {"127.0.0.1", "localhost", "::1"} and not os.environ.get(
        "QUANT_AGENT_RUNTIME_TOKEN"
    ):
        raise QueryError("非本机地址启动服务时必须设置 QUANT_AGENT_RUNTIME_TOKEN")
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"量化模型运行层已启动：http://{host}:{port}")
    server.serve_forever()
