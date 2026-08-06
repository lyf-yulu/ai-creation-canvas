#!/usr/bin/env bash
set -euo pipefail

! rg -n 'new Function|VITE_PLUGIN_REGISTRY_URL|runModelPlugin|apiKey:\s*string|Authorization:\s*`Bearer' web/src
! rg -n 'import\(/\* @vite-ignore \*/|plugins/index\.json|VITE_(?:.*API|.*KEY|PLUGIN_REGISTRY_URL)' web/src
