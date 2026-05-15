"""DeepSeek Eyes — 线程安全图片缓存。

- 内存 dict + 可选磁盘持久化（默认关闭，含隐私数据）
- Length-prefixed hash 消除拼接歧义
- Per-key in-flight lock: 并发同图只调一次 vision
- Atomic disk write (os.replace)
"""

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import config

_cache: dict = {}
_cache_lock = threading.RLock()
_inflight: dict[str, threading.Event] = {}
_inflight_results: dict[str, dict] = {}

_cache_path: Optional[Path] = None
if config.CACHE_PERSIST_ENABLED:
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_path = config.CACHE_DIR / "cache.json"
    os.chmod(config.CACHE_DIR, config.CACHE_FILE_MODE)


def _load_from_disk():
    if not _cache_path or not _cache_path.exists():
        return
    try:
        with open(_cache_path, "r") as f:
            data = json.load(f)
        now = time.time()
        ttl = config.CACHE_TTL_HOURS * 3600
        with _cache_lock:
            for key, entry in data.items():
                if now - entry.get("created_at", 0) < ttl:
                    _cache[key] = entry
    except Exception:
        pass


def _persist_to_disk():
    if not _cache_path:
        return
    try:
        with _cache_lock:
            data = dict(_cache)
        tmp_path = str(_cache_path) + ".tmp"
        with open(tmp_path, "w") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp_path, str(_cache_path))
        os.chmod(str(_cache_path), config.CACHE_FILE_MODE)
    except Exception:
        pass


# 启动时加载
_load_from_disk()


def _cache_key(raw_bytes: bytes, mime: str, model: str, prompt_version: str) -> str:
    """Length-prefixed hash，消除拼接歧义。

    sha256(
        b"deepseek-eyes-v1\0"
        + len(raw_bytes).to_bytes(4, "big") + raw_bytes
        + len(mime).to_bytes(2, "big") + mime.encode()
        + len(model).to_bytes(2, "big") + model.encode()
        + len(prompt_version).to_bytes(2, "big") + prompt_version.encode()
    )
    """
    h = hashlib.sha256()
    h.update(b"deepseek-eyes-v1\0")
    h.update(len(raw_bytes).to_bytes(4, "big"))
    h.update(raw_bytes)
    h.update(len(mime).to_bytes(2, "big"))
    h.update(mime.encode())
    h.update(len(model).to_bytes(2, "big"))
    h.update(model.encode())
    h.update(len(prompt_version).to_bytes(2, "big"))
    h.update(prompt_version.encode())
    return h.hexdigest()


def _hash_prefix(full_hash: str) -> str:
    return full_hash[:8]


def get_or_compute(
    raw_bytes: bytes,
    mime: str,
    compute_fn: Callable[[], dict],
) -> tuple:
    """获取缓存或计算。同一 key 的并发请求只调一次 compute_fn。

    Returns:
        (result_dict, cache_hit: bool)
    """
    key = _cache_key(raw_bytes, mime, config.VISION_MODEL, config.VISION_PROMPT_VERSION)

    with _cache_lock:
        if key in _cache:
            return _cache[key]["result"], True

        if key in _inflight:
            event = _inflight[key]
            owner = False
        else:
            event = threading.Event()
            _inflight[key] = event
            owner = True

    if not owner:
        event.wait()
        with _cache_lock:
            if key in _cache:
                return _cache[key]["result"], True
            if key in _inflight_results:
                return _inflight_results[key], True
            raise RuntimeError("in-flight vision failed, no result available")

    # Owner: 负责计算
    try:
        result = compute_fn()
        entry = {
            "result": result,
            "created_at": time.time(),
            "prompt_version": config.VISION_PROMPT_VERSION,
            "model": config.VISION_MODEL,
            "image_bytes_len": len(raw_bytes),
            "mime": mime,
        }
        with _cache_lock:
            _cache[key] = entry
            _inflight_results[key] = result
            # 超过上限时淘汰最旧的
            if len(_cache) > config.CACHE_MAX_ENTRIES:
                oldest = min(_cache.items(), key=lambda x: x[1]["created_at"])
                del _cache[oldest[0]]
        if config.CACHE_PERSIST_ENABLED:
            _persist_to_disk()
        return result, False
    finally:
        with _cache_lock:
            _inflight.pop(key, None)
            _inflight_results.pop(key, None)
            event.set()
