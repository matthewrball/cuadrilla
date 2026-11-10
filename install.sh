#!/bin/sh
set -eu
cd "$(dirname "$0")"
export PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m cuadrilla install
