#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

source ~/CARLA_0916/.venv/bin/activate

python -m myagent.bridge.server
