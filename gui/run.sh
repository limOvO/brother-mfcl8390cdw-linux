#!/usr/bin/env bash
# Brother Studio 啟動器
cd "$(dirname "$(readlink -f "$0")")" || exit 1
exec python3 brother_studio.py "$@"
