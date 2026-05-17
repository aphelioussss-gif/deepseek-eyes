# DeepSeek Eyes

给 DeepSeek + Claude Code 加视觉能力。本地 HTTP 代理拦截 Anthropic 请求中的图片，通过豆包 Chat API 将图片转为文字描述（含坐标定位），再转发给 DeepSeek。

## 约束

- 如果要改代码，先跑测试，再提交改动。
- **图片识别必须走豆包 `vision.py`**，不得依赖模型原生视觉。DeepSeek 原生视觉不可靠（已知幻觉问题），在本项目目录下处理任何图片时，直接调用 `vision.analyze_image()` 获取豆包结果后再回复。

## 快速开始

```bash
# 1. 复制配置，填入 ARK_API_KEY
cp .env.example .env
# 编辑 .env: ARK_API_KEY=ark-xxx

# 2. 启动代理
bash start.sh

# 3. 启动 Claude Code（自动指向本地代理）
bash claude-with-eyes.sh
```

不要把 `ANTHROPIC_BASE_URL` 设为 `https://api.deepseek.com/anthropic`，那会绕过本地代理，图片不会被转换。
`ANTHROPIC_BASE_URL` 默认为直连 DeepSeek（`https://api.deepseek.com/anthropic`），`claude-with-eyes.sh` 通过环境变量临时覆盖为 `http://127.0.0.1:8788`，不动 settings.json。

## 文件

| 文件 | 功能 |
|------|------|
| `proxy.py` | ThreadingHTTPServer 主代理 |
| `transformer.py` | 直接 image block 替换，不介入工具块 |
| `vision.py` | 豆包 Chat API |
| `cache.py` | 线程安全图片缓存 |
| `config.py` | .env 配置加载 |
| `claude-with-eyes.sh` | 推荐启动入口，自动设置本地代理 base URL |

## 测试

```bash
python3 test_transformer.py          # 15 单测
python3 test_proxy_stream.py         # 20 单测（SSE + 透明转发）
bash start.sh && bash test_smoke.sh  # 15 冒烟测试 (需先启动代理)
```

## 端口

8788，仅绑定 127.0.0.1。
