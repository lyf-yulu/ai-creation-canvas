#!/usr/bin/env bash
set -euo pipefail

# This is a defense-in-depth source tripwire, not a substitute for behavioral tests
# of URL normalization, asset ownership, and job polling.
#
# Each detection command exits 0 when it FINDS a violation; the scan fails the
# build on any match. (Negating with `!` would exempt the command from errexit
# and silently turn matches into passes, so matches are checked explicitly.)
reject() {
    local matches
    if matches=$("$@" 2>&1); then
        printf '%s\n' "$matches" >&2
        echo "security scan rejected a match" >&2
        exit 1
    fi
}

reject rg -n --glob '!web/src/test/**' '\beval\s*\(|\b(?:new\s+)?Function\s*\(|runModelPlugin|VITE_PLUGIN_REGISTRY_URL' web/src
reject rg -n --glob '!web/src/test/**' 'import\s*\(\s*/\*\s*@vite-ignore|plugins/index\.json|fetchOfficialPlugins|installPluginFromUrl' web/src
reject rg -n --glob '!web/src/test/**' 'api[_-]?key\s*[:=]|Authorization\s*[:=].*Bearer|VITE_(?:.*API|.*KEY|PLUGIN_REGISTRY_URL)' web/src
# Provider origins and route templates are code-owned. No browser surface may
# offer Key, credential reference, arbitrary Base URL, or dynamic code controls.
reject rg -ni --glob '!web/src/test/**' '<(?:input|textarea|select)[^>]+(?:name|aria-label)=["'"'"'][^"'"'"']*(?:api[ _-]?key|credential|凭据引用|base[ _-]?url|服务地址)' web/src
reject rg -n --glob '!web/src/test/**' 'base[_-]?url\s*[:=]' web/src
# Dynamic imports need string literals; eval/exec calls are banned. The `(?<!\.)`
# lookbehind exempts `regex.exec(...)` method calls, which are not code execution.
reject rg -nP --glob '!web/src/test/**' 'import\s*\(\s*[^"'"'"']|\beval\s*\(|(?<!\.)\bexec\s*\(' web/src

# Committed deployment-shaped config must not contain credential literals. The
# documented fake example and behavioral test fixtures are intentional.
reject rg -n --glob '*.{yaml,yml,json,toml,env}' --glob '!server/config/credential-pools.example.yaml' --glob '!tests/**' --glob '!web/src/test/**' '^\s*api_key\s*:' .

# Runtime and secret material must never be tracked, even when a developer has
# created an ignored local acceptance directory.
reject bash -c 'git ls-files | rg "(^|/)(?:\.acceptance-[^/]+|secrets|state|outputs|archives|uploads|logs|requests)(/|$)|\.(?:sqlite3?|db|jsonl|pem|p12|pfx|key)$"'
