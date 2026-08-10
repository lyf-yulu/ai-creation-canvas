#!/usr/bin/env bash
# Prepare a disposable, allowlisted Portal fixture.  It never overwrites input.
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 /absolute/portal-source /absolute/ai-creation-canvas/work/portal-test-name" >&2
  exit 64
fi

source_arg=$1
target_arg=$2
if [[ $source_arg != /* || $target_arg != /* ]]; then
  echo "source and target must be absolute paths" >&2
  exit 64
fi

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
repo_root=$(CDPATH= cd -- "$script_dir/.." && pwd -P)
patch_file="$repo_root/integrations/portal/signed-identity-v2.patch"

canonical_source=$(python3 - "$source_arg" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
if not p.is_dir():
    raise SystemExit("source must be an existing directory")
print(p.resolve(strict=True))
PY
)
target_parent="$repo_root/work"
mkdir -p -- "$target_parent"
canonical_parent=$(python3 - "$target_parent" <<'PY'
from pathlib import Path
import sys
print(Path(sys.argv[1]).resolve(strict=True))
PY
)
canonical_target=$(python3 - "$target_arg" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
if p.exists() or p.is_symlink():
    raise SystemExit("target must not already exist")
print((p.parent.resolve(strict=True) / p.name))
PY
)

if [[ $(dirname -- "$canonical_target") != "$canonical_parent" || $(basename -- "$canonical_target") != portal-test-* ]]; then
  echo "target must be a new $canonical_parent/portal-test-* directory" >&2
  exit 64
fi
if [[ $canonical_target == "$repo_root" || $canonical_source == "$canonical_target" || $canonical_source == "$canonical_target"/* || $canonical_target == "$canonical_source"/* ]]; then
  echo "source and target must not overlap the repository or each other" >&2
  exit 64
fi
if [[ ! -f $patch_file ]]; then
  echo "required integration patch is missing" >&2
  exit 70
fi

created=0
cleanup() {
  status=$?
  if [[ $status -ne 0 && $created -eq 1 && -d $canonical_target ]]; then
    rm -rf -- "$canonical_target"
  fi
  exit "$status"
}
trap cleanup EXIT

mkdir -- "$canonical_target"
created=1

copy_file() {
  local relative=$1
  local source_file="$canonical_source/$relative"
  [[ -f $source_file ]] || return 0
  if [[ -L $source_file ]]; then
    echo "allowlisted source files must not be symlinks: $relative" >&2
    exit 65
  fi
  mkdir -p -- "$canonical_target/$(dirname -- "$relative")"
  cp -p -- "$source_file" "$canonical_target/$relative"
}

# This is deliberately an allowlist, not a broad copy plus a denylist.
for file in app.py app_spec.py pyproject.toml requirements.txt requirements.lock config.example.json config.example.toml; do
  copy_file "$file"
done
for root in portal static config; do
  [[ -e $canonical_source/$root ]] || continue
  while IFS= read -r -d '' file; do
    relative=${file#"$canonical_source/"}
    case "/$relative" in
      */.git/*|*/.env*|*/secrets/*|*/secret/*|*/keys/*|*/certificates/*|*/certs/*|*/state/*|*/outputs/*|*/archives/*|*/uploads/*|*/logs/*|*/cache/*|*/caches/*|*/database/*|*/databases/*|*/request-records/*|*/[Ss]eedance*|*/[Nn]ano-[Bb]anana*|*/[Nn]ano[Bb]anana*|*/[Dd]reamina*|*/[Pp]ortrait*) continue ;;
    esac
    case "$relative" in
      portal/*.py|static/*|config/*.example.json|config/*.example.toml|config/*.example.yaml|config/*.example.yml) copy_file "$relative" ;;
    esac
  done < <(find -P "$canonical_source/$root" -type f -print0)
done

[[ -f $canonical_target/app.py ]] || { echo "allowlisted Portal app.py is required" >&2; exit 65; }
patch --batch --forward -p1 -d "$canonical_target" < "$patch_file"
mkdir -- "$canonical_target/test-data"
python3 - "$canonical_target/ai-canvas-test.json" "$canonical_target/test-data" <<'PY'
import json
from pathlib import Path
import sys
Path(sys.argv[1]).write_text(json.dumps({
    "canvas_origin": "http://127.0.0.1:8992",
    "portal_port": 9190,
    "test_data_dir": sys.argv[2],
}, sort_keys=True) + "\n", encoding="utf-8")
PY

created=0
trap - EXIT
echo "prepared isolated Portal test copy: $canonical_target"
