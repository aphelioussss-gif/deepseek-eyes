#!/usr/bin/env python3
"""DeepSeek Eyes — HTTP 代理服务。

在 Claude Code 和 DeepSeek 之间拦截 Anthropic /v1/messages 请求，
将 image block 替换为豆包视觉分析文本，转发给 DeepSeek。

启动:
  DEBUG + FAKE:  DEEPSEEK_EYES_DEBUG=1 DEEPSEEK_EYES_FAKE_VISION=1 python3 proxy.py
  PRODUCTION:    python3 proxy.py  (需 .env 中 ARK_API_KEY)
"""

import http.client
import json
import os
import sys
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import config

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from transformer import BlockNotSupportedError, ImageSourceError, scan_content
from cache import get_or_compute
from vision_fake import analyze_image as fake_analyze


def _real_analyze(raw_bytes, mime):
    """调真实豆包 API（延迟 import，避免离线模式因缺依赖崩溃）。"""
    from vision import analyze_image as real_analyze_fn
    return real_analyze_fn(raw_bytes, mime)


def _vision_compute_fn(raw_bytes, mime):
    if config.FAKE_VISION:
        return fake_analyze(raw_bytes, mime)
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


class ProxyHandler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        pass

    # ── body reading ──────────────────────────────────────

    def _read_body(self) -> bytes | None:
        cl = self.headers.get("Content-Length")
        if not cl:
            self._send_error(400, "缺少 Content-Length"); return None
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

    def _forward_to_deepseek(self, request_id: str, body_str: str, auth: dict, t0: float):
        """转发请求到 DeepSeek，处理 SSE / 非流式。"""
        body_bytes = body_str.encode("utf-8")

        out_headers = {
            "Content-Type": "application/json",
            "Anthropic-Version": "2023-06-01",
        }
        for k, v in auth.items():
            out_headers[k] = v

        conn = http.client.HTTPSConnection(
            config.DEEPSEEK_HOST, timeout=600
        )
        try:
            conn.request(
                "POST",
                config.DEEPSEEK_PATH,
                body=body_bytes,
                headers=out_headers,
            )
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

        # 构造响应 headers，过滤 hop-by-hop
        response_headers = []
        for k, v in resp.getheaders():
            lk = k.lower()
            if lk in ("transfer-encoding", "connection", "content-length"):
                continue
            response_headers.append((k, v))
        if not is_sse:
            response_headers.append(("Content-Length", resp.getheader("Content-Length", "0")))

        self.send_response(upstream_status)
        for k, v in response_headers:
            self.send_header(k, v)
        self.end_headers()

        client_closed = False

        try:
            if is_sse:
                # SSE: read(8192) 循环 + flush
                while True:
                    chunk = resp.read(8192)
                    if not chunk:
                        break
                    try:
                        self.wfile.write(chunk)
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        client_closed = True
                        break
            else:
                # 非流式：直接读完整 body
                full_body = resp.read()
                try:
                    self.wfile.write(full_body)
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
        _log(
            "info",
            request_id,
            upstream_status=str(upstream_status),
            sse=str(is_sse).lower(),
            client_closed=str(client_closed).lower(),
            duration_ms=str(duration_ms),
        )

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

        body_bytes = self._read_body()
        if body_bytes is None:
            return

        try:
            parsed = json.loads(body_bytes)
        except json.JSONDecodeError as e:
            self._send_error(400, f"JSON 解析失败: {e}"); return

        # ── 图片扫描与替换 ──
        try:
            total_count = 0
            for msg in parsed.get("messages", []):
                content = msg.get("content")
                if content is not None:
                    new_content, count = scan_content(content, vision_fn=_process_image)
                    msg["content"] = new_content
                    total_count += count
            image_count = total_count
        except ImageSourceError as e:
            _log("warn", request_id, error="image_source", detail=e.message)
            self._send_error(e.status_code, e.message); return
        except BlockNotSupportedError as e:
            _log("warn", request_id, error="unsupported_block", detail=e.message)
            self._send_error(e.status_code, e.message); return
        except Exception as e:
            _log("error", request_id, error="transform", detail=str(e))
            self._send_error(500, f"图片处理异常: {e}"); return

        transformed_body = json.dumps(parsed, ensure_ascii=False)

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

        self._forward_to_deepseek(request_id, transformed_body, auth, t0)

    # ── routing ───────────────────────────────────────────

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/health":
            self.handle_health()
        elif path == "/debug/transform" and config.DEBUG_MODE:
            self.handle_debug_transform()
        else:
            self._send_error(404, f"未知路径: {self.path}")

    def do_POST(self):
        path = self.path.split("?")[0]
        if path == "/v1/messages":
            self.handle_messages()
        else:
            self._send_error(404, f"未知路径: {self.path}")


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
    if config.FAKE_VISION:
        mode.append("FAKE_VISION")
    mode_str = "+".join(mode) if mode else "PRODUCTION"

    server = ThreadingHTTPServer((config.PROXY_HOST, config.PROXY_PORT), ProxyHandler)
    print(
        f"[deepseek-eyes] http://{config.PROXY_HOST}:{config.PROXY_PORT} mode={mode_str}",
        file=sys.stderr,
    )
    dbg_endpoint = " /debug/transform" if config.DEBUG_MODE else ""
    print(f"[deepseek-eyes] 端点: /health /v1/messages{dbg_endpoint}", file=sys.stderr)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[deepseek-eyes] 停止", file=sys.stderr)
        server.shutdown()


if __name__ == "__main__":
    main()
