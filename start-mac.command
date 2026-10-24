#!/bin/bash
# Double-click to start Resonance on macOS. First run sets everything up.
cd "$(dirname "$0")" || exit 1
if command -v python3 >/dev/null 2>&1; then
  python3 resonance/launch.py "$@"
else
  echo "Python 3 is not installed. Install Python 3.12 from https://www.python.org/downloads/"
  echo "then double-click this file again."
fi
read -n 1 -s -r -p "Press any key to close this window."
