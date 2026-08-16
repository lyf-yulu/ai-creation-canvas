#!/bin/sh
set -eu

aicc_repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
AICC_LOCAL_DATA=${AICC_LOCAL_DATA:-"$aicc_repo_root/.local-data"}
AICC_LOCAL_PORT=${AICC_LOCAL_PORT:-8992}
AICC_LOCAL_HOST=${AICC_LOCAL_HOST:-127.0.0.1}

case "$AICC_LOCAL_PORT" in
    ''|*[!0-9]*) echo "AICC_LOCAL_PORT must be a numeric local port" >&2; exit 64 ;;
    8991|9090|8787|8797|8891) echo "Refusing to use a reserved production port" >&2; exit 64 ;;
esac

if [ -n "${AICC_PYTHON:-}" ]; then
    aicc_python=$AICC_PYTHON
elif [ -x "$aicc_repo_root/.venv/bin/python" ]; then
    aicc_python="$aicc_repo_root/.venv/bin/python"
else
    aicc_python=python3
fi

npm ci --prefix "$aicc_repo_root/web"
npm run build --prefix "$aicc_repo_root/web"
set -- \
    "$aicc_python" -m ai_creation_canvas serve-local \
    --host "$AICC_LOCAL_HOST" \
    --port "$AICC_LOCAL_PORT" \
    --data-dir "$AICC_LOCAL_DATA" \
    --static-dir "$aicc_repo_root/web/dist" \
    --bootstrap-if-empty

if [ -n "${AICC_LOCAL_ORIGIN:-}" ]; then
    set -- "$@" --public-origin "$AICC_LOCAL_ORIGIN"
fi

if [ -n "${AICC_COMFYUI_SERVICES:-}" ]; then
    set -- "$@" --comfyui-services "$AICC_COMFYUI_SERVICES"
fi

case "$AICC_LOCAL_HOST" in
    127.*) set -- "$@" --open ;;
esac

PYTHONPATH="$aicc_repo_root/server" exec "$@"
