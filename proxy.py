#!/usr/bin/env python3
"""DeepSeek Eyes — HTTP 代理服务。

在 Claude Code 和 DeepSeek 之间拦截 Anthropic /v1/messages 请求，
将 image block 替换为豆包视觉分析文本，转发给 DeepSeek。
其他所有请求透明转发。

启动:
  PRODUCTION:    python3 proxy.py  (需 .env 中 ARK_API_KEY)
"""

import http.client
import json
import os
import sys
import time
import uuid
from typing import Iterable, Optional
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import config

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from transformer import ImageSourceError, scan_content
from cache import get_or_compute
def _real_analyze(raw_bytes, mime):
    """调真实豆包 API（延迟 import，避免离线模式因缺依赖崩溃）。"""
    from vision import analyze_image as real_analyze_fn
    return real_analyze_fn(raw_bytes, mime)


def _vision_compute_fn(raw_bytes, mime):
    return _real_analyze(raw_bytes, mime)


def _process_image(raw_bytes: bytes, mime: str) -> str:
    """处理单张图片：调视觉 API（或 fake），缓存结果，返回文字描述。"""
    result, cache_hit = get_or_compute(
        raw_bytes=raw_bytes,
        mime=mime,
        compute_fn=lambda: _vision_compute_fn(raw_bytes, mime),
    )
    return _format_vision_result(result)


def _format_vision_result(result: dict) -> str:
    """将 vision 结果 dict 格式化为注入 DeepSeek 的文本块。"""
    parts = []
    parts.append(f"描述: {result.get('描述', '')}")
    parts.append(f"文字转录: {result.get('文字转录', '')}")

    elements = result.get("元素列表", [])
    if elements:
        parts.append(f"元素坐标 (归一化 0-1000, {len(elements)}个元素):")
        for i, elem in enumerate(elements, 1):
            coords = elem.get("坐标", [0, 0, 0, 0])
            parts.append(
                f"  {i}. [{elem.get('类型', '?')}] \"{elem.get('内容', '')}\" "
                f"bbox[{coords[0]},{coords[1]},{coords[2]},{coords[3]}] "
                f"重要性={elem.get('重要性', 2)}"
            )

    parts.append(f"空间关系: {result.get('空间关系', '')}")
    parts.append(f"辅助定位: {result.get('辅助定位', '')}")
    return "\n".join(parts)


def _log(level: str, request_id: str, **kwargs):
    """结构化日志。只记元数据，不记 prompt/描述/OCR/Key。"""
    parts = [f"[deepseek-eyes] {level} request_id={request_id}"]
    for k, v in sorted(kwargs.items()):
        parts.append(f"{k}={v}")
    msg = " ".join(parts)
    print(msg, file=sys.stderr, flush=True)
    if config.LOG_FILE:
        try:
            with open(config.LOG_FILE, "a") as f:
                f.write(msg + "\n")
        except Exception:
            pass


def _sse_event_is_terminal(lines: Iterable[bytes]) -> bool:
    """Return True when an SSE event is the final model event.

    Detects terminal signals across three formats:

    * Anthropic SSE: ``event: message_stop``
    * OpenAI SSE: ``data: [DONE]``
    * JSON payload: ``{"type":"message_stop"}``, ``{"type":"message_delta",
      "delta":{"stop_reason":"..."}}``, or top-level ``{"stop_reason":"..."}``

    The JSON-path detection handles DeepSeek's Anthropic-compatible endpoint,
    which may embed stop_reason inside a message_delta event rather than
    emitting an explicit message_stop event.
    """
    for raw_line in lines:
        line = raw_line.strip()
        if line == b"event: message_stop":
            return True
        if line == b"data: [DONE]":
            return True
        if line.startswith(b"data: "):
            json_str = line[len(b"data: "):]
            try:
                payload = json.loads(json_str)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(payload, dict):
                if payload.get("type") == "message_stop":
                    return True
                if payload.get("stop_reason"):
                    return True
                delta = payload.get("delta")
                if isinstance(delta, dict) and delta.get("stop_reason"):
                    return True
    return False


