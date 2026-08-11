#!/bin/sh
set -eu

aicc_repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
AICC_REAL_MEDIA_DATA=${AICC_REAL_MEDIA_DATA:-"$aicc_repo_root/.local-real-media-data"}
AICC_REAL_MEDIA_PORT=${AICC_REAL_MEDIA_PORT:-8994}
AICC_ARK_MODELS_CONFIG=${AICC_ARK_MODELS_CONFIG:-"$aicc_repo_root/server/config/ark-models.example.json"}

case "$AICC_REAL_MEDIA_PORT" in
    ''|*[!0-9]*) echo "AICC_REAL_MEDIA_PORT must be a numeric local port" >&2; exit 64 ;;
    8991|9090|8787|8797|8891) echo "Refusing to use a reserved production port" >&2; exit 64 ;;
esac

if [ -z "${ARK_API_KEY:-}" ]; then
    echo "ARK_API_KEY is required for real Seedream and Seedance media." >&2
    exit 64
fi
if [ ! -f "$AICC_ARK_MODELS_CONFIG" ] || [ -L "$AICC_ARK_MODELS_CONFIG" ]; then
    echo "AICC_ARK_MODELS_CONFIG must name a regular administrator-owned declaration file" >&2
    exit 64
fi

if [ -n "${AICC_PYTHON:-}" ]; then
    aicc_python=$AICC_PYTHON
elif [ -x "$aicc_repo_root/.venv/bin/python" ]; then
    aicc_python="$aicc_repo_root/.venv/bin/python"
else
    aicc_python=python3
fi

npm ci --prefix "$aicc_repo_root/web"
npm run build --prefix "$aicc_repo_root/web"
set -- -m ai_creation_canvas serve-local \
    --port "$AICC_REAL_MEDIA_PORT" \
    --data-dir "$AICC_REAL_MEDIA_DATA" \
    --static-dir "$aicc_repo_root/web/dist" \
    --ark-models "$AICC_ARK_MODELS_CONFIG" \
    --bootstrap-if-empty \
    --open
if [ -n "${AICC_PROMPT_SKILL_MODEL:-}" ]; then
    set -- "$@" --prompt-skill-model "$AICC_PROMPT_SKILL_MODEL"
fi
PYTHONPATH="$aicc_repo_root/server" exec "$aicc_python" "$@"
