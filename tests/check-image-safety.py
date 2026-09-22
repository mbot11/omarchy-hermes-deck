#!/usr/bin/env python3
"""Audit an image before it is published — screenshots, previews, diagrams.

A screenshot is a document, and it is the one artifact in a repository that no
text scanner can read. `check-secrets.py` walks text blobs; a PNG is opaque to
it, so a capture of a live desktop can carry a session title, a working
directory, a hostname, a terminal scrollback or an API key straight into a
public listing while every text gate reports clean.

This reads an image two ways and refuses anything it cannot vouch for:

  * **Metadata** — PNG text chunks (tEXt/zTXt/iTXt), EXIF, ICC profiles and any
    embedded comment. Author names, machine names, tool paths and GPS
    coordinates live here, and they survive cropping.
  * **Pixels** — OCR over the image with tesseract, then the same credential and
    identity shapes `check-secrets.py` applies to text. OCR is imperfect, so a
    miss is possible; it is a strong filter, not a proof. Say so in the verdict.

Usage:
  check-image-safety.py IMAGE [IMAGE ...]   audit images
  check-image-safety.py --self-test         prove the detectors fire
  check-image-safety.py --explain IMAGE     print what was extracted, no verdict

Exit 0 when every image is clean, 1 on any finding, 2 on a usage error.
"""

from __future__ import annotations

import os
import re
import struct
import subprocess
import sys
from pathlib import Path

