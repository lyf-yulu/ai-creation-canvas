#!/usr/bin/env bash
# Build a self-contained static/Python release without copying runtime data.
set -euo pipefail

script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
root="$(CDPATH= cd -- "$script_dir/.." && pwd)"
skip_web_build=false
target=""

for argument in "$@"; do
    case "$argument" in
        --skip-web-build) skip_web_build=true ;;
        -*) echo "unknown option: $argument" >&2; exit 64 ;;
        *)
            if [[ -n "$target" ]]; then
                echo "only one output directory may be supplied" >&2
                exit 64
            fi
            target="$argument"
            ;;
    esac
done

if [[ -z "$target" ]]; then
    target="$(mktemp -d "${TMPDIR:-/tmp}/ai-creation-canvas-release.XXXXXX")"
    created_by_mktemp=true
else
    created_by_mktemp=false
fi

python3 - "$root" "$target" "$created_by_mktemp" <<'PY'
import os
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
target = Path(sys.argv[2]).expanduser()
created = sys.argv[3] == "true"
if target.is_symlink() or (target.exists() and not created):
    raise SystemExit("release output must be a new, non-symlink directory")
for parent in (target, *target.parents):
    if parent.exists() and parent.is_symlink():
        raise SystemExit("release output must not be beneath a symlink")
resolved = target.resolve(strict=False)
try:
    resolved.relative_to(root)
except ValueError:
    try:
        root.relative_to(resolved)
    except ValueError:
        pass
    else:
        raise SystemExit("release output must not contain the repository")
else:
    raise SystemExit("release output must not overlap the repository")
if not created:
    target.mkdir(mode=0o700, parents=True)
PY

marker="$target/.ai-creation-canvas-release-marker"
nonce="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
printf '%s\n' "$nonce" > "$marker"

cleanup_target() {
    status=$?
    if [[ "$status" -ne 0 && -f "$marker" ]]; then
        python3 - "$root" "$target" "$marker" "$nonce" <<'PY'
import shutil
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve(strict=False)
target = Path(sys.argv[2]).resolve(strict=False)
marker = Path(sys.argv[3]).resolve(strict=False)
nonce = sys.argv[4]
if target == root or target.is_symlink() or marker.parent != target:
    raise SystemExit(0)
if not marker.is_file() or marker.read_text(encoding="utf-8") != nonce + "\n":
    raise SystemExit(0)
for parent in (target, *target.parents):
    if parent.exists() and parent.is_symlink():
        raise SystemExit(0)
try:
    target.resolve(strict=True).relative_to(root)
except ValueError:
    shutil.rmtree(target)
PY
    fi
    exit "$status"
}
trap cleanup_target EXIT

build_input_hash() {
    python3 - "$root" <<'PY'
import hashlib
import sys
from pathlib import Path

root = Path(sys.argv[1])
inputs = [root / "VERSION", root / "CHANGELOG.md", root / "UPSTREAM.md", root / "web" / "package.json", root / "web" / "package-lock.json", root / "web" / "vite.config.ts"]
web = root / "web"
inputs.extend(path for path in web.rglob("*") if path.is_file() and "node_modules" not in path.parts and "dist" not in path.parts and path.suffix not in {".tsbuildinfo"})
digest = hashlib.sha256()
for path in sorted(set(inputs), key=lambda item: item.relative_to(root).as_posix()):
    relative = path.relative_to(root).as_posix().encode("utf-8")
    digest.update(relative + b"\0")
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
print(digest.hexdigest())
PY
}

stamp="$root/web/dist/.ai-creation-canvas-build-input.sha256"
input_hash="$(build_input_hash)"

