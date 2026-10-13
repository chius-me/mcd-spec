"""Minimal Streamable HTTP client for the McDonald's MCP server."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Protocol

from mcd_spec import __version__


class McpError(RuntimeError):
    """The MCP call failed before a usable tool payload came back."""


class Transport(Protocol):
    def post(self, url: str, headers: dict[str, str], body: bytes) -> tuple[int, dict[str, str], bytes]:
        """Return HTTP status, lower-cased headers, and the raw body."""


class UrllibTransport:
    def __init__(self, timeout: float = 30):
        self.timeout = timeout

    def post(self, url: str, headers: dict[str, str], body: bytes) -> tuple[int, dict[str, str], bytes]:
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                header_map = {key.lower(): value for key, value in response.headers.items()}
                return response.status, header_map, response.read()
        except urllib.error.HTTPError as exc:
            header_map = {key.lower(): value for key, value in exc.headers.items()}
            return exc.code, header_map, exc.read()


class McpClient:
    def __init__(self, url: str, token: str, transport: Transport | None = None):
        if not token or not str(token).strip():
            raise McpError("缺少 MCD_MCP_TOKEN")
        self.url = url.rstrip("/")
        self._token = str(token).strip()
        self.transport = transport or UrllibTransport()
        self.session_id: str | None = None
        self._next_id = 0
        self._ready = False

    def call_tool(self, name: str, arguments: dict | None = None):
        self._ensure_session()
        message = self._rpc(
            "tools/call",
            {"name": name, "arguments": arguments or {}},
            allow_empty=False,
        )
        return unwrap_tool_result(message)

    def _ensure_session(self) -> None:
        if self._ready:
            return
        self._rpc(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "mcd-spec", "version": __version__},
            },
            allow_empty=False,
        )
        self._rpc_notification("notifications/initialized")
        self._ready = True

    def _rpc_notification(self, method: str) -> None:
        self._post({"jsonrpc": "2.0", "method": method}, allow_empty=True)

    def _rpc(self, method: str, params: dict, *, allow_empty: bool) -> dict:
        self._next_id += 1
        message = self._post(
            {"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": params},
            allow_empty=allow_empty,
        )
        if message is None:
            raise McpError(f"{method} 没有返回内容")
        if message.get("error"):
            error = message["error"]
            detail = error.get("message") if isinstance(error, dict) else str(error)
            raise McpError(detail or f"{method} 失败")
        return message

    def _post(self, payload: dict, *, allow_empty: bool) -> dict | None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {self._token}",
            "MCP-Protocol-Version": "2025-06-18",
            "User-Agent": f"mcd-spec/{__version__}",
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        status, response_headers, raw = self.transport.post(self.url, headers, body)
        session = response_headers.get("mcp-session-id")
        if session and not self.session_id:
            self.session_id = session
        if status == 401:
            raise McpError("MCP Token 无效、已过期或未提供")
        if status == 429:
            raise McpError("请求过于频繁，麦当劳 MCP 每分钟限制 600 次")
        if status >= 400:
            raise McpError(f"MCP 请求失败（HTTP {status}）")
        if not raw or not raw.strip():
            if allow_empty:
                return None
            raise McpError("MCP 返回空响应")
        return parse_message(response_headers.get("content-type", ""), raw)


def parse_message(content_type: str, raw: bytes) -> dict:
    text = raw.decode("utf-8").strip()
    if "text/event-stream" in content_type or text.startswith("event:") or text.startswith("data:"):
        messages = parse_sse(text)
        if not messages:
            raise McpError("SSE 响应里没有 JSON 数据")
        return messages[-1]
    if text.startswith("{") or text.startswith("["):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise McpError("MCP 返回的 JSON 无法解析") from exc
        if isinstance(parsed, dict):
            return parsed
        raise McpError("MCP 返回的 JSON 不是对象")
    raise McpError("无法识别 MCP 响应")


def parse_sse(text: str) -> list[dict]:
    messages = []
    for block in text.split("\n\n"):
        data_lines = []
        for line in block.splitlines():
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
        if not data_lines:
            continue
        raw = "\n".join(data_lines).strip()
        if not raw or raw == "[DONE]":
            continue
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise McpError("SSE 数据不是 JSON") from exc
        if isinstance(parsed, dict):
            messages.append(parsed)
    return messages


def unwrap_tool_result(message: dict):
    if not isinstance(message, dict):
        raise McpError("工具返回无法读取")
    if message.get("error"):
        error = message["error"]
        detail = error.get("message") if isinstance(error, dict) else str(error)
        raise McpError(detail or "工具调用失败")
    result = message.get("result", message)
    if not isinstance(result, dict):
        raise McpError("工具返回缺少 result")
    if result.get("isError"):
        text = _text_from_result(result)
        raise McpError(text or "工具调用失败")
    text = _text_from_result(result)
    if text:
        parsed = _try_json(text)
        if parsed is not None:
            return unwrap_business(parsed)
        return text
    if "success" in result or "data" in result:
        return unwrap_business(result)
    return result


def unwrap_business(payload):
    if isinstance(payload, dict) and "success" in payload:
        if not payload.get("success"):
            raise McpError(str(payload.get("message") or "麦当劳接口返回失败"))
        if "data" in payload:
            return payload.get("data")
    return payload


def _text_from_result(result: dict) -> str:
    content = result.get("content")
    if not isinstance(content, list):
        return ""
    parts = []
    for item in content:
        if isinstance(item, dict) and item.get("type", "text") == "text":
            parts.append(str(item.get("text") or ""))
    return "\n".join(part for part in parts if part).strip()


def _try_json(text: str):
    stripped = text.strip()
    if not stripped or stripped[0] not in "{[":
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return None
