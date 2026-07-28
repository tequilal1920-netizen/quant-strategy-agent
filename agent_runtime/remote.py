from __future__ import annotations

import http.cookiejar
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .core import QueryError


class RemoteModelClient:
    """使用环境变量凭据访问已部署的统一模型服务。"""

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (
            base_url
            or os.environ.get("QUANT_AGENT_BASE_URL")
            or "http://127.0.0.1:8076/quant-agent-vnext"
        ).rstrip("/")
        self.username = os.environ.get("QUANT_AGENT_USER", "")
        self.password = os.environ.get("QUANT_AGENT_PASSWORD", "")
        jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(jar)
        )

    def _url(self, path: str) -> str:
        if not path.startswith("/"):
            path = "/" + path
        return self.base_url + path

    def login(self) -> None:
        if not self.username or not self.password:
            raise QueryError(
                "远程调用需要 QUANT_AGENT_USER 和 QUANT_AGENT_PASSWORD"
            )
        body = urllib.parse.urlencode(
            {"username": self.username, "password": self.password}
        ).encode("utf-8")
        request = urllib.request.Request(
            self._url("/login"),
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            response = self.opener.open(request, timeout=30)
        except (OSError, urllib.error.HTTPError) as exc:
            raise QueryError(f"远程登录失败：{exc}") from exc
        if response.geturl().rstrip("/").endswith("/login"):
            raise QueryError("远程登录失败：账号或密码不正确")

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        self.login()
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self._url(path),
            data=data,
            headers=headers,
            method=method.upper(),
        )
        try:
            with self.opener.open(request, timeout=120) as response:
                raw = response.read()
                content_type = response.headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise QueryError(f"远程接口返回 {exc.code}：{body[:800]}") from exc
        except OSError as exc:
            raise QueryError(f"远程接口不可用：{exc}") from exc
        if "json" in content_type.lower():
            return json.loads(raw.decode("utf-8"))
        return {"状态": "正常", "内容类型": content_type, "字节数": len(raw)}
