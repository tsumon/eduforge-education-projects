#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [ ! -d .venv ]; then
  echo "正在创建虚拟环境..."
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -c "import fastapi, uvicorn, multipart, qdrant_client, dotenv, langgraph" 2>/dev/null || python -m pip install -r requirements.txt
exec python run.py --load-samples "$@"
