#!/usr/bin/env bash
cd "$(dirname "$0")/.."
for p in .lab/app.pid .lab/standin.pid; do [ -f "$p" ] && kill "$(cat "$p")" 2>/dev/null && rm -f "$p"; done
echo stopped
