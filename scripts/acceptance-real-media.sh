#!/bin/sh
set -eu

fail() { echo "$1" >&2; exit 64; }

[ "${AICC_ALLOW_PAID_ACCEPTANCE:-}" = "YES" ] || fail "Set AICC_ALLOW_PAID_ACCEPTANCE=YES to authorize the bounded paid acceptance."
[ -n "${ARK_API_KEY:-}" ] || fail "ARK_API_KEY is required in the server environment."

# Remove the paid credential from the inherited environment before starting any
# verifier/build subprocess. noclobber prevents following or replacing a link.
umask 077
aicc_key_file=${TMPDIR:-/tmp}/.aicc-paid-acceptance-key.$$
set -C
if ! printf '%s' "$ARK_API_KEY" > "$aicc_key_file"; then
    set +C
    fail "Could not create the isolated acceptance credential file."
fi
set +C
unset ARK_API_KEY AICC_ACCEPTANCE_KEY_FILE
cleanup_key() { rm -f -- "$aicc_key_file"; }
trap cleanup_key EXIT HUP INT TERM

aicc_repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
AICC_ACCEPTANCE_PORT=${AICC_ACCEPTANCE_PORT:-8998}
AICC_ACCEPTANCE_DATA=${AICC_ACCEPTANCE_DATA:-"$aicc_repo_root/.paid-acceptance/run-$$"}
AICC_ACCEPTANCE_MODELS_CONFIG=${AICC_ACCEPTANCE_MODELS_CONFIG:-"$aicc_repo_root/server/config/ark-models.example.json"}
AICC_ACCEPTANCE_IMAGE_MODEL_ID=${AICC_ACCEPTANCE_IMAGE_MODEL_ID:-doubao-seedream-4-0-250828}
AICC_ACCEPTANCE_VIDEO_MODEL_ID=${AICC_ACCEPTANCE_VIDEO_MODEL_ID:-doubao-seedance-2-0-260128}
AICC_ACCEPTANCE_IMAGE_COUNT=${AICC_ACCEPTANCE_IMAGE_COUNT:-1}
AICC_ACCEPTANCE_VIDEO_COUNT=${AICC_ACCEPTANCE_VIDEO_COUNT:-1}

[ "$AICC_ACCEPTANCE_IMAGE_MODEL_ID" = "doubao-seedream-4-0-250828" ] || fail "Image model is outside the paid acceptance allowlist."
[ "$AICC_ACCEPTANCE_VIDEO_MODEL_ID" = "doubao-seedance-2-0-260128" ] || fail "Video model is outside the paid acceptance allowlist."
[ "$AICC_ACCEPTANCE_IMAGE_COUNT" = "1" ] && [ "$AICC_ACCEPTANCE_VIDEO_COUNT" = "1" ] || fail "Paid acceptance requires exactly one image call and exactly one video call."

case "$AICC_ACCEPTANCE_PORT" in
    ''|*[!0-9]*) fail "AICC_ACCEPTANCE_PORT must be numeric." ;;
    8991|8992|8994|9090|8787|8797|8798|8788|9190) fail "Refusing to use a reserved production or development port." ;;
esac
[ ! -e "$AICC_ACCEPTANCE_DATA" ] && [ ! -L "$AICC_ACCEPTANCE_DATA" ] || fail "AICC_ACCEPTANCE_DATA must be a brand-new path."
[ -f "$AICC_ACCEPTANCE_MODELS_CONFIG" ] && [ ! -L "$AICC_ACCEPTANCE_MODELS_CONFIG" ] || fail "Model declarations must be a regular administrator-owned file."

if [ -n "${AICC_PYTHON:-}" ]; then
    aicc_python=$AICC_PYTHON
elif [ -x "$aicc_repo_root/.venv/bin/python" ]; then
    aicc_python="$aicc_repo_root/.venv/bin/python"
else
    aicc_python=python3
fi

aicc_data_scope=$(
    AICC_REPO_ROOT="$aicc_repo_root" AICC_ACCEPTANCE_DATA="$AICC_ACCEPTANCE_DATA" "$aicc_python" - <<'PY'
import os, pathlib, sys
repo = pathlib.Path(os.environ["AICC_REPO_ROOT"]).resolve(strict=True)
candidate = pathlib.Path(os.environ["AICC_ACCEPTANCE_DATA"])
if not candidate.is_absolute():
    print("Acceptance data path must be absolute.", file=sys.stderr); raise SystemExit(1)
for parent in (candidate, *candidate.parents):
    if parent.is_symlink():
        print("Acceptance data path cannot use a symlink.", file=sys.stderr); raise SystemExit(1)
resolved = candidate.resolve(strict=False)
if resolved != candidate:
    print("Acceptance data path must be normalized without traversal.", file=sys.stderr); raise SystemExit(1)
try:
    relative = resolved.relative_to(repo)
except ValueError:
    try:
        repo.relative_to(resolved)
    except ValueError:
        print("external")
    else:
        print("Acceptance data path cannot contain the repository.", file=sys.stderr); raise SystemExit(1)
else:
    if len(relative.parts) < 2 or relative.parts[0] != ".paid-acceptance":
        print("Repository data path must stay under .paid-acceptance.", file=sys.stderr); raise SystemExit(1)
    print("inside")
PY
) || fail "Acceptance data path is unsafe."
if [ "$aicc_data_scope" = "inside" ]; then
    git -C "$aicc_repo_root" check-ignore -q -- "$AICC_ACCEPTANCE_DATA" || fail "Repository acceptance data path must be ignored by Git."