write_dist_stamp() {
    python3 - "$1" "$2" "$3" <<'PY'
import hashlib
import json
import os
import stat
import sys
from pathlib import Path

dist = Path(sys.argv[1])
stamp = Path(sys.argv[2])
source_hash = sys.argv[3]
forbidden_names = {"node_modules", ".git", ".local-data", "state", "outputs", "uploads", "logs", "archives", "secrets"}
forbidden_suffixes = (".map", ".pyc", ".pyo", ".sqlite", ".sqlite3", ".db", ".pem", ".key", ".p12", ".pfx", ".jsonl")

def forbidden(path: Path) -> bool:
    return any(part in forbidden_names or part == ".env" or part.startswith(".env.") for part in path.parts) or path.name.endswith(forbidden_suffixes)

def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def manifest(root: Path) -> list[dict[str, str]]:
    if root.is_symlink() or not root.is_dir():
        raise SystemExit("web dist must be a real directory")
    files = []
    for current_text, directories, names in os.walk(root, topdown=True, followlinks=False):
        current = Path(current_text)
        for name in sorted(directories):
            candidate = current / name
            if candidate.is_symlink() or not stat.S_ISDIR(candidate.lstat().st_mode):
                raise SystemExit("web dist contains a non-directory entry")
            if forbidden(candidate.relative_to(root)):
                raise SystemExit("web dist contains a forbidden entry")
        for name in sorted(names):
            candidate = current / name
            relative = candidate.relative_to(root)
            if candidate == stamp:
                continue
            if candidate.is_symlink() or not stat.S_ISREG(candidate.lstat().st_mode) or forbidden(relative):
                raise SystemExit("web dist contains an unsafe file")
            text = relative.as_posix()
            if not text or text.startswith("/") or ".." in relative.parts or any(ord(char) < 32 for char in text):
                raise SystemExit("web dist contains an unsafe path")
            files.append({"path": text, "sha256": file_hash(candidate)})
    return sorted(files, key=lambda item: item["path"])

if stamp.is_symlink():
    raise SystemExit("web dist stamp must not be a symlink")
payload = {"files": manifest(dist), "source_input_sha256": source_hash, "version": 1}
encoded = (json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")
temporary = stamp.with_name(f".{stamp.name}.{os.getpid()}.tmp")
try:
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write(encoded)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, stamp)
finally:
    temporary.unlink(missing_ok=True)
PY
}

