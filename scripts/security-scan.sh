#!/usr/bin/env bash
set -euo pipefail

# This is a defense-in-depth source tripwire, not a substitute for behavioral tests
# of URL normalization, asset ownership, and job polling.
! rg -n --glob '!web/src/test/**' '\beval\s*\(|\b(?:new\s+)?Function\s*\(|runModelPlugin|VITE_PLUGIN_REGISTRY_URL' web/src
! rg -n --glob '!web/src/test/**' 'import\s*\(\s*/\*\s*@vite-ignore|plugins/index\.json|fetchOfficialPlugins|installPluginFromUrl' web/src
! rg -n --glob '!web/src/test/**' 'api[_-]?key\s*[:=]|Authorization\s*[:=].*Bearer|VITE_(?:.*API|.*KEY|PLUGIN_REGISTRY_URL)' web/src
# Provider origins are administrator-managed data, not credentials. Keep them
# confined to the fixed admin API and page; ordinary canvas code remains unable
# to introduce a remote execution origin.
! rg -n --glob '!web/src/test/**' --glob '!web/src/api/admin.ts' --glob '!web/src/pages/admin/models.tsx' 'base[_-]?url\s*[:=]' web/src