elif [ "$aicc_data_scope" != "external" ]; then
    fail "Acceptance data path is unsafe."
fi

AICC_ACCEPTANCE_MODELS_CONFIG="$AICC_ACCEPTANCE_MODELS_CONFIG" "$aicc_python" - <<'PY' || exit 64
import json, os, pathlib, sys
path = pathlib.Path(os.environ["AICC_ACCEPTANCE_MODELS_CONFIG"])
try:
    models = json.loads(path.read_text(encoding="utf-8"))["models"]
except (OSError, ValueError, KeyError, TypeError):
    print("Model allowlist declaration is invalid.", file=sys.stderr); raise SystemExit(1)
expected = {"doubao-seedream-4-0-250828", "doubao-seedance-2-0-260128"}
if not isinstance(models, list) or {item.get("model_id") for item in models if isinstance(item, dict)} != expected or len(models) != 2:
    print("Model declaration does not exactly match the paid acceptance allowlist.", file=sys.stderr); raise SystemExit(1)
video = next(item for item in models if item["model_id"] == "doubao-seedance-2-0-260128")
image = next(item for item in models if item["model_id"] == "doubao-seedream-4-0-250828")
properties = video.get("parameter_schema", {}).get("properties", {})
if "480p" not in properties.get("resolution", {}).get("enum", []) or not properties.get("duration", {}).get("minimum", 99) <= 5 <= properties.get("duration", {}).get("maximum", -1):
    print("The reviewed video model does not support 5s/480p; refusing to increase cost.", file=sys.stderr); raise SystemExit(1)
def supports_reference(item):
    return any(port.get("port_id") == "reference_images" and port.get("max_items", 0) >= 1 for port in item.get("input_ports", []) if isinstance(port, dict))
if "image.edit" not in image.get("operations", []) or "video.generate" not in video.get("operations", []) or not supports_reference(image) or not supports_reference(video):
    print("The reviewed models do not support the required reference chain.", file=sys.stderr); raise SystemExit(1)
PY

if [ "${AICC_ACCEPTANCE_ENV_PROBE:-}" = "YES" ]; then
    [ -z "${ARK_API_KEY+x}" ] || fail "Offline environment still contains the paid credential."
    [ -z "${AICC_ACCEPTANCE_KEY_FILE+x}" ] || fail "Offline environment can locate the paid credential file."
    AICC_ACCEPTANCE_KEY_FILE="$aicc_key_file" PYTHONPATH="$aicc_repo_root:$aicc_repo_root/server" "$aicc_python" "$aicc_repo_root/scripts/acceptance_real_media.py" --probe-key-boundary
    exit 0
fi

if [ "${AICC_ACCEPTANCE_GUARD_ONLY:-}" = "YES" ]; then
    echo "Paid acceptance guard ready. No provider request was made."
    exit 0
fi

echo "Running complete offline verification before paid acceptance."
cd "$aicc_repo_root"
git diff --check
git diff --exit-code
git diff --cached --exit-code
[ -z "$(git status --porcelain --untracked-files=normal)" ] || fail "Paid acceptance requires a clean committed worktree."
bash "$aicc_repo_root/scripts/security-scan.sh"
PYTHONPATH="$aicc_repo_root:$aicc_repo_root/server" "$aicc_python" -m pytest -q "$aicc_repo_root/tests"
npm ci --prefix "$aicc_repo_root/web"
npm run verify:release --prefix "$aicc_repo_root/web"
aicc_release_parent=$("$aicc_python" -c 'import tempfile; from pathlib import Path; print(Path(tempfile.mkdtemp(prefix="aicc-paid-acceptance-release.")).resolve())')
bash "$aicc_repo_root/scripts/build-release.sh" "$aicc_release_parent/full"
bash "$aicc_repo_root/scripts/build-release.sh" --skip-web-build "$aicc_release_parent/skip"

mkdir -m 700 -p "$(dirname -- "$AICC_ACCEPTANCE_DATA")"
mkdir -m 700 "$AICC_ACCEPTANCE_DATA"
git status --porcelain --untracked-files=normal | grep -q . && fail "Acceptance data creation changed the worktree."
bash "$aicc_repo_root/scripts/security-scan.sh"
export AICC_ACCEPTANCE_PORT AICC_ACCEPTANCE_DATA AICC_ACCEPTANCE_MODELS_CONFIG AICC_ACCEPTANCE_IMAGE_MODEL_ID AICC_ACCEPTANCE_VIDEO_MODEL_ID
AICC_ACCEPTANCE_KEY_FILE="$aicc_key_file" PYTHONPATH="$aicc_repo_root:$aicc_repo_root/server" exec "$aicc_python" "$aicc_repo_root/scripts/acceptance_real_media.py"