def _forward_sse_stream(resp, wfile, conn=None) -> tuple:
    """Forward an SSE stream and stop at the logical terminal event.

    Returns (client_closed, end_reason) where end_reason is one of:
      terminal_event  — recognised terminal SSE event
      idle_timeout    — no data on the socket for SSE_IDLE_TIMEOUT seconds
      connection_closed — upstream closed the connection (readline returned empty)

    When conn is provided and SSE_IDLE_TIMEOUT > 0, sets a read timeout on the
    underlying socket so that a stalled stream is terminated cleanly instead of
    blocking for up to the connection-level 600 s timeout.
    """
    import socket

    if conn is not None and config.SSE_IDLE_TIMEOUT > 0:
        try:
            conn.sock.settimeout(config.SSE_IDLE_TIMEOUT)
        except Exception:
            pass

    event_lines = []
    end_reason = "connection_closed"
    while True:
        try:
            line = resp.readline()
        except socket.timeout:
            end_reason = "idle_timeout"
            break
        if not line:
            break
        event_lines.append(line)
        try:
            wfile.write(line)
            wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return True, "client_closed"
        if line in (b"\n", b"\r\n"):
            if _sse_event_is_terminal(event_lines):
                end_reason = "terminal_event"
                break
            event_lines = []
    return False, end_reason


def _prepare_upstream_body(body_bytes: bytes, vision_fn=_process_image) -> tuple:
    """Return (upstream_body_bytes, parsed_request, image_count).

    The no-image path is intentionally byte-for-byte pass-through. We parse only
    to detect direct Anthropic image blocks under messages[].content; if none are
    present, the original request body is forwarded unchanged.
    """
    parsed = json.loads(body_bytes)
    total_count = 0

    if not isinstance(parsed, dict):
        return body_bytes, parsed, 0

    messages = parsed.get("messages", [])
    if isinstance(messages, list):
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            content = msg.get("content")
            if content is None:
                continue
            new_content, count = scan_content(content, vision_fn=vision_fn)
            if count:
                msg["content"] = new_content
                total_count += count

    if total_count == 0:
        return body_bytes, parsed, 0

    transformed_body = json.dumps(parsed, ensure_ascii=False).encode("utf-8")
    return transformed_body, parsed, total_count


def _build_upstream_path(path: str) -> str:
    """给所有上游请求统一加 /anthropic 前缀。"""
    return "/anthropic" + path


def _build_upstream_headers(inbound_headers, auth: dict) -> dict:
    """Build upstream request headers while preserving Anthropic/tool headers.

    We keep the inbound request as transparent as possible, but still drop
    hop-by-hop headers and force a JSON content type for the request body.
    """
    hop_by_hop = {
        "connection",
        "content-length",
        "host",
        "keep-alive",
        "proxy-connection",
        "transfer-encoding",
        "upgrade",
        "accept-encoding",
    }

    out_headers = {}
    for k, v in inbound_headers.items():
        if k.lower() in hop_by_hop:
            continue
        out_headers[k] = v

    out_headers["Content-Type"] = inbound_headers.get("Content-Type", "application/json")
    out_headers.setdefault("Anthropic-Version", inbound_headers.get("Anthropic-Version", "2023-06-01"))

    for k, v in auth.items():
        out_headers[k] = v

    return out_headers


