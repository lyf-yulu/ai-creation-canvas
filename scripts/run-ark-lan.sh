#!/bin/sh
set -eu

# LAN + real Ark models (Seedream / Seedance) entry for trusted local networks.
# ARK_API_KEY stays in the server terminal environment only; it is never sent
# to or displayed in the browser. The declaration file contains no keys.
#
# Usage:
#   ARK_API_KEY=你的方舟APIKey bash scripts/run-ark-lan.sh
# Optional overrides:
#   AICC_ARK_MODELS_CONFIG       (default: my-ark-models.json in the repo root)
#   AICC_LOCAL_PORT              (default: 8992)
#   AICC_LOCAL_HOST              (default: 0.0.0.0 for LAN access)
#   AICC_LOCAL_ORIGINS           (default: loopback + the LAN address below;
#                                 must match the browser address exactly)
#   AICC_LOCAL_DATA              (default: .local-data in the repo root)
#   AICC_ASSET_LIBRARY_CONFIG    (optional: Ark portrait asset library JSON)
#   AICC_ASSET_LIBRARY_CONFIG_ROOT (required together with the config path)
#   AICC_ARK_KEY_CONFIG          (optional: web-importable Ark generation key JSON)
#   AICC_ARK_KEY_CONFIG_ROOT     (required together with the key config path)

aicc_repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
AICC_LOCAL_HOST=${AICC_LOCAL_HOST:-0.0.0.0}
AICC_LOCAL_PORT=${AICC_LOCAL_PORT:-8992}
AICC_LOCAL_DATA=${AICC_LOCAL_DATA:-"$aicc_repo_root/.local-data"}
AICC_ARK_MODELS_CONFIG=${AICC_ARK_MODELS_CONFIG:-"$aicc_repo_root/my-ark-models.json"}
AICC_LAN_ORIGINS=${AICC_LOCAL_ORIGINS:-"http://127.0.0.1:8992 http://172.16.0.90:8992"}

case "$AICC_LOCAL_PORT" in
    ''|*[!0-9]*) echo "AICC_LOCAL_PORT must be a numeric local port" >&2; exit 64 ;;
    8991|9090|8787|8797|8891) echo "Refusing to use a reserved production port" >&2; exit 64 ;;
esac

if [ -z "${ARK_API_KEY:-}" ] && [ -z "${AICC_ARK_KEY_CONFIG:-}" ]; then
    echo "ARK_API_KEY or AICC_ARK_KEY_CONFIG is required for real Seedream and Seedance media." >&2
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
    --host "$AICC_LOCAL_HOST" \
    --port "$AICC_LOCAL_PORT" \
    --data-dir "$AICC_LOCAL_DATA" \
    --static-dir "$aicc_repo_root/web/dist" \
    --ark-models "$AICC_ARK_MODELS_CONFIG" \
    --bootstrap-if-empty
for aicc_origin in $AICC_LAN_ORIGINS; do
    set -- "$@" --public-origin "$aicc_origin"
done
if [ -n "${AICC_ASSET_LIBRARY_CONFIG:-}" ]; then
    if [ -z "${AICC_ASSET_LIBRARY_CONFIG_ROOT:-}" ]; then
        echo "AICC_ASSET_LIBRARY_CONFIG requires AICC_ASSET_LIBRARY_CONFIG_ROOT" >&2
        exit 64
    fi
    set -- "$@" --asset-library-config "$AICC_ASSET_LIBRARY_CONFIG" --asset-library-config-root "$AICC_ASSET_LIBRARY_CONFIG_ROOT"
fi
if [ -n "${AICC_ARK_KEY_CONFIG:-}" ]; then
    if [ -z "${AICC_ARK_KEY_CONFIG_ROOT:-}" ]; then
        echo "AICC_ARK_KEY_CONFIG requires AICC_ARK_KEY_CONFIG_ROOT" >&2
        exit 64
    fi
    set -- "$@" --ark-key-config "$AICC_ARK_KEY_CONFIG" --ark-key-config-root "$AICC_ARK_KEY_CONFIG_ROOT"
fi
if [ -n "${AICC_PROMPT_SKILL_MODEL:-}" ]; then
    set -- "$@" --prompt-skill-model "$AICC_PROMPT_SKILL_MODEL"
fi
PYTHONPATH="$aicc_repo_root/server" exec "$aicc_python" "$@"