verify_dist_stamp() {
    python3 - "$1" "$2" "$3" <<'PY'
import hashlib
import hmac
import json
import os
import stat
import sys
from pathlib import Path

dist = Path(sys.argv[1])
stamp = Path(sys.argv[2])
source_hash = sys.argv[3]
forbidden_names = {"node_modules", ".git", ".local-data", "state", "outputs", "uploads", "logs", "archives", "secrets"}
forbidden_suffixes = (".map", ".pyc", ".pyo", ".sqlite", ".sqlite3", ".db", ".pem", ".key", ".p12", ".pfx", ".jsonl")

def forbidden(path: Path) -> bool:
    return any(part in forbidden_names or part == ".env" or part.startswith(".env.") for part in path.parts) or path.name.endswith(forbidden_suffixes)

def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def manifest(root: Path) -> list[dict[str, str]]:
    if root.is_symlink() or not root.is_dir():
        raise ValueError
    files = []
    for current_text, directories, names in os.walk(root, topdown=True, followlinks=False):
        current = Path(current_text)
        for name in sorted(directories):
            candidate = current / name
            if candidate.is_symlink() or not stat.S_ISDIR(candidate.lstat().st_mode) or forbidden(candidate.relative_to(root)):
                raise ValueError
        for name in sorted(names):
            candidate = current / name
            relative = candidate.relative_to(root)
            if candidate == stamp:
                continue
            if candidate.is_symlink() or not stat.S_ISREG(candidate.lstat().st_mode) or forbidden(relative):
                raise ValueError
            text = relative.as_posix()
            if not text or text.startswith("/") or ".." in relative.parts or any(ord(char) < 32 for char in text):
                raise ValueError
            files.append({"path": text, "sha256": file_hash(candidate)})
    return sorted(files, key=lambda item: item["path"])

try:
    if stamp.is_symlink() or not stamp.is_file():
        raise ValueError
    payload = json.loads(stamp.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or set(payload) != {"files", "source_input_sha256", "version"} or payload["version"] != 1 or not isinstance(payload["source_input_sha256"], str) or not hmac.compare_digest(payload["source_input_sha256"], source_hash):
        raise ValueError
    expected = payload["files"]
    actual = manifest(dist)
    if not isinstance(expected, list) or len(expected) != len(actual):
        raise ValueError
    seen = set()
    for recorded, observed in zip(expected, actual):
        if not isinstance(recorded, dict) or set(recorded) != {"path", "sha256"} or not isinstance(recorded["path"], str) or not isinstance(recorded["sha256"], str) or recorded["path"] in seen or recorded["path"] != observed["path"] or not hmac.compare_digest(recorded["sha256"], observed["sha256"]):
            raise ValueError
        seen.add(recorded["path"])
except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
    raise SystemExit("web dist stamp is missing, stale, tampered, or incomplete")
PY
}

if [[ "$skip_web_build" == true ]]; then
    [[ -f "$root/web/dist/index.html" ]] || { echo "--skip-web-build requires a verified web/dist/index.html" >&2; exit 65; }
    [[ -n "$(find "$root/web/dist" -type f -name '*.js' -print -quit)" ]] || { echo "--skip-web-build requires built JavaScript assets" >&2; exit 65; }
    verify_dist_stamp "$root/web/dist" "$stamp" "$input_hash"
else
    npm ci --prefix "$root/web"
    npm run build --prefix "$root/web"
    input_hash="$(build_input_hash)"
    write_dist_stamp "$root/web/dist" "$stamp" "$input_hash"
fi

[[ -f "$root/web/dist/index.html" ]] || { echo "web build did not produce index.html" >&2; exit 65; }

mkdir -p "$target/server" "$target/web" "$target/docs"
cp -R "$root/server/ai_creation_canvas" "$target/server/"
cp -R "$root/server/config" "$target/server/"
cp -R "$root/web/dist" "$target/web/"
verify_dist_stamp "$target/web/dist" "$target/web/dist/.ai-creation-canvas-build-input.sha256" "$input_hash"
for file in pyproject.toml requirements.lock LICENSE UPSTREAM.md README.md; do
    install -m 0644 "$root/$file" "$target/$file"
done
for document in operations.md verification.md; do
    install -m 0644 "$root/docs/$document" "$target/docs/$document"
done

# Only the fresh staging directory is touched below.  Remove interpreter and
# build leftovers before the hard allow-list audit; no source data is removed.
find "$target" -type d -name '__pycache__' -prune -exec rm -rf {} +
find "$target" -type f \( -name '*.pyc' -o -name '*.pyo' -o -name '*.map' \) -delete

if find "$target" -type l -print -quit | grep -q .; then
    echo "release staging contains a symlink" >&2
    exit 65
fi
if find "$target" \( -type d -o -type f \) \( \
    -name 'node_modules' -o -name '.git' -o -name '.local-data' -o -name '.env' -o -name '.env.*' -o \
    -name 'state' -o -name 'outputs' -o -name 'uploads' -o -name 'logs' -o -name 'archives' -o \
    -name '*.sqlite' -o -name '*.sqlite3' -o -name '*.db' -o -name '*.pem' -o -name '*.key' -o -name '*.p12' -o -name '*.pfx' -o -name '*.jsonl' \
\) -print -quit | grep -q .; then
    echo "release staging contains a forbidden runtime or secret entry" >&2
    exit 65
fi

(
    cd "$target"
    LC_ALL=C find . -type f ! -name manifest.sha256 -print | LC_ALL=C sort | while IFS= read -r path; do
        shasum -a 256 "$path"
    done > manifest.sha256
)
rm -f "$marker"
trap - EXIT
printf '%s\n' "$target"