class ProxyHandler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        pass

    # ── body reading ──────────────────────────────────────

    def _read_body(self) -> Optional[bytes]:
        cl = self.headers.get("Content-Length")
        if not cl:
            return b""
        try:
            cl = int(cl)
        except ValueError:
            self._send_error(400, "Content-Length 不是有效数字"); return None
        if cl < 0:
            self._send_error(400, "Content-Length 不能为负"); return None
        if cl > config.MAX_REQUEST_BODY_BYTES:
            self._send_error(413, f"请求体过大: {cl} bytes (最大 {config.MAX_REQUEST_BODY_BYTES})")
            return None
        try:
            return self.rfile.read(cl)
        except Exception as e:
            self._send_error(400, f"读取请求体失败: {e}"); return None

    # ── response helpers ──────────────────────────────────

    def _send_error(self, status: int, message: str):
        body_str = json.dumps({"error": {"type": "proxy_error", "message": message}}, ensure_ascii=False)
        body_bytes = body_str.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body_bytes)))
        self.end_headers()
        self.wfile.write(body_bytes)

    def _send_json(self, status: int, data: dict):
        body_str = json.dumps(data, ensure_ascii=False)
        body_bytes = body_str.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body_bytes)))
        self.end_headers()
        self.wfile.write(body_bytes)

    # ── auth ──────────────────────────────────────────────

    def _forward_auth_headers(self) -> dict:
        h = {}
        x_api_key = self.headers.get("x-api-key")
        if x_api_key:
            h["x-api-key"] = x_api_key
        authorization = self.headers.get("Authorization")
        if authorization:
            h["Authorization"] = authorization
        return h

    # ── upstream forwarding ───────────────────────────────

    def _forward_upstream(self, request_id: str, method: str, path: str,
                          body_bytes: bytes, auth: dict, t0: float):
        """通用上游转发。支持任意 HTTP 方法和路径，透明转发所有响应。

        修复了 Content-Length fallback 为 "0" 的问题：非 SSE 响应先读完
        upstream body，用实际长度设 Content-Length，再回传客户端。
        """
        out_headers = _build_upstream_headers(self.headers, auth)

        conn = http.client.HTTPSConnection(
            config.DEEPSEEK_HOST, timeout=600
        )
        try:
            conn.request(method, path, body=body_bytes, headers=out_headers)
            resp = conn.getresponse()
        except Exception as e:
            conn.close()
            duration_ms = int((time.time() - t0) * 1000)
            _log("error", request_id, error="deepseek_connect", detail=str(e), duration_ms=str(duration_ms))
            self._send_error(502, f"DeepSeek 连接失败: {e}")
            return

        upstream_status = resp.status
        upstream_ct = resp.getheader("Content-Type", "")
        is_sse = "text/event-stream" in upstream_ct.lower()

        # 收集响应 headers，过滤 hop-by-hop，Content-Length 稍后统一处理
        response_headers = []
        for k, v in resp.getheaders():
            lk = k.lower()
            if lk in ("transfer-encoding", "connection", "content-length"):
                continue
            response_headers.append((k, v))

        client_closed = False
        end_reason = ""

        try:
            if is_sse:
                self.send_response(upstream_status)
                for k, v in response_headers:
                    self.send_header(k, v)
                self.end_headers()
                client_closed, end_reason = _forward_sse_stream(resp, self.wfile, conn)
            else:
                # 先读完 upstream body，再用实际长度设 Content-Length
                full_body = resp.read()
                self.send_response(upstream_status)
                for k, v in response_headers:
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(full_body)))
                self.end_headers()
                try:
                    self.wfile.write(full_body)
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    client_closed = True
        finally:
            if client_closed:
                try:
                    conn.close()
                except Exception:
                    pass
            else:
                conn.close()

        duration_ms = int((time.time() - t0) * 1000)
        log_kwargs = dict(
            method=method,
            upstream_path=path,
            upstream_status=str(upstream_status),
            sse=str(is_sse).lower(),
            client_closed=str(client_closed).lower(),
            duration_ms=str(duration_ms),
        )
        if end_reason:
            log_kwargs["sse_end_reason"] = end_reason
        _log("info", request_id, **log_kwargs)

    def _transparent_forward(self, method: str):
        """透明转发：不解析 body，原样传给 DeepSeek。

        所有非 /v1/messages 的请求走这条路。
        """
        request_id = uuid.uuid4().hex[:12]
        t0 = time.time()

        body_bytes = b""
        if method in ("POST", "PUT", "PATCH"):
            body_bytes = self._read_body()
            if body_bytes is None:
                return

        auth = self._forward_auth_headers()
        if not auth:
            _log("warn", request_id, error="no_auth")
            self._send_error(401, "缺少认证 header (x-api-key 或 Authorization)")
            return

        upstream_path = _build_upstream_path(self.path.split("?")[0])
        self._forward_upstream(request_id, method, upstream_path, body_bytes, auth, t0)

    # ── endpoint handlers ─────────────────────────────────

    def handle_health(self):
        uptime = int(time.time() - start_time)
        self._send_json(200, {"status": "ok", "uptime": uptime})

    def handle_debug_transform(self):
        if not config.DEBUG_MODE:
            self._send_error(404, "DEBUG 模式未开启"); return
        global last_transform
        self._send_json(200, last_transform or {"message": "尚无请求"})

    def handle_messages(self):
        request_id = uuid.uuid4().hex[:12]
        t0 = time.time()
        image_count = 0
        image_details = []
        _log(
            "info",
            request_id,
            event="received",
            path="/v1/messages",
            content_length=self.headers.get("Content-Length", ""),
        )

        body_bytes = self._read_body()
        if body_bytes is None:
            return

        # ── 图片扫描与替换 ──
        try:
            transformed_body, parsed, image_count = _prepare_upstream_body(body_bytes)
        except json.JSONDecodeError as e:
            self._send_error(400, f"JSON 解析失败: {e}"); return
        except ImageSourceError as e:
            _log("warn", request_id, error="image_source", detail=e.message)
            self._send_error(e.status_code, e.message); return
        except Exception as e:
            _log("error", request_id, error="transform", detail=str(e))
            self._send_error(500, f"图片处理异常: {e}"); return

        if config.DEBUG_MODE:
            global last_transform
            auth = self._forward_auth_headers()
            duration_ms = int((time.time() - t0) * 1000)
            last_transform = {
                "transformed_request": parsed,
                "image_count": image_count,
                "images": image_details,
                "forward_auth": auth,
                "inbound_x_api_key": self.headers.get("x-api-key", ""),
                "inbound_authorization": self.headers.get("Authorization", ""),
                "request_id": request_id,
                "duration_ms": duration_ms,
            }
            _log("info", request_id, has_image=str(image_count > 0).lower(),
                 image_count=str(image_count), duration_ms=str(duration_ms), debug="1")
            self._send_json(200, last_transform)
            return

        # ── 转发 DeepSeek ──
        auth = self._forward_auth_headers()
        if not auth:
            _log("warn", request_id, error="no_auth")
            self._send_error(401, "缺少认证 header (x-api-key 或 Authorization)")
            return

        self._forward_upstream(request_id, "POST", config.DEEPSEEK_PATH,
                               transformed_body, auth, t0)

    # ── routing ───────────────────────────────────────────

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/health":
            self.handle_health()
        elif path == "/debug/transform" and config.DEBUG_MODE:
            self.handle_debug_transform()
        else:
            self._transparent_forward("GET")

    def do_POST(self):
        path = self.path.split("?")[0]
        if path == "/v1/messages":
            self.handle_messages()
        else:
            self._transparent_forward("POST")

    def do_PUT(self):
        self._transparent_forward("PUT")

    def do_DELETE(self):
        self._transparent_forward("DELETE")

    def do_PATCH(self):
        self._transparent_forward("PATCH")

    def do_OPTIONS(self):
        self._transparent_forward("OPTIONS")


# ── 全局 ────────────────────────────────────────────

start_time = time.time()
last_transform = None


def main():
    ok, errors = config.validate()
    if not ok:
        for e in errors:
            print(f"[deepseek-eyes] config warning: {e}", file=sys.stderr)

    mode = []
    if config.DEBUG_MODE:
        mode.append("DEBUG")
    mode_str = "+".join(mode) if mode else "PRODUCTION"

    server = ThreadingHTTPServer((config.PROXY_HOST, config.PROXY_PORT), ProxyHandler)
    print(
        f"[deepseek-eyes] http://{config.PROXY_HOST}:{config.PROXY_PORT} "
        f"version={config.PROJECT_VERSION} mode={mode_str}",
        file=sys.stderr,
    )
    dbg_endpoint = " /debug/transform" if config.DEBUG_MODE else ""
    print(f"[deepseek-eyes] 端点: /health /v1/messages{dbg_endpoint}（其他全部透传）", file=sys.stderr)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[deepseek-eyes] 停止", file=sys.stderr)
        server.shutdown()


if __name__ == "__main__":
    main()
