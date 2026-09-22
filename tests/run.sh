#!/usr/bin/env bash
# Hermes Deck test runner: the same gates CI runs, plus the two that need a
# real Omarchy/Qt6 host and therefore cannot run on a GitHub runner.
#
# Order matters: the syntax and floor checks are fast and name their own
# failure, so they come before the two suites.
set -euo pipefail
here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
root="$(dirname "$here")"

echo "== syntax =="
bash -n "$root/scripts/deck-act"
python3 -m py_compile "$root/scripts/deck-collect"
python3 -m py_compile "$here/check-qml-plaintext.py"
python3 -m py_compile "$here/check-secrets.py"

echo
echo "== secret / identity / artifact audit (pushed blobs) =="
python3 "$here/check-secrets.py"

echo
echo "== collector behavioral tests =="
python3 "$here/test_collect.py"

echo
echo "== action dispatcher tests =="
python3 "$here/test_act.py"

echo
echo "== QML plain-text floor =="
python3 "$here/check-qml-plaintext.py" "$root"

echo
echo "== tree hygiene and manifest contract =="
bash "$here/check-tree.sh" "$root"

echo
echo "== official plugin validator =="
if command -v omarchy >/dev/null 2>&1; then
  omarchy plugin validate "$root"
  echo "omarchy plugin validate: exit 0"
else
  echo "omarchy CLI not found; skipping plugin validate"
fi

# qmllint is a ratchet, not a gate. Omarchy's own first-party Panel.qml reports
# 96 warnings under this same invocation and roughly three quarters of ours are
# `missing-property` against the qs.Ui / qs.Commons singletons, whose members
# qmllint cannot see. A zero threshold would be red forever and teach everyone
# to ignore it, so this reports the count and fails only if it grows.
echo
echo "== qmllint (ratchet: must not grow) =="
QML_BUDGET_TOTAL=178
lint_one() {
  local subject="$1" scratch
  scratch="$(mktemp -d)"
  mkdir -p "$scratch/imports" "$scratch/qml/js"
  ln -sfn /usr/share/omarchy/shell "$scratch/imports/qs"
  cp "$root/js/DeckState.js" "$scratch/qml/js/" 2>/dev/null || true
  local f
  for f in "$root"/*.qml; do
    [ "$(basename "$f")" = "$(basename "$subject")" ] && continue
    cp "$f" "$scratch/qml/"
  done
  cp "$subject" "$scratch/qml/Subject.qml"
  local count
  count="$(timeout 120 /usr/lib/qt6/bin/qmllint -I "$scratch/imports" -I "$scratch/qml" \
    "$scratch/qml/Subject.qml" 2>&1 | grep -c '^Warning:' || true)"
  rm -rf "$scratch"
  printf '%s\n' "${count:-0}"
}
if [ -x /usr/lib/qt6/bin/qmllint ]; then
  total=0
  for f in "$root"/*.qml; do
    n="$(lint_one "$f")"
    total=$((total + n))
    printf '  %-18s %s\n' "$(basename "$f")" "$n"
  done
  echo "  total: $total (budget $QML_BUDGET_TOTAL)"
  if [ "$total" -gt "$QML_BUDGET_TOTAL" ]; then
    echo "qmllint ratchet: warning count grew past the recorded ceiling" >&2
    exit 1
  fi
else
  echo "  Qt6 qmllint not found at /usr/lib/qt6/bin/qmllint; skipping"
fi

echo
echo "all checks passed"
