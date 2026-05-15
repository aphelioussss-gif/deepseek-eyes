"""DeepSeek Eyes — 配置加载。

加载顺序：环境变量 > .env 文件 > 默认值。
launchd 不继承 shell 环境，必须从项目目录稳定加载 .env。
"""

import os
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
PROJECT_VERSION = "v1.2-lite"


def _load_dotenv():
    """加载项目目录下的 .env 文件（不依赖 python-dotenv）。"""
    env_path = PROJECT_DIR / ".env"
    if not env_path.exists():
        return
    with open(env_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


_load_dotenv()


def _env_str(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, ""))
    except (ValueError, TypeError):
        return default


def _env_bool(key: str, default: bool = False) -> bool:
    val = os.environ.get(key, "").lower()
    if val in ("1", "true", "yes", "on"):
        return True
    if val in ("0", "false", "no", "off"):
        return False
    return default


def _normalize_vision_model(model: str, base_url: str) -> str:
    """Normalize common Doubao 2.0 Lite aliases for the selected Ark API surface."""
    normalized = model.strip()
    key = normalized.lower()
    base = base_url.rstrip("/")

    if base.endswith("/api/v3"):
        online_aliases = {
            "doubao-seed-2.0-lite": "doubao-seed-2-0-lite-260215",
            "doubao-seed-2-0-lite": "doubao-seed-2-0-lite-260215",
            "doubao-seed-2-0-lite-260215": "doubao-seed-2-0-lite-260215",
        }
        return online_aliases.get(key, normalized)

    if base.endswith("/api/coding/v3"):
        coding_aliases = {
            "doubao-seed-2-0-lite": "doubao-seed-2.0-lite",
            "doubao-seed-2-0-lite-260215": "doubao-seed-2.0-lite",
            "doubao-seed-2.0-lite": "doubao-seed-2.0-lite",
        }
        return coding_aliases.get(key, normalized)

    return normalized


# === 豆包（视觉）===
ARK_API_KEY = _env_str("ARK_API_KEY")
ARK_BASE_URL = _env_str("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3").rstrip("/")
VISION_MODEL_RAW = _env_str("VISION_MODEL", "doubao-seed-2-0-lite-260215")
VISION_MODEL = _normalize_vision_model(VISION_MODEL_RAW, ARK_BASE_URL)
VISION_PROMPT_VERSION = "v1"
VISION_TIMEOUT = _env_int("VISION_TIMEOUT", 120)
VISION_FAIL_MODE = _env_str("VISION_FAIL_MODE", "placeholder")  # placeholder | block

# === 代理 ===
PROXY_HOST = "127.0.0.1"
PROXY_PORT = _env_int("PROXY_PORT", 8788)

# === 缓存 ===
CACHE_PERSIST_ENABLED = _env_bool("CACHE_PERSIST_ENABLED", False)
CACHE_TTL_HOURS = _env_int("CACHE_TTL_HOURS", 168)
CACHE_MAX_ENTRIES = _env_int("CACHE_MAX_ENTRIES", 500)
CACHE_DIR = PROJECT_DIR / ".cache"
CACHE_FILE_MODE = 0o600

# === 安全 ===
MAX_IMAGE_RAW_BYTES = _env_int("MAX_IMAGE_RAW_BYTES", 10 * 1024 * 1024)
MAX_REQUEST_BODY_BYTES = _env_int("MAX_REQUEST_BODY_BYTES", 32 * 1024 * 1024)

# === 调试 ===
DEBUG_MODE = _env_bool("DEEPSEEK_EYES_DEBUG", False)
FAKE_VISION = _env_bool("DEEPSEEK_EYES_FAKE_VISION", False)

# === DeepSeek（文本）===
DEEPSEEK_HOST = "api.deepseek.com"
DEEPSEEK_PATH = "/anthropic/v1/messages"

# === 日志 ===
LOG_FILE = _env_str("DEEPSEEK_EYES_LOG", "")


def validate():
    """检查配置完整性。返回 (ok, errors)。"""
    errors = []
    if not FAKE_VISION and not ARK_API_KEY:
        errors.append("ARK_API_KEY 未设置（真实视觉模式需要）。或设置 DEEPSEEK_EYES_FAKE_VISION=1")
    if VISION_FAIL_MODE not in ("placeholder", "block"):
        errors.append(f"VISION_FAIL_MODE 无效值: {VISION_FAIL_MODE}")
    if ARK_BASE_URL.endswith("/api/v3") and VISION_MODEL_RAW.lower() == "doubao-seed-2.0-lite":
        print(
            "[deepseek-eyes] config notice: VISION_MODEL=doubao-seed-2.0-lite "
            "已按在线推理 /api/v3 自动改用 doubao-seed-2-0-lite-260215",
            file=sys.stderr,
        )
    if ARK_BASE_URL.endswith("/api/coding/v3") and VISION_MODEL_RAW.lower() == "doubao-seed-2-0-lite-260215":
        print(
            "[deepseek-eyes] config notice: VISION_MODEL=doubao-seed-2-0-lite-260215 "
            "已按 Coding Plan /api/coding/v3 自动改用 doubao-seed-2.0-lite",
            file=sys.stderr,
        )
    return len(errors) == 0, errors
