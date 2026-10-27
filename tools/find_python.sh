#!/usr/bin/env bash
# Prints the path of a Python >= 3.9 and exits 0, or exits 1. Honors $PYTHON.
ok() { "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; }

if [[ -n "${PYTHON:-}" ]]; then
  if ok "$PYTHON"; then command -v "$PYTHON"; exit 0; fi
  echo "PYTHON=$PYTHON is not a Python 3.9+ interpreter." >&2
  exit 1
fi

shopt -s nullglob
candidates=(
  python3.14 python3.13 python3.12 python3.11 python3.10 python3.9
  /opt/homebrew/bin/python3 /usr/local/bin/python3
  /Library/Frameworks/Python.framework/Versions/3.*/bin/python3
  python3
  /usr/bin/python3   # last: without Xcode tools this is a stub that opens an install dialog
)
for c in "${candidates[@]}"; do
  if command -v "$c" >/dev/null 2>&1 && ok "$c"; then command -v "$c"; exit 0; fi
done
exit 1
