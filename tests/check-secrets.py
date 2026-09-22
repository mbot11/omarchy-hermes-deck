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
import re
import subprocess
import sys

# ── credential shapes ────────────────────────────────────────────────────
# Ordered specific-first: a broad pattern above a specific one would mask it.
SECRET_PATTERNS: list[tuple[str, str]] = [
    ("private key block", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    ("ssh private key", r"-----BEGIN OPENSSH PRIVATE KEY-----"),
    ("pgp private key", r"-----BEGIN PGP PRIVATE KEY BLOCK-----"),
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
# The author's own username is expected in URLs and the license; it is only a
# leak when it appears inside a filesystem path, i.e. as a home directory.
IDENTITY_CHECKS: list[tuple[str, str]] = [
    # A real account name, not a placeholder: at least three characters and
    # not a single-letter or obviously synthetic fixture name.
    ("absolute home path", r"/home/(?!u/|user/|username/|[a-z]/)[a-z][a-z0-9_-]{2,}/"),
    ("macOS home path", r"/Users/(?!user/|username/)[A-Za-z][A-Za-z0-9_-]{2,}/"),
    ("root home path", r"(?<![\w/])/root/"),
    ("hostname leak (omarchy host)", r"\bomarchy\b(?=[^\s]*\.(?:local|lan)\b)"),
    ("private ipv4", r"\b(?:10\.\d{1,3}|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b"),
    ("loopback with port", r"\b127\.0\.0\.1:\d{2,5}\b"),
    ("email address", r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
]

# The real account name on the author's machine. Checked explicitly below as
# well, because it is the one string whose presence anywhere is a leak.
AUTHOR_USERNAMES = ["mbtruby"]

# Identity strings that are legitimately public in this repo.
IDENTITY_ALLOW = [
    "mbot11",              # the author's GitHub handle: URLs, manifest author
    "noreply@",            # GitHub noreply mail in commit metadata, not in tree
    "@example.com", "@example.org", "@test.invalid",
    "hermes-agent.nousresearch.com", "omarchy.org", "github.com",
    "api.anthropic.com", "api.openai.com", "openrouter.ai",
    "127.0.0.1:9119",      # documented Hermes dashboard default, in docs only
]

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
    """Read the committed content of `path`, not the working copy.

    The working copy is not what publication scans: a file can be edited after
    the commit and `git ls-files` still lists it. Reading the blob means this
    audit describes exactly what was pushed. A file that is tracked but
    deleted on disk is still scanned, which is the point.
    """
    out = subprocess.run(["git", "cat-file", "blob", f"HEAD:{path}"], capture_output=True)
    if out.returncode != 0:
        return None
    return out.stdout


def audit() -> int:
    files = tracked_files()
    findings: list[tuple[str, str, int, str]] = []

    for path in files:
        data = read_blob(path)
        if data is None:
            print(f"cannot read blob for {path}", file=sys.stderr)
            return 2
        if b"\x00" in data[:8192]:
            continue  # binary asset; no text patterns apply
        text = data.decode("utf-8", "replace")

        for line_number, line in enumerate(text.split("\n"), 1):
            for label, pattern in SECRET_PATTERNS:
                if re.search(pattern, line):
                    findings.append(("SECRET", label, line_number, f"{path}: {line.strip()[:100]}"))
            for username in AUTHOR_USERNAMES:
                if username in line:
                    findings.append(("IDENTITY", "author username in tree", line_number, f"{path}: {line.strip()[:100]}"))
            for label, pattern in IDENTITY_CHECKS:
                for match in re.finditer(pattern, line):
                    if any(allowed in line for allowed in IDENTITY_ALLOW):
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

    order = {"SECRET": 0, "IDENTITY": 1, "ENTROPY": 2, "ARTIFACT": 3}
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
    sys.exit(audit())
