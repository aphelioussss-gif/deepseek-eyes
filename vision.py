"""DeepSeek Eyes — 豆包 Chat API 调用。

通过豆包 Vision API 分析图片，一次调用返回描述 + 坐标定位。
JSON 输出后处理：坐标 clamp、缺字段补默认值、非法类型降级。
"""

import base64
import json
import time
import urllib.request
import urllib.error

import config


VISION_SYSTEM_PROMPT = """你是一个精确的视觉分析器。看到图片后，用以下 JSON 结构输出分析结果：

{
  "描述": "图片整体内容的中文描述，包括类型、主题、关键信息",
  "文字转录": "逐字转录图片中所有可见文字，保持原文格式和换行",
  "元素列表": [
    {
      "类型": "标题|按钮|输入框|文本块|图标|菜单|代码块|图表|其他",
      "内容": "元素显示的文字或说明",
      "坐标": [x1, y1, x2, y2],
      "重要性": 1
    }
  ],
  "空间关系": "描述主要元素之间空间位置关系（上下左右、对齐、距离）",
  "辅助定位": "如果这是UI截图，指出用户最可能点击或关注的位置和坐标。注意：坐标为0-1000归一化，仅辅助定位，不映射真实屏幕坐标。"
}

坐标规则：
- 归一化坐标 0-1000，图片左上角(0,0)，右下角(1000,1000)
- 每个元素 bbox 精确框住可见边界
- 坐标格式 [x1, y1, x2, y2]
- 重要性：标题/核心按钮=1，次要文本=2，装饰元素=3

务必输出合法 JSON，不要添加 markdown 代码块标记。"""


def _safe_int(val, default=0, min_val=0, max_val=1000):
    try:
        v = int(val)
        return max(min_val, min(max_val, v))
    except (ValueError, TypeError):
        return default


def validate_and_normalize(raw: dict) -> dict:
    """防御式后处理：clamp 坐标、补默认值、降级。"""
    result = {
        "描述": str(raw.get("描述", "")),
        "文字转录": str(raw.get("文字转录", "")),
        "元素列表": [],
        "空间关系": str(raw.get("空间关系", "")),
        "辅助定位": str(raw.get("辅助定位", "")),
    }

    elements = raw.get("元素列表")
    if isinstance(elements, list):
        for elem in elements:
            if not isinstance(elem, dict):
                continue
            coords = elem.get("坐标", [])
            if isinstance(coords, list) and len(coords) == 4:
                clamped = [_safe_int(c) for c in coords]
            else:
                clamped = [0, 0, 0, 0]
            result["元素列表"].append({
                "类型": str(elem.get("类型", "未知")),
                "内容": str(elem.get("内容", "")),
                "坐标": clamped,
                "重要性": _safe_int(elem.get("重要性", 2), 2, 1, 3),
            })

    return result


def _call_api(base64_data: str, mime: str) -> dict:
    """调豆包 Chat API，返回 raw dict。"""
    body = {
        "model": config.VISION_MODEL,
        "messages": [
            {"role": "system", "content": VISION_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{base64_data}"},
                    },
                    {"type": "text", "text": "请分析这张图片"},
                ],
            },
        ],
        "max_tokens": 4096,
        "temperature": 0.0,
        "response_format": {"type": "json_object"},
    }

    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    url = f"{config.ARK_BASE_URL}/chat/completions"

    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.ARK_API_KEY}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=config.VISION_TIMEOUT) as resp:
            resp_body = resp.read()
    except urllib.error.HTTPError as e:
        status = e.code
        if status == 401 or status == 403:
            raise RuntimeError(f"豆包 API Key 无效 (HTTP {status})，请检查 ARK_API_KEY")
        if status == 429:
            raise RuntimeError(f"豆包 API 频率限制 (HTTP 429)，请稍后重试")
        raise RuntimeError(f"豆包 API 错误 (HTTP {status}): {e.reason}")
    except Exception as e:
        raise RuntimeError(f"豆包 API 网络错误: {e}")

    try:
        result = json.loads(resp_body)
    except json.JSONDecodeError:
        raise RuntimeError("豆包返回了非 JSON 响应")

    choice = result.get("choices", [{}])[0]
    content = choice.get("message", {}).get("content", "")
    return content


def analyze_image(raw_bytes: bytes, mime: str) -> dict:
    """分析图片，返回结构化描述结果。

    重试策略：429/5xx 重试 1 次，401 不重试。
    """
    base64_data = base64.b64encode(raw_bytes).decode("ascii")

    last_error = None
    for attempt in range(2):
        try:
            content = _call_api(base64_data, mime)
            if isinstance(content, str):
                try:
                    parsed = json.loads(content)
                except json.JSONDecodeError:
                    # JSON 解析失败，降级为纯文本
                    return {
                        "描述": content,
                        "文字转录": "",
                        "元素列表": [],
                        "空间关系": "",
                        "辅助定位": "",
                    }
            elif isinstance(content, dict):
                parsed = content
            else:
                raise RuntimeError(f"豆包返回了意外的响应类型: {type(content)}")

            return validate_and_normalize(parsed)

        except RuntimeError as e:
            last_error = e
            msg = str(e)
            # 401/403 不重试
            if "HTTP 401" in msg or "HTTP 403" in msg:
                break
            # 429/5xx/网络错误 重试 1 次
            if attempt == 0:
                time.sleep(1)
                continue
            break

    raise last_error or RuntimeError("豆包 API 调用失败，未知错误")
