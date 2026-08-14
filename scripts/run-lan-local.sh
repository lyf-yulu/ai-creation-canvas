#!/bin/sh
set -eu

: "${AICC_LAN_ORIGIN:?AICC_LAN_ORIGIN is required, for example http://192.168.1.20:8992}"
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
AICC_LOCAL_HOST=${AICC_LOCAL_HOST:-0.0.0.0}
AICC_LOCAL_ORIGIN=$AICC_LAN_ORIGIN
AICC_LOCAL_DATA=${AICC_LOCAL_DATA:-"$(CDPATH= cd -- "$script_dir/.." && pwd)/.local-lan-data"}
export AICC_LOCAL_HOST AICC_LOCAL_ORIGIN AICC_LOCAL_DATA
exec "$script_dir/run-local.sh"
