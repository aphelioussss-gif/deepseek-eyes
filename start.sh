#!/bin/bash
# DeepSeek Eyes — 启动代理
# 用法: bash start.sh [debug|fake]
#   debug  → DEEPSEEK_EYES_DEBUG=1 + FAKE_VISION=1 (离线测试)
#   fake   → DEEPSEEK_EYES_FAKE_VISION=1 (假视觉+真实DeepSeek转发)
#   无参数 → 生产模式 (真实豆包+真实DeepSeek)

set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

PID_FILE="/tmp/deepseek-eyes.pid"
LOG_FILE="/tmp/deepseek-eyes.log"

if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "代理已在运行 (pid=$(cat "$PID_FILE"))"
    exit 1
fi

case "${1:-}" in
    debug)
        export DEEPSEEK_EYES_DEBUG=1
        export DEEPSEEK_EYES_FAKE_VISION=1
        echo "启动模式: DEBUG + FAKE_VISION (离线测试)"
        ;;
    fake)
        export DEEPSEEK_EYES_FAKE_VISION=1
        echo "启动模式: FAKE_VISION (假视觉+真实转发)"
        ;;
    *)
        echo "启动模式: PRODUCTION (真实豆包+真实DeepSeek)"
        ;;
esac

nohup python3 proxy.py > "$LOG_FILE" 2>&1 &
PID=$!
echo "$PID" > "$PID_FILE"

sleep 1
if kill -0 "$PID" 2>/dev/null; then
    echo "代理已启动 (pid=$PID)"
    echo "日志: tail -f $LOG_FILE"
    echo ""
    echo "Claude Code 必须指向本地代理，不要直连 https://api.deepseek.com/anthropic"
    echo "推荐启动:"
    echo "  bash claude-with-eyes.sh"
    echo ""
    echo "手动启动时使用:"
    echo "  export ANTHROPIC_BASE_URL=http://127.0.0.1:8788"
    echo "  claude"
else
    echo "启动失败，查看日志: $LOG_FILE"
    exit 1
fi
