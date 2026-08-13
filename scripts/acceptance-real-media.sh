#!/bin/sh
set -eu

fail() { echo "$1" >&2; exit 64; }

[ "${AICC_RUN_PAID_ACCEPTANCE:-}" = "YES" ] || fail "Set AICC_RUN_PAID_ACCEPTANCE=YES to authorize the bounded paid acceptance."

aicc_repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
[ -n "${AICC_ACCEPTANCE_DATA:-}" ] || fail "AICC_ACCEPTANCE_DATA must name a brand-new isolated data path."
[ -n "${AICC_ACCEPTANCE_PORT:-}" ] || fail "AICC_ACCEPTANCE_PORT must be explicit."
[ -n "${AICC_ACCEPTANCE_MODEL_IDS:-}" ] || fail "AICC_ACCEPTANCE_MODEL_IDS must explicitly name every selected logical model."
[ -n "${AICC_ACCEPTANCE_CHANNEL_IDS:-}" ] || fail "AICC_ACCEPTANCE_CHANNEL_IDS must explicitly name every paid channel."
[ -n "${AICC_ACCEPTANCE_BANANA_SAMPLE_COUNT:-}" ] || fail "AICC_ACCEPTANCE_BANANA_SAMPLE_COUNT must be explicit, including zero."
[ -n "${AICC_MAX_PAID_CALLS:-}" ] || fail "AICC_MAX_PAID_CALLS must be an explicit integer from 1 through 20."

case "$AICC_ACCEPTANCE_PORT" in
    ''|*[!0-9]*) fail "AICC_ACCEPTANCE_PORT must be numeric." ;;
    8991|8992|8994|9003|9090|8787|8797|8798|8788|8891|8892|9190) fail "Refusing to use a reserved production, development, or acceptance port." ;;
esac
[ "$AICC_ACCEPTANCE_PORT" -ge 1024 ] 2>/dev/null && [ "$AICC_ACCEPTANCE_PORT" -le 65535 ] 2>/dev/null || fail "AICC_ACCEPTANCE_PORT is outside the safe range."
[ ! -e "$AICC_ACCEPTANCE_DATA" ] && [ ! -L "$AICC_ACCEPTANCE_DATA" ] || fail "AICC_ACCEPTANCE_DATA must be a brand-new path."

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

export AICC_ACCEPTANCE_MODEL_IDS AICC_ACCEPTANCE_CHANNEL_IDS
export AICC_ACCEPTANCE_BANANA_SAMPLE_COUNT AICC_MAX_PAID_CALLS
export AICC_CHIYUN_BASE_URL="${AICC_CHIYUN_BASE_URL:-}"
export AICC_T8STAR_BASE_URL="${AICC_T8STAR_BASE_URL:-}"

"$aicc_python" - <<'PY' || exit 64
import os
import re
import sys
from urllib.parse import urlsplit

channels = {
    "banana-chiyun": ("banana", "CHIYUN_API_KEY", "AICC_CHIYUN_BASE_URL"),
    "banana-t8star": ("banana", "T8STAR_API_KEY", "AICC_T8STAR_BASE_URL"),
    "gpt-image2-chiyun": ("gpt-image2", "CHIYUN_API_KEY", "AICC_CHIYUN_BASE_URL"),
    "seedream-ark": ("seedream", "ARK_API_KEY", None),
    "seedance-ark": ("seedance", "ARK_API_KEY", None),
}

def reject(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)

