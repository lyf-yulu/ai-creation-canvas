#!/usr/bin/env bash
set -euo pipefail

# This is a defense-in-depth source tripwire, not a substitute for behavioral tests
# of URL normalization, asset ownership, and job polling.
! rg -n --glob '!test/**' '\beval\s*\(|\b(?:new\s+)?Function\s*\(|runModelPlugin|VITE_PLUGIN_REGISTRY_URL' web/src
! rg -n --glob '!test/**' 'import\s*\(\s*/\*\s*@vite-ignore|plugins/index\.json|fetchOfficialPlugins|installPluginFromUrl' web/src
! rg -n --glob '!test/**' '(?:api[_-]?key|base[_-]?url)\s*[:=]|Authorization\s*[:=].*Bearer|VITE_(?:.*API|.*KEY|PLUGIN_REGISTRY_URL)' web/src
