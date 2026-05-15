#!/bin/bash
# DeepSeek Eyes — 停止代理
set -e

PID_FILE="/tmp/deepseek-eyes.pid"

if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        kill "$PID"
        rm -f "$PID_FILE"
        echo "代理已停止 (pid=$PID)"
    else
        rm -f "$PID_FILE"
        echo "代理未运行 (残留 pid 文件已清理)"
    fi
else
    # fallback: 按端口杀
    if lsof -ti:8788 >/dev/null 2>&1; then
        lsof -ti:8788 | xargs kill
        echo "代理已停止 (按端口 8788)"
    else
        echo "代理未运行"
    fi
fi