def values(name: str) -> list[str]:
    raw = os.environ[name]
    parsed = raw.split(",")
    if any(not item or re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", item) is None for item in parsed):
        reject(f"{name} is invalid.")
    if len(parsed) != len(set(parsed)):
        reject(f"{name} contains a duplicate channel or model.")
    return parsed

model_ids = values("AICC_ACCEPTANCE_MODEL_IDS")
channel_ids = values("AICC_ACCEPTANCE_CHANNEL_IDS")
unknown = set(channel_ids) - set(channels)
if unknown:
    reject("AICC_ACCEPTANCE_CHANNEL_IDS contains a channel outside the reviewed allowlist.")
expected_models = {channels[channel][0] for channel in channel_ids}
if set(model_ids) != expected_models:
    reject("The explicit model allowlist does not exactly cover the selected channels.")

try:
    maximum = int(os.environ["AICC_MAX_PAID_CALLS"])
    banana_samples = int(os.environ["AICC_ACCEPTANCE_BANANA_SAMPLE_COUNT"])
except ValueError:
    reject("AICC_MAX_PAID_CALLS and the Banana sample count must be integers.")
if not 1 <= maximum <= 20:
    reject("AICC_MAX_PAID_CALLS must be between 1 and 20.")
if not 0 <= banana_samples <= 20:
    reject("The Banana sample count must be between 0 and 20.")
if banana_samples and "banana" not in expected_models:
    reject("A Banana sample requires an explicitly selected Banana channel.")
planned = len(channel_ids) + banana_samples
if planned > maximum:
    reject("The paid call plan exceeds the explicit AICC_MAX_PAID_CALLS budget.")

selected_key_names = {channels[channel][1] for channel in channel_ids}
selected_origin_names = {name for channel in channel_ids if (name := channels[channel][2]) is not None}
for name in sorted(selected_key_names):
    value = os.environ.get(name, "")
    if not 8 <= len(value) <= 4096 or any(char in value for char in "\r\n\0"):
        reject(f"{name} is required for the selected channel.")
for name in sorted(selected_origin_names):
    value = os.environ.get(name, "")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        reject(f"{name} must be an HTTPS origin.")
    if parsed.scheme != "https" or not parsed.hostname or port not in {None, 443} or parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        reject(f"{name} must be an HTTPS origin.")

print(f"Paid acceptance plan: models={','.join(model_ids)} channels={','.join(channel_ids)} calls={planned}/{maximum}.")
for name in ("CHIYUN_API_KEY", "T8STAR_API_KEY", "ARK_API_KEY"):
    print(f"{name}={'SET' if bool(os.environ.get(name)) else 'UNSET'}")
PY

if [ "${AICC_ACCEPTANCE_GUARD_ONLY:-}" = "YES" ]; then
    echo "Paid acceptance guard ready. No provider request was made."
    exit 0
fi

# Move selected paid credentials out of the inherited environment before any
# verifier/build subprocess. The bundle is mode 0600 and is consumed exactly
# once by the acceptance runner.
umask 077
aicc_key_file=$(mktemp "${TMPDIR:-/tmp}/.aicc-paid-acceptance-keys.XXXXXX") || fail "Could not create the isolated acceptance credential file."
aicc_release_parent=""
cleanup_acceptance() {
    rm -f -- "$aicc_key_file"
    if [ -n "$aicc_release_parent" ] && [ -d "$aicc_release_parent" ]; then
        rm -rf -- "$aicc_release_parent"
    fi
}
trap cleanup_acceptance EXIT HUP INT TERM

AICC_ACCEPTANCE_KEY_FILE="$aicc_key_file" "$aicc_python" - <<'PY' || fail "Could not isolate the selected paid credentials."
import json
import os
from pathlib import Path
import stat

channel_keys = {
    "banana-chiyun": "CHIYUN_API_KEY",
    "banana-t8star": "T8STAR_API_KEY",
    "gpt-image2-chiyun": "CHIYUN_API_KEY",
    "seedream-ark": "ARK_API_KEY",
    "seedance-ark": "ARK_API_KEY",
}
selected = {channel_keys[channel] for channel in os.environ["AICC_ACCEPTANCE_CHANNEL_IDS"].split(",")}
payload = {name: os.environ[name] for name in sorted(selected)}
path = Path(os.environ["AICC_ACCEPTANCE_KEY_FILE"])
initial = path.lstat()
flags = os.O_WRONLY | os.O_TRUNC | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
descriptor = os.open(path, flags)
with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
    opened = os.fstat(handle.fileno())
    current = path.lstat()
    if (
        not stat.S_ISREG(initial.st_mode)
        or initial.st_mode & 0o077
        or (initial.st_dev, initial.st_ino) != (opened.st_dev, opened.st_ino)
        or (initial.st_dev, initial.st_ino) != (current.st_dev, current.st_ino)
    ):
        raise RuntimeError("unsafe acceptance credential file")
    json.dump(payload, handle, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    handle.flush()
    os.fsync(handle.fileno())
PY
unset ARK_API_KEY CHIYUN_API_KEY T8STAR_API_KEY

if [ "${AICC_ACCEPTANCE_ENV_PROBE:-}" = "YES" ]; then
    [ -z "${ARK_API_KEY+x}" ] && [ -z "${CHIYUN_API_KEY+x}" ] && [ -z "${T8STAR_API_KEY+x}" ] || fail "Offline environment still contains a paid credential."
    AICC_ACCEPTANCE_KEY_FILE="$aicc_key_file" PYTHONPATH="$aicc_repo_root:$aicc_repo_root/server" "$aicc_python" "$aicc_repo_root/scripts/acceptance_real_media.py" --probe-key-boundary
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
command -v ffprobe >/dev/null 2>&1 || fail "ffprobe is required for paid result decode verification."
aicc_release_parent=$(mktemp -d "${TMPDIR:-/tmp}/aicc-paid-acceptance-release.XXXXXX") || fail "Could not create an isolated release directory."
bash "$aicc_repo_root/scripts/build-release.sh" "$aicc_release_parent/full"
bash "$aicc_repo_root/scripts/build-release.sh" --skip-web-build "$aicc_release_parent/skip"

mkdir -m 700 -p "$(dirname -- "$AICC_ACCEPTANCE_DATA")"
mkdir -m 700 "$AICC_ACCEPTANCE_DATA"
git status --porcelain --untracked-files=normal | grep -q . && fail "Acceptance data creation changed the worktree."
bash "$aicc_repo_root/scripts/security-scan.sh"

export AICC_ACCEPTANCE_PORT AICC_ACCEPTANCE_DATA
AICC_ACCEPTANCE_KEY_FILE="$aicc_key_file" PYTHONPATH="$aicc_repo_root:$aicc_repo_root/server" "$aicc_python" "$aicc_repo_root/scripts/acceptance_real_media.py"
