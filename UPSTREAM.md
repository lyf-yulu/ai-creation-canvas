# Upstream source

This repository imports the `web/` frontend snapshot from
<https://github.com/basketikun/infinite-canvas> at commit
`9bccd0ff1a7057a835708a731644ab05371fea3b`.

The snapshot covers the upstream `web/` directory. The accompanying
`LICENSE`, `CHANGELOG.md`, and `VERSION` files are copied from the same pinned
source revision. The upstream project is licensed under AGPL-3.0; its license
and copyright notices are retained in this repository.

The import is reproducible with `scripts/import-upstream.sh`, which verifies
the source checkout's exact HEAD before replacing the snapshot.

This branch prohibits browser-held API keys, remote plugins, and dynamically
loaded scripts. Subsequent security adaptations enforce those restrictions on
top of this preserved upstream baseline.
