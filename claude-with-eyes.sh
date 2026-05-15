#!/bin/bash
# DeepSeek Eyes — launch Claude Code through the local vision proxy.
#
# This is the recommended entrypoint. It avoids the common mistake of keeping
# ANTHROPIC_BASE_URL pointed directly at https://api.deepseek.com/anthropic.

set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

BASE_URL="http://127.0.0.1:8788"
SETTINGS_PATH="$HOME/.claude/settings.json"

python3 - "$SETTINGS_PATH" "$BASE_URL" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
base_url = sys.argv[2]

if not path.exists():
    raise SystemExit(0)

data = json.loads(path.read_text())
env = data.setdefault("env", {})
old = env.get("ANTHROPIC_BASE_URL", "")

if old != base_url:
    env["ANTHROPIC_BASE_URL"] = base_url
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    path.chmod(0o600)
    print(f"已更新 {path}: ANTHROPIC_BASE_URL={base_url}")
PY

if bash start.sh; then
    :
else
    if curl --noproxy '*' -fsS "$BASE_URL/health" >/dev/null 2>&1; then
        echo "代理已可用: $BASE_URL"
    else
        echo "代理启动失败，且 $BASE_URL/health 不可用" >&2
        echo "查看日志: tail -f /tmp/deepseek-eyes.log" >&2
        exit 1
    fi
fi

export ANTHROPIC_BASE_URL="$BASE_URL"

echo ""
echo "启动 Claude Code via DeepSeek Eyes"
echo "ANTHROPIC_BASE_URL=$ANTHROPIC_BASE_URL"
echo "Claude settings: $SETTINGS_PATH"
echo "日志: tail -f /tmp/deepseek-eyes.log"
echo ""

exec claude "$@"
