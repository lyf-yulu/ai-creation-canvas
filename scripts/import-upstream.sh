#!/usr/bin/env bash
set -euo pipefail

source_dir="${1:?usage: import-upstream.sh /path/to/infinite-canvas}"
expected="9bccd0ff1a7057a835708a731644ab05371fea3b"

test "$(git -C "$source_dir" rev-parse HEAD)" = "$expected"
rsync -a --delete --exclude='.git/' "$source_dir/web/" web/
cp "$source_dir/LICENSE" LICENSE
cp "$source_dir/CHANGELOG.md" CHANGELOG.md
cp "$source_dir/VERSION" VERSION
