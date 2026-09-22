#!/usr/bin/env bash
# Tree hygiene checks the marketplace validates at submission time.
#
# Each check mirrors a rule the marketplace build script applies to the pushed
# git tree, not to the working copy, so this reads `git ls-files` rather than
# walking the directory. A plugin that passes here and fails there is a plugin
# whose check was pointed at the wrong tree.
#
# Exit 0 when clean, 1 on any violation, 2 on a usage error.
set -uo pipefail

root="${1:-.}"
cd "$root" || { echo "check-tree: cannot enter $root" >&2; exit 2; }

git rev-parse --git-dir >/dev/null 2>&1 || {
  echo "check-tree: not a git checkout; the marketplace reads the git tree" >&2
  exit 2
}

fail=0
note() { printf '%s %s\n' "$1" "$2"; }
pass() { note "PASS " "$1"; }
bad()  { note "FAIL " "$1"; fail=1; }

# --- no symlinks anywhere: a mode-120000 blob is a fatal manifest-invalid,
# --- and the security scanner never looks inside docs/ or tests/.
links="$(git ls-files -s | awk -F'\t' '$1 ~ /^120000/ { print $2 }')"
if [ -z "$links" ]; then
  pass "no symlinks in the tree"
else
  bad "symlinks are not allowed in plugin folders: $(echo "$links" | tr '\n' ' ')"
fi

# --- control characters in a tracked path
if git ls-files | grep -qE '[[:cntrl:]]'; then
  bad "a tracked path contains a control character"
else
  pass "every tracked path is plain"
fi

# --- exactly one manifest.json, at the repository root
any_manifest="$(git ls-files | grep -icE '^([^/]+/)?manifest\.json$' || true)"
root_manifest="$(git ls-files | grep -cE '^manifest\.json$' || true)"
if [ "$any_manifest" = "1" ] && [ "$root_manifest" = "1" ]; then
  pass "exactly one manifest.json, at the root"
else
  bad "one root manifest required (any=$any_manifest root=$root_manifest)"
fi

# --- README and LICENSE at the root
if git ls-files | grep -qiE '^readme(\.[^/]+)?$'; then
  pass "root README present"
else
  bad "a README is required in the repository root"
fi
if git ls-files | grep -qiE '^(licen[cs]e|copying)(\.[^/]+)?$'; then
  pass "root license present"
else
  bad "a license file is required in the repository root"
fi

# --- every entry point declared in the manifest must be a tracked blob, and
# --- must not live under a scan-excluded directory (docs/ tests/ .github/).
if [ -f manifest.json ]; then
  python3 - <<'PY'
import json, re, subprocess, sys

tracked = set(subprocess.run(["git", "ls-files"], capture_output=True,
                             text=True, check=True).stdout.split("\n"))
try:
    m = json.load(open("manifest.json", encoding="utf-8"))
except Exception as exc:
    print(f"FAIL manifest.json does not parse: {exc}")
    sys.exit(1)

problems = []

if m.get("schemaVersion") != 1:
    problems.append("schemaVersion must be exactly 1")

pid = m.get("id", "")
if not isinstance(pid, str) or not re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]*$", pid):
    problems.append("manifest id contains unsupported characters")
elif pid != pid.lower():
    problems.append("community manifest ids must be lowercase")

for field in ("id", "name", "version", "author", "description"):
    value = m.get(field)
    if not isinstance(value, str) or not value.strip():
        problems.append(f'manifest field "{field}" is required')

kinds = m.get("kinds") or []
allowed = {"bar", "bar-widget", "menu", "overlay", "panel", "service"}
unknown = [k for k in kinds if k not in allowed]
if not kinds or unknown:
    problems.append(f"unsupported kinds: {unknown or 'none declared'}")

eps = m.get("entryPoints")
if not isinstance(eps, dict) or not eps:
    problems.append("entryPoints must be a non-empty object")
else:
    key = lambda k: "barWidget" if k == "bar-widget" else k
    for kind in kinds:
        if key(kind) not in eps:
            problems.append(f'entry point for kind "{kind}" is missing')
    excluded = {".github", "coverage", "docs", "fixtures", "node_modules",
                "spec", "specs", "test", "tests"}
    for name, path in eps.items():
        if not isinstance(path, str) or path.startswith("/") or ".." in path:
            problems.append(f"entry point {name} is not a safe relative path")
            continue
        if path not in tracked:
            problems.append(f"declared entry point is missing from the tree: {path}")
        if any(part in excluded for part in path.split("/")[:-1]):
            problems.append(f"entry point {path} sits under a scan-excluded directory")

bw = m.get("barWidget")
if isinstance(bw, dict):
    defaults = sorted((bw.get("defaults") or {}).keys())
    schema = sorted(e.get("key") for e in (bw.get("schema") or [])
                    if isinstance(e, dict) and isinstance(e.get("key"), str))
    if defaults != schema:
        problems.append("barWidget schema keys and defaults disagree")
    for entry in bw.get("schema") or []:
        if isinstance(entry, dict) and not all(
                k in entry for k in ("key", "type", "label", "defaultValue")):
            problems.append(f'setting {entry.get("key")} lacks key/type/label/defaultValue')
    section = bw.get("defaultSection")
    if section is not None and section not in ("left", "center", "right"):
        problems.append("barWidget.defaultSection must be left, center, or right")

if problems:
    for problem in problems:
        print(f"FAIL {problem}")
    sys.exit(1)
print("PASS manifest contract (schemaVersion, fields, kinds, entry points, settings)")
PY
  [ $? -ne 0 ] && fail=1
else
  bad "manifest.json is missing"
fi

# --- the marketplace security baseline refuses an oversized scan; the two
# --- limits that actually bind for a plugin this size are per-file and total.
python3 - <<'PY'
import os, subprocess, sys

per_file, total_cap = 512 * 1024, 8 * 1024 * 1024
files = [f for f in subprocess.run(["git", "ls-files"], capture_output=True,
                                   text=True, check=True).stdout.split("\n") if f]
big = [f for f in files if os.path.isfile(f) and os.path.getsize(f) > per_file]
total = sum(os.path.getsize(f) for f in files if os.path.isfile(f))
if big:
    print(f"FAIL {big[0]} exceeds the 512 KiB per-file limit")
    sys.exit(1)
if total > total_cap:
    print(f"FAIL tracked bytes {total} exceed the 8 MiB snapshot limit")
    sys.exit(1)
print(f"PASS tracked size within limits ({total} B across {len(files)} files)")
PY
[ $? -ne 0 ] && fail=1

echo
if [ "$fail" -eq 0 ]; then
  echo "tree hygiene: clean"
else
  echo "tree hygiene: violations above must be fixed before submission" >&2
fi
exit "$fail"
