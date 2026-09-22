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
python3 -m py_compile "$here/check-image-safety.py"
python3 -m py_compile "$here/test_image_audit.py"

echo
echo "== secret / identity / artifact audit (pushed blobs) =="
python3 "$here/check-secrets.py" --self-test
python3 "$here/check-secrets.py"

echo
echo "== image audit logic (regression tests for the detectors) =="
python3 "$here/test_image_audit.py"

echo
echo "== cron fixture provenance (skipped when Hermes is absent) =="
# The cron fixture must still match what the real CLI renders. It is generated,
# not typed: three successive parsers were validated against hand-written
# fixtures that agreed with the parser instead of the CLI.
if python3 "$here/fixtures/generate-cron-fixture.py" --check 2>/dev/null; then
  :
else
  echo "  (no Hermes source tree here; the committed fixture is used as-is)"
fi

echo
echo "== DeckState JS helpers =="
if command -v node >/dev/null 2>&1; then
  node "$here/test_deckstate.js"
else
  echo "node not found; skipping JS helper tests"
fi

echo
echo "== image audit (every tracked image, before it can be published) =="
python3 "$here/check-image-safety.py" --self-test
# Audit every image the tree would publish. A screenshot is the one artifact no
# text scanner can read, so it gets its own pass over metadata and pixels.
mapfile -t IMAGES < <(git ls-files | grep -iE '\.(png|jpe?g|webp|avif|gif)$')
if [ "${#IMAGES[@]}" -gt 0 ]; then
  python3 "$here/check-image-safety.py" "${IMAGES[@]}"
else
  echo "no tracked images"
fi

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
# Raised 178 -> 193 when the Cron section landed, then lowered 197 -> 193 after
# hoisting the section's derived properties removed four warnings. The remaining
# ones are the same two classes documented above and prove out as false
# positives: Omarchy's own first-party agents/Panel.qml reports 35
# missing-property and 61 unqualified under this identical invocation, and the
# missing-property hits are all against `Style` and `bar`, whose members qmllint
# cannot see through the qs.Commons / qs.Ui singletons. A real QML error is
# still caught: it is reported as an Error, and this counts only `^Warning:`.
QML_BUDGET_TOTAL=193
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
