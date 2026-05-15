# DeepSeek Eyes

给 DeepSeek + Claude Code 加视觉能力。本地 HTTP 代理拦截 Anthropic 请求中的图片，通过豆包 Chat API 将图片转为文字描述（含坐标定位），再转发给 DeepSeek。

## 快速开始

```bash
# 1. 复制配置，填入 ARK_API_KEY
cp .env.example .env
# 编辑 .env: ARK_API_KEY=sk-xxx

# 2. 离线测试（不调真实 API）
bash start.sh debug
bash test_smoke.sh

# 3. 生产模式
bash claude-with-eyes.sh
```

不要把 `ANTHROPIC_BASE_URL` 设为 `https://api.deepseek.com/anthropic`，那会绕过本地代理，图片不会被转换。
`~/.claude/settings.json` 里的 `env.ANTHROPIC_BASE_URL` 也必须是 `http://127.0.0.1:8788`；`claude-with-eyes.sh` 会自动修正它。

## 文件

| 文件 | 功能 |
|------|------|
| `proxy.py` | ThreadingHTTPServer 主代理 |
| `transformer.py` | 直接 image block 替换，不介入工具块 |
| `vision.py` | 豆包 Chat API |
| `vision_fake.py` | 假视觉（离线测试） |
| `cache.py` | 线程安全图片缓存 |
| `config.py` | .env 配置加载 |
| `claude-with-eyes.sh` | 推荐启动入口，自动设置本地代理 base URL |

## 测试

```bash
python3 test_transformer.py          # 15 单测
python3 test_proxy_stream.py         # SSE + 透明转发单测
bash start.sh debug && bash test_smoke.sh  # 14 冒烟测试 (需先启动代理)
```

## 端口

8788，仅绑定 127.0.0.1。