def _load_secret_shapes():
    """Load the shared pattern tables out of `check-secrets.py`.

    The filename has a hyphen, so it is not importable by name; it is loaded by
    path instead. Reusing one source keeps an image and a text file judged by
    identical shapes — a second copy would drift.
    """
    import importlib.util

    source = Path(__file__).resolve().parent / "check-secrets.py"
    spec = importlib.util.spec_from_file_location("check_secrets", str(source))
    if spec is None or spec.loader is None:
        raise SystemExit("check-image-safety: cannot load check-secrets.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_secrets = _load_secret_shapes()
SECRET_PATTERNS = _secrets.SECRET_PATTERNS
IDENTITY_CHECKS = _secrets.IDENTITY_CHECKS
author_usernames = _secrets.author_usernames

# Text that a screenshot of a terminal or app window routinely contains and that
# must never reach a public listing, beyond the credential shapes.
CONTEXT_PATTERNS: list[tuple[str, str]] = [
    ("absolute home path in pixels", r"/home/(?!u/|user/|username/)[a-z][a-z0-9_-]{2,}/"),
    # A shell prompt prints `~/Work/...`, not `/home/<user>/Work`, so the
    # absolute-path pattern above misses the most common real leak: a
    # screenshot of a terminal whose prompt shows a working directory. This
    # requires a path separator or a dot-directory after the tilde, so ordinary
    # prose containing `~` (an approximation, a range) is not a finding.
    ("tilde home path", r"~/(?![ \t])\S"),
    ("prompt with cwd", r"[$#]\s*(?:cd\s+)?(?:/|~)[\w./-]{3,}"),
    ("shell prompt line", r"^\s*[a-z][\w-]*@[\w.-]+\s*[:$#]"),
    ("git remote url", r"(?:git@|https://)[\w.-]*(?:github|gitlab)\.com[:/][\w./-]+"),
    ("private ipv4", r"\b(?:10\.\d{1,3}|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b"),
    ("loopback with port", r"\b127\.0\.0\.1:\d{2,5}\b"),
    ("email address", r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    ("telegram bot token", r"\b\d{8,12}:[A-Za-z0-9_-]{30,}\b"),
    ("assigned secret", r"(?i)\b(?:api[_-]?key|secret|password|token|credential)\s*[:=]\s*[\"']?[A-Za-z0-9_\-./+=]{16,}"),
]

# OCR artefacts that look like a finding but are not: this file's own pattern
# literals cannot appear in pixels, but tesseract does mangle glyphs, and a
# version string or a hash is not a secret.
# Deliberately no OCR-noise allowlist. One existed and was removed: any
# exemption on a pre-publish scanner has to prove it cannot hide a real finding,
# and both shapes tried here could. A per-line exemption suppressed a credential
# on any line containing "gateway" or "providers"; a span-scoped one suppressed
# a credential whose *value* contained a product word
# (`api_key = ollama_cloud_prod_...`) and any identity under a home directory
# named after the distro. Over-reporting is the correct
# failure direction here: a false positive costs one review of a cropped image,
# a false negative publishes the secret. If OCR noise ever becomes a real
# problem, fix it by reading the image at higher resolution, not by whitelisting.

METADATA_KEYS_OF_INTEREST = (
    "author", "artist", "comment", "copyright", "description", "software",
    "source", "title", "hostcomputer", "make", "model", "gps", "xml",
)

# Values this script synthesises for compressed text chunks. Membership is
# tested against the literal placeholder, never as a substring: every comment
# written by an image tool contains "png", so a substring test exempts the very
# field that carries the author's name.
PLACEHOLDER_VALUE = re.compile(r"^<(?:zTXt|iTXt)>$")

# A gate on the publish path must terminate and must not be a memory amplifier.
# Neither bound has a legitimate reason to be larger: tesseract on a 64 MiB
# image is already pathological, and no screenshot preview approaches it.
OCR_TIMEOUT_SECONDS = 60
MAX_IMAGE_BYTES = 64 * 1024 * 1024


def png_text_chunks(data: bytes) -> dict[str, str]:
    """Pull tEXt/zTXt/iTXt chunks out of a PNG without a decoder.

    These survive cropping and resizing and routinely carry the tool that made
    the image plus the author and machine name.
    """
    out: dict[str, str] = {}
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return out
    offset = 8
    while offset + 8 <= len(data):
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        kind = data[offset + 4 : offset + 8]
        body = data[offset + 8 : offset + 8 + length]
        if kind == b"tEXt":
            key, _, value = body.partition(b"\x00")
            out[key.decode("latin-1", "replace")] = value.decode("latin-1", "replace")
        elif kind in (b"zTXt", b"iTXt"):
            key = body.split(b"\x00", 1)[0].decode("latin-1", "replace")
            out[key] = f"<{kind.decode()}>"
        if kind == b"IEND":
            break
        offset += 12 + length
    return out


def image_metadata(path: Path) -> dict[str, str]:
    """Every text-bearing metadata field the image carries."""
    size = path.stat().st_size
    if size > MAX_IMAGE_BYTES:
        raise ValueError(
            f"{path} is {size} bytes, over the {MAX_IMAGE_BYTES}-byte cap;"
            " refusing to read it"
        )
    out: dict[str, str] = {}
    data = path.read_bytes()
    out.update({f"png:{k}": v for k, v in png_text_chunks(data).items()})
    try:
        from PIL import Image

        with Image.open(path) as im:
            for key, value in (im.info or {}).items():
                if isinstance(value, (str, bytes)):
                    text = value.decode("utf-8", "replace") if isinstance(value, bytes) else value
                    out.setdefault(f"info:{key}", text)
            exif = getattr(im, "getexif", lambda: {})() or {}
            for tag, value in exif.items():
                if isinstance(value, (str, bytes)):
                    text = value.decode("utf-8", "replace") if isinstance(value, bytes) else value
                    out[f"exif:{tag}"] = text
    except Exception as exc:  # noqa: BLE001
        out["info:unreadable"] = f"{type(exc).__name__}: {exc}"
    return out


def ocr_text(path: Path) -> str:
    """OCR the pixels. Empty string when tesseract is unavailable or too slow."""
    if not _which("tesseract"):
        return ""
    try:
        out = subprocess.run(
            ["tesseract", str(path), "stdout", "--psm", "6"],
            capture_output=True,
            text=True,
            timeout=OCR_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        print(f"  WARNING: tesseract exceeded {OCR_TIMEOUT_SECONDS}s on {path};"
              " pixels were NOT fully read. Treat this result as partial.",
              file=sys.stderr)
        return ""
    return out.stdout if out.returncode == 0 else ""


def _which(name: str) -> bool:
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        candidate = Path(directory) / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return True
    return False


def scan_text(text: str, origin: str) -> list[tuple[str, str, str]]:
    findings: list[tuple[str, str, str]] = []
    for line in text.split("\n"):
        if not line.strip():
            continue
        for label, pattern in SECRET_PATTERNS + IDENTITY_CHECKS + CONTEXT_PATTERNS:
            if re.search(pattern, line):
                findings.append((origin, label, line.strip()[:110]))
        for username in author_usernames():
            if username in line:
                findings.append((origin, "author account name", line.strip()[:110]))
    return findings


def audit_image(path: Path, explain: bool = False) -> list[tuple[str, str, str]]:
    print(f"auditing {path}")
    metadata = image_metadata(path)
    findings: list[tuple[str, str, str]] = []

    for key, value in metadata.items():
        interesting = any(word in key.lower() for word in METADATA_KEYS_OF_INTEREST)
        if explain and value:
            print(f"  metadata {key}: {value[:100]}")
        if not value:
            continue
        # A metadata value is itself text: run the same shapes over it.
        for origin, label, detail in scan_text(value, f"metadata:{key}"):
            findings.append((origin, label, detail))
        if interesting and not PLACEHOLDER_VALUE.match(value):
            findings.append((f"metadata:{key}", "identifying metadata field", value[:110]))

    text = ocr_text(path)
    if explain:
        print(f"  OCR: {len(text)} characters extracted")
        for line in text.split("\n"):
            if line.strip():
                print(f"    | {line.strip()[:110]}")
    findings.extend(scan_text(text, "OCR"))

    if not _which("tesseract"):
        print("  WARNING: tesseract is not installed, so the pixels were NOT read."
              " Metadata only. Treat this result as partial.", file=sys.stderr)

    # Resolution matters more than anything else about OCR, and it fails in the
    # direction that matters: at small font sizes the engine mangles
    # `/home/<user>/` into `homel<user>` and `sk-or-v1-` into `skor 1`, so the
    # patterns miss and the audit reports CLEAN on an image that plainly leaks.
    # A clean verdict on a low-resolution image is worth much less than the same
    # verdict on a full-size one, and saying so is the difference between a
    # filter and a false assurance.
    try:
        from PIL import Image

        with Image.open(path) as im:
            width, height = im.size
        if max(width, height) < 1000:
            print(f"  WARNING: {width}x{height} is small for OCR. Credential shapes are"
                  " frequently misread at this size and will not match, so a CLEAN"
                  " verdict here is weak evidence. Audit the full-resolution capture"
                  " instead of a downscaled copy.", file=sys.stderr)
    except Exception:  # noqa: BLE001
        pass

    return findings


def self_test() -> int:
    samples = {
        "aws key": "k = \"AKIA" + "IOSFODNN7EXAMPLE\"",
        "private key": "-----BEGIN OPENSSH " + "PRIVATE KEY-----",
        # Assembled from fragments: the detector's own sample must contain a
        # realistic account name (that is what it hunts), but a literal here
        # would make the scanner flag its own source. The two halves concatenate
        # into a value no allowlist covers, so the detector is genuinely proved.
        "home path": "cd /home/" + "acct" + "name/Work",
        "email": "mail me at someone@example.com",
        "telegram token": "8155344002:" + "A" * 32,
        "git remote": "https://github.com/owner/private-repo",
        "shell prompt": "realuser@realhost:$ ls",
    }
    failures = 0
    for label, sample in samples.items():
        if scan_text(sample, "self-test"):
            print(f"  detects {label}")
        else:
            failures += 1
            print(f"  FAILS TO DETECT {label}")
    total = len(samples)
    print(f"\nself-test: {total - failures}/{total} detectors fired")
    return 1 if failures else 0


def main(argv: list[str]) -> int:
    args = argv[1:]
    if "--self-test" in args:
        return self_test()
    explain = "--explain" in args
    paths = [Path(a) for a in args if not a.startswith("--")]
    if not paths:
        print(__doc__, file=sys.stderr)
        return 2
    missing = [p for p in paths if not p.is_file()]
    if missing:
        print(f"check-image-safety: not a file: {missing[0]}", file=sys.stderr)
        return 2

    all_findings: list[tuple[str, str, str]] = []
    for path in paths:
        try:
            all_findings.extend(audit_image(path, explain))
        except ValueError as exc:
            print(f"check-image-safety: {exc}", file=sys.stderr)
            return 2

    if all_findings:
        print()
        for origin, label, detail in all_findings:
            print(f"FINDING   {label}  [{origin}]")
            print(f"          {detail}")
        print(f"\n{len(all_findings)} finding(s). DO NOT PUBLISH this image until each is"
              " resolved: crop or redact the region, or capture again with the content"
              " removed.", file=sys.stderr)
        return 1

    print("\nCLEAN: no credential, identity or context finding in metadata or pixels")
    print("Note: OCR is imperfect; a reading of the image by eye is still required"
          " for anything you did not generate yourself.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
