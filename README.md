# DeepSeek Eyes

给 DeepSeek + Claude Code 加视觉能力。本地 HTTP 代理拦截 Anthropic 请求中的图片，通过豆包 Chat API 将图片转为文字描述（含坐标定位），再转发给 DeepSeek。

## 原理

```
Claude Code ──→ 127.0.0.1:8788 (DeepSeek Eyes) ──→ DeepSeek API
                    │
                    ├── 检测图片 → 豆包视觉 API → 文字描述注入请求
                    └── 无图片   → byte-for-byte 透传
```

## 快速开始

### 1. 获取 API Key

- 注册[火山引擎 ARK](https://www.volcengine.com/product/ark)，开通豆包视觉模型
- 获取 API Key（格式 `ark-xxx`）

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 配置

```bash
cp .env.example .env
# 编辑 .env，填入 ARK_API_KEY=ark-xxx
```

### 4. 离线测试（不调真实 API）

```bash
bash start.sh debug
bash test_smoke.sh
```

### 5. 生产模式

```bash
bash claude-with-eyes.sh
```

然后像平常一样使用 Claude Code，发图片即可。

## 注意事项

- **不要**把 `ANTHROPIC_BASE_URL` 设为 `https://api.deepseek.com/anthropic`，那会绕过本地代理
- `claude-with-eyes.sh` 是推荐入口，自动设置正确的代理地址
- 代理仅监听 `127.0.0.1:8788`，不接受外部连接

## 配置参考

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ARK_API_KEY` | 必填 | 火山引擎 ARK API Key |
| `VISION_MODEL` | `doubao-seed-2-0-lite-260215` | 豆包视觉模型 |
| `PROXY_PORT` | `8788` | 代理端口 |
| `CACHE_PERSIST_ENABLED` | `false` | 磁盘缓存（含 OCR 文字，谨慎开启） |
| `MAX_IMAGE_RAW_BYTES` | `10485760` | 单张图片最大 10MB |

完整配置见 `.env.example`。

## 测试

```bash
python3 test_transformer.py          # 15 单测
python3 test_proxy_stream.py         # SSE + 透明转发单测
bash start.sh debug && bash test_smoke.sh  # 14 冒烟测试
```

## 文件

| 文件 | 功能 |
|------|------|
| `proxy.py` | ThreadingHTTPServer 主代理 |
| `transformer.py` | image block 替换，不介入工具块 |
| `vision.py` | 豆包 Chat API |
| `vision_fake.py` | 假视觉（离线测试） |
| `cache.py` | 线程安全图片缓存 |
| `config.py` | .env 配置加载 |
| `claude-with-eyes.sh` | 推荐启动入口 |

## 架构决策

详见 [MEMORY.md](MEMORY.md) — 为什么选豆包、为什么双输出（描述+坐标）、缓存策略等。

## License

MIT
