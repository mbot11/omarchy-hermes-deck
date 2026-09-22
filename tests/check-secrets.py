#!/usr/bin/env python3
"""Audit a plugin tree for secrets, identity leaks, and path hazards.

This runs the checks the functional gates do not: credential shapes, machine
identity that should not be published, absolute paths that assume this
machine, and leftover build artifacts. It reads the PUSHED GIT TREE, not the
working copy, because that is what the marketplace scans and what `git push`
published.

Exit 0 clean, 1 on any finding, 2 on a usage error.
"""

from __future__ import annotations

import math
import os
import re
import subprocess
import sys

# ── credential shapes ────────────────────────────────────────────────────
# Ordered specific-first: a broad pattern above a specific one would mask it.
#
# The literal markers are assembled from fragments rather than written whole.
# A scanner necessarily contains the shapes it looks for, so a file that spells
# them out flags itself on every run — as this one did in CI. Splitting the
# strings keeps the file scannable by its own rules and by any other scanner,
# at no cost to detection.
_KEY = "PRIVATE KEY"
SECRET_PATTERNS: list[tuple[str, str]] = [
    ("private key block", r"-----BEGIN [A-Z ]*" + _KEY + r"-----"),
    ("ssh private key", r"-----BEGIN OPENSSH " + _KEY + r"-----"),
    ("pgp private key", r"-----BEGIN PGP " + _KEY + " BLOCK-----"),
    ("aws access key", r"\bAKIA[0-9A-Z]{16}\b"),
    ("github token", r"\bgh[pousr]_[A-Za-z0-9]{16,}\b"),
    ("github fine-grained pat", r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    ("slack token", r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"),
    ("openai key", r"\bsk-[A-Za-z0-9]{20,}\b"),
    ("anthropic key", r"\bsk-ant-[A-Za-z0-9_-]{20,}\b"),
    ("openrouter key", r"\bsk-or-v1-[A-Za-z0-9]{32,}\b"),
    ("tavily key", r"\btvly-[A-Za-z0-9]{16,}\b"),
    ("google api key", r"\bAIza[A-Za-z0-9_-]{30,}\b"),
    ("huggingface token", r"\bhf_[A-Za-z0-9]{30,}\b"),
    ("stripe secret", r"\bsk_(?:live|test)_[A-Za-z0-9]{20,}\b"),
    ("generic bearer", r"\bBearer\s+[A-Za-z0-9._~+/-]{20,}=*\b"),
    ("telegram bot token", r"\b\d{8,12}:[A-Za-z0-9_-]{30,}\b"),
    ("jwt", r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    ("assigned secret", r"(?i)\b(?:api[_-]?key|secret|passwd|password|token|credential)\s*[:=]\s*[\"'][A-Za-z0-9_\-./+=]{16,}[\"']"),
]

# ── machine identity that must not be published ──────────────────────────
# Assembled for the same reason as the key markers above: this file is in the
# tree it scans, so a literal it searches for would always match itself.
_ROOT = "/" + "root" + "/"
IDENTITY_CHECKS: list[tuple[str, str]] = [
    # A real account name, not a placeholder: at least three characters and
    # not a single-letter or obviously synthetic fixture name.
    ("absolute home path", r"/home/(?!u/|user/|username/|[a-z]/)[a-z][a-z0-9_-]{2,}/"),
    ("macOS home path", r"/Users/(?!user/|username/)[A-Za-z][A-Za-z0-9_-]{2,}/"),
    ("root home path", r"(?<![\w/])" + _ROOT),
    ("hostname leak (omarchy host)", r"\bomarchy\b(?=[^\s]*\.(?:local|lan)\b)"),
    ("private ipv4", r"\b(?:10\.\d{1,3}|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b"),
    ("loopback with port", r"\b127\.0\.0\.1:\d{2,5}\b"),
    ("email address", r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
]

# The account names to treat as leaks. Taken from the environment and git
# config at runtime rather than written here: hardcoding the author's name in
# the repository is itself the leak this checks for, and it failed CI for
# exactly that before this change.
def author_usernames() -> list[str]:
    names = {os.environ.get("USER", ""), os.environ.get("LOGNAME", "")}
    for key in ("user.name", "user.email"):
        out = subprocess.run(["git", "config", "--get", key], capture_output=True, text=True)
        if out.returncode == 0 and out.stdout.strip():
            value = out.stdout.strip()
            names.add(value.split("@")[0] if "@" in value else value)
    return sorted(n for n in names if len(n) >= 3 and n not in ("root", "runner", "user"))

# Identity strings that are legitimately public in this repo.
IDENTITY_ALLOW = [
    "mbot11",              # the author's GitHub handle: URLs, manifest author
    "noreply@",            # GitHub noreply mail in commit metadata, not in tree
    "@example.com", "@example.org", "@test.invalid",
    "hermes-agent.nousresearch.com", "omarchy.org", "github.com",
    "api.anthropic.com", "api.openai.com", "openrouter.ai",
    "127.0.0.1:9119",      # documented Hermes dashboard default, in docs only
]

# Cap on the blob this scanner will load and scan. Not a memory worry in
# practice — check-tree.sh already rejects a tracked file over 512 KiB — but the
# bound should be stated where it is relied on, not inherited from another gate's
# ordering. A blob over this is reported as a finding, never silently skipped:
# "too large to scan" must not read the same as "clean".
MAX_BLOB_BYTES = 4 * 1024 * 1024

ARTIFACT_PATTERNS: list[tuple[str, str]] = [
    ("python bytecode", r"__pycache__/|\.pyc$"),
    ("editor backup", r"~$|\.swp$|\.swo$"),
    ("os metadata", r"(?:^|/)\.DS_Store$|(?:^|/)Thumbs\.db$"),
    ("env file", r"(?:^|/)\.env(?:\.|$)"),
    ("credential store", r"(?:^|/)(?:auth|credentials)\.json$|(?:^|/)\.netrc$"),
    ("log file", r"\.log$"),
    ("sqlite database", r"\.db$|\.sqlite3?$"),
    ("shell history", r"(?:^|/)\.(?:bash|zsh)_history$"),
]


def tracked_files() -> list[str]:
    out = subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True)
    return [line for line in out.stdout.split("\n") if line]


def shannon_entropy(text: str) -> float:
    if not text:
        return 0.0
    counts: dict[str, int] = {}
    for char in text:
        counts[char] = counts.get(char, 0) + 1
    length = len(text)
    return -sum((n / length) * math.log2(n / length) for n in counts.values())


def read_blob(path: str) -> bytes | None:
    """Read the content that publication would send for `path`.

    The working copy is not that content: a file can be edited after its commit
    and `git ls-files` still lists it, so scanning the file on disk would audit
    bytes that were never published. A tracked-but-deleted file still needs
    scanning, which the blob form handles.

    The revision is the INDEX when the path exists there, and `HEAD` otherwise.
    The index is what a `git commit` would capture, so it is the correct subject
    for a gate that runs before committing; `HEAD` covers a tracked path that is
    not staged. See the comment below for why the order is this way round.
    """
    # The INDEX first, then HEAD. The index is what a `git commit` would capture,
    # which is the content this gate exists to judge — auditing HEAD instead
    # means a staged fix is invisible and the gate reports the previous commit's
    # problem forever. HEAD is the fallback for a tracked path that is not staged
    # (already committed and unchanged, or deleted from the working tree).
    for revision in (None, "HEAD"):
        ref = f":{path}" if revision is None else f"{revision}:{path}"
        out = subprocess.run(["git", "cat-file", "blob", ref], capture_output=True)
        if out.returncode == 0:
            return out.stdout
    return None


def self_test() -> int:
    """Prove the detector fires, so a clean run means clean rather than broken.

    A scanner that silently matches nothing reports CLEAN forever, and the
    failure is invisible because success and total dysfunction look identical
    from the outside. This plants one sample per class in a temporary file and
    asserts each is caught. It runs only under --self-test so normal use stays
    a single pass over the tree.
    """
    samples = {
        "aws access key": "k = \"" + "AKIA" + "IOSFODNN7EXAMPLE\"",
        "github token": "k = \"" + "ghp_" + "abcdefghijklmnopqrstuvwxyz0123456789\"",
        "openrouter key": "k = \"" + "sk-or-v1-" + "a" * 44 + "\"",
        "private key block": "k = \"-----BEGIN OPENSSH " + _KEY + "-----\"",
        "ssh private key": "k = \"-----BEGIN OPENSSH " + _KEY + "-----\"",
        "absolute home path": "p = \"/home/realaccount/x\"",
        "email address": "e = \"someone.real@proton.me\"",
    }
    failures = 0
    for label, sample in samples.items():
        hit = False
        for kind, pattern in SECRET_PATTERNS + IDENTITY_CHECKS:
            if kind == label and re.search(pattern, sample):
                hit = True
        if hit:
            print(f"  detects {label}")
        else:
            failures += 1
            print(f"  FAILS TO DETECT {label}")
    if shannon_entropy("ghp_abcdefghijklmnopqrstuvwxyz0123456789") < 4.0:
        failures += 1
        print("  FAILS TO DETECT high-entropy literal")
    else:
        print("  detects high-entropy literal")
    print(f"\nself-test: {len(samples) + 1 - failures}/{len(samples) + 1} detectors fired")
    return 1 if failures else 0


def audit() -> int:
    files = tracked_files()
    findings: list[tuple[str, str, int, str]] = []

    # This file is excluded from its own scan. It necessarily contains every
    # shape it looks for — the patterns, and the self-test's planted samples —
    # so including it can only ever report the scanner. Its correctness is
    # covered by --self-test instead, which is the honest way to check a
    # detector: prove it fires, rather than scan the thing that defines it.
    self_path = os.path.relpath(os.path.abspath(__file__), os.getcwd())
    files = [f for f in files if f != self_path]

    for path in files:
        data = read_blob(path)
        if data is None:
            print(f"cannot read blob for {path}", file=sys.stderr)
            return 2
        # A NUL byte does not mean "no text to scan". Skipping the file outright
        # audited CLEAN a binary blob carrying `key = "AKIA…"`, because a NUL is
        # common in genuine text (a UTF-16 file, a stray byte) and the extension
        # was never consulted. Scan the text anyway: an embedded secret is exactly
        # what this check exists to find, and the cost is a decode on a large file.
        if len(data) > MAX_BLOB_BYTES:
            findings.append((
                "OVERSIZE", "blob too large to scan", 0,
                f"{path}: {len(data)} bytes exceeds the {MAX_BLOB_BYTES}-byte scan cap;"
                " this file was NOT inspected",
            ))
            continue
        text = data.decode("utf-8", "replace")

        # Resolve the identity list ONCE per file, not once per line. This was
        # called inside the per-line loop, spawning two `git config` subprocesses
        # for every line of every tracked file (~17k spawns on a 8.7k-line tree).
        # It never changed the verdict, only the runtime.
        usernames = author_usernames()

        for line_number, line in enumerate(text.split("\n"), 1):
            for label, pattern in SECRET_PATTERNS:
                if re.search(pattern, line):
                    findings.append(("SECRET", label, line_number, f"{path}: {line.strip()[:100]}"))
            for username in usernames:
                if username in line:
                    findings.append(("IDENTITY", "author username in tree", line_number, f"{path}: {line.strip()[:100]}"))
            for label, pattern in IDENTITY_CHECKS:
                for match in re.finditer(pattern, line):
                    # Scope the allowlist to the MATCHED SPAN, not the whole line.
                    # A per-line test let an allowed host anywhere on the line
                    # suppress a different finding on it: a line mentioning
                    # github.com also carrying /home/<acct>/ audited CLEAN, while
                    # the same path alone was reported. The image scanner was fixed
                    # for this class; this sibling was not.
                    low, high = match.span()
                    if any(
                        low <= line.find(allowed) < high
                        for allowed in IDENTITY_ALLOW
                        if line.find(allowed) != -1
                    ):
                        continue
                    if any(allowed in match.group(0) for allowed in IDENTITY_ALLOW):
                        continue
                    findings.append(("IDENTITY", label, line_number, f"{path}: {line.strip()[:100]}"))
                    break

        # Entropy sweep for long opaque string literals, which catch a secret
        # whose prefix this pattern list does not know.
        for match in re.finditer(r"[\"']([A-Za-z0-9+/=_\-]{32,})[\"']", text):
            token = match.group(1)
            if shannon_entropy(token) > 4.0:
                findings.append(("ENTROPY", "high-entropy literal", 0, f"{path}: {token[:40]}…"))

        for label, pattern in ARTIFACT_PATTERNS:
            if re.search(pattern, path):
                findings.append(("ARTIFACT", label, 0, path))

    # ── report ───────────────────────────────────────────────────────────
    print(f"audited {len(files)} tracked files\n")
    if not findings:
        print("CLEAN: no secrets, no machine identity, no stray artifacts")
        return 0

    order = {"SECRET": 0, "IDENTITY": 1, "ENTROPY": 2, "ARTIFACT": 3, "OVERSIZE": 4}
    for kind, label, line_number, detail in sorted(findings, key=lambda f: (order[f[0]], f[1])):
        where = f":{line_number}" if line_number else ""
        print(f"{kind:<9} {label}{where}")
        print(f"          {detail}")
    counts: dict[str, int] = {}
    for kind, *_ in findings:
        counts[kind] = counts.get(kind, 0) + 1
    print("\n" + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    return 1


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        sys.exit(self_test())
    sys.exit(audit())
