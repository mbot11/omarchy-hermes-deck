#!/usr/bin/env bash
# Hermes Deck test runner: syntax checks, behavioral collector tests,
# and the official plugin validator when the omarchy CLI is available.
set -euo pipefail
here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
root="$(dirname "$here")"
bash -n "$root/scripts/deck-act"
python3 -m py_compile "$root/scripts/deck-collect"
python3 "$here/test_collect.py"
python3 "$here/test_act.py"

if command -v omarchy >/dev/null 2>&1; then
  omarchy plugin validate "$root"
else
  echo "omarchy CLI not found; skipping plugin validate"
fi

if command -v qmllint >/dev/null 2>&1; then
  qmllint -I /usr/lib/qt6/qml -I /usr/share/omarchy/shell \
    "$root/BarWidget.qml" "$root/Panel.qml" "$root/Service.qml" "$root/Modal.qml"
else
  echo "qmllint not found; skipping QML lint"
fi

echo "all checks passed"
