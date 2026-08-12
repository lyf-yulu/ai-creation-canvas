#!/usr/bin/env bash
set -euo pipefail

# This is a defense-in-depth source tripwire, not a substitute for behavioral tests
# of URL normalization, asset ownership, and job polling.
! rg -n --glob '!web/src/test/**' '\beval\s*\(|\b(?:new\s+)?Function\s*\(|runModelPlugin|VITE_PLUGIN_REGISTRY_URL' web/src
! rg -n --glob '!web/src/test/**' 'import\s*\(\s*/\*\s*@vite-ignore|plugins/index\.json|fetchOfficialPlugins|installPluginFromUrl' web/src
! rg -n --glob '!web/src/test/**' 'api[_-]?key\s*[:=]|Authorization\s*[:=].*Bearer|VITE_(?:.*API|.*KEY|PLUGIN_REGISTRY_URL)' web/src
# Provider origins and route templates are code-owned. No browser surface may
# offer Key, credential reference, arbitrary Base URL, or dynamic code controls.
! rg -ni --glob '!web/src/test/**' "<(?:input|textarea|select)[^>]+(?:name|aria-label)=[\"'][^\"']*(?:api[ _-]?key|credential|凭据引用|base[ _-]?url|服务地址)" web/src
! rg -n --glob '!web/src/test/**' 'base[_-]?url\s*[:=]' web/src
! rg -n --glob '!web/src/test/**' "import\s*\(\s*[^\"']|\b(?:eval|exec)\s*\(" web/src

# Committed deployment-shaped config must not contain credential literals. The
# documented fake example and behavioral test fixtures are intentional.
! rg -n --glob '*.{yaml,yml,json,toml,env}' --glob '!server/config/credential-pools.example.yaml' --glob '!tests/**' --glob '!web/src/test/**' '^\s*api_key\s*:' .

# Runtime and secret material must never be tracked, even when a developer has
# created an ignored local acceptance directory.
! git ls-files | rg '(^|/)(?:\.acceptance-[^/]+|secrets|state|outputs|archives|uploads|logs|requests)(/|$)|\.(?:sqlite3?|db|jsonl|pem|p12|pfx|key)$'
