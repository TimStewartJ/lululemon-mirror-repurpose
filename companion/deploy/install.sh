#!/usr/bin/env bash
# Installs what the companion needs on Linux: the Node packages, a Python
# environment for speech-to-text under the state directory, and the speech
# model. Safe to run again; it only adds what is missing.
#
#   deploy/install.sh [--state DIR] [--model NAME] [--no-gpu]

state="${MIRROR_COMPANION_STATE:-$HOME/.local/state/mirror-companion}"
model="small.en"
gpu=1
while [ $# -gt 0 ]; do
  case "$1" in
    --state) state="$2"; shift 2 ;;
    --model) model="$2"; shift 2 ;;
    --no-gpu) gpu=0; shift ;;
    *)
      echo "Unknown option $1. Usage: install.sh [--state DIR] [--model NAME] [--no-gpu]" >&2
      exit 2
      ;;
  esac
done

# nvm.sh reads unset variables, so it is loaded before the strict mode below.
if ! command -v node >/dev/null 2>&1 && [ -s "${NVM_DIR:-$HOME/.nvm}/nvm.sh" ]; then
  . "${NVM_DIR:-$HOME/.nvm}/nvm.sh"
fi
set -euo pipefail

if ! command -v node >/dev/null 2>&1; then
  echo "Node.js was not found. Install Node 24, for example with nvm, and run this again." >&2
  exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 was not found. Install Python 3.10 or later and run this again." >&2
  exit 1
fi

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "Installing the Node packages."
(cd "$here" && npm ci --omit=dev --no-audit --no-fund)

mkdir -p "$state"
chmod 700 "$state"
python="$state/venv/bin/python"
if [ ! -x "$python" ]; then
  echo "Creating the Python environment in $state/venv."
  python3 -m venv "$state/venv"
fi

echo "Installing the speech packages. The first time this downloads about 1.5 GB."
"$python" -m pip install --quiet --upgrade pip
"$python" -m pip install --quiet -r "$here/deploy/requirements.txt"
if [ "$gpu" = 1 ]; then
  "$python" -m pip install --quiet -r "$here/deploy/requirements-gpu.txt"
fi

echo "Fetching the speech model $model into $state/models."
"$python" - "$model" "$state/models" <<'PY'
import sys
from faster_whisper import download_model

download_model(sys.argv[1], cache_dir=sys.argv[2])
PY

echo "Done. The speech-to-text Python is $python."
