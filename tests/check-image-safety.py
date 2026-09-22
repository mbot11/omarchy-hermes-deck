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

# These three follow Pillow's own documented values rather than numbers invented
# here. PngImagePlugin.MAX_TEXT_CHUNK is 1 MiB and MAX_TEXT_MEMORY is 64 MiB, both
# commented in Pillow as guards against decompression bombs "where compressed
# chunks can expand 1000x" — the exact threat. An earlier version used 4 MiB per
# chunk (4x looser than the reference) and 8 MiB total.
MAX_INFLATED_BYTES = 1 * 1024 * 1024
MAX_TOTAL_INFLATED_BYTES = 64 * 1024 * 1024

# Pillow refuses to decode an image over Image.MAX_IMAGE_PIXELS (89,478,485) and
# raises DecompressionBombError. That protection saved this gate from an 873 KB
# PNG that decodes to a 900-megapixel raster — but it was INHERITED from a library
# default and never stated, so any change to Pillow or a caller setting
# MAX_IMAGE_PIXELS = None (which Pillow's own docs warn against) would have
# exposed it. Verified: with it disabled the audit took 880 MB of RSS from that
# same file. Pin it explicitly, and treat the refusal as a finding.
MAX_IMAGE_PIXELS = 89_478_485

# Emitted when a text chunk cannot be fully inflated within the budget. It is a
# SENTINEL, not content: an earlier version returned the literal string
# "<truncated at the size cap>", which matches no detector, so a file whose
# credential chunk sat after the budget was spent produced a CLEAN verdict. The
# audit turns this marker into a finding instead. "Not fully scanned" must never
# read the same as "clean".
TRUNCATED_TEXT = "\x00<truncated at the size cap>\x00"


def _inflate(blob: bytes, budget: int = MAX_INFLATED_BYTES) -> str:
    """Decompress a zlib stream from a PNG text chunk, with a hard size cap.

    Stdlib rather than Pillow, so a compressed chunk is readable even where
    Pillow is not installed — an earlier version stored the literal `<zTXt>` and
    then exempted that field, so a credential in a compressed chunk produced a
    CLEAN verdict.

    The cap matters as much as the decompression. `zlib.decompress` on an
    untrusted stream is a decompression bomb: 200 KB of highly compressible
    input inflates to 200 MB, and this runs on the publish gate, whose whole
    premise is that it terminates and is not a memory amplifier. Decompress
    incrementally and stop at `budget` bytes. The budget is shared by every chunk
    in one image and is consumed IN FILE ORDER, so it is a bound on work, not a
    guarantee that later chunks are read: once it is exhausted, subsequent chunks
    are reported as TRUNCATED_TEXT and the audit raises a finding. Earlier
    comments claimed the total was simply "bounded", which was wrong — an
    exhausted budget silently blinded the tail of the file and the placeholder
    matched no detector, so the verdict was CLEAN.
    """
    import zlib

    # An exhausted budget must produce NOTHING, and `decompress(data, 0)` does not
    # do that — zlib documents max_length=0 as "unlimited", so a zero budget
    # returned a full megabyte. That is how 40 chunks retained 42 MB against an
    # 8 MB budget. Return early instead of relying on the library's sentinel.
    if budget <= 0:
        return TRUNCATED_TEXT

    try:
        engine = zlib.decompressobj()
        out = bytearray()
        for offset in range(0, len(blob), 65536):
            out += engine.decompress(blob[offset : offset + 65536],
                                     budget - len(out))
            if len(out) >= budget:
                # Truncated rather than refused: the credential is almost always
                # early in the text, and a partial read still gets scanned. The
                # sentinel makes the partial read VISIBLE to the audit.
                out += TRUNCATED_TEXT.encode()
                break
        return out.decode("utf-8", "replace")
    except (zlib.error, ValueError, TypeError):
        # Undecodable is not the same as empty. Say so, so this cannot pass for
        # a chunk that merely had no text in it.
        return "<undecodable compressed text chunk>"


def png_text_chunks(data: bytes) -> dict[str, str]:
    """Pull tEXt/zTXt/iTXt chunks out of a PNG without a decoder.

    These survive cropping and resizing and routinely carry the tool that made
    the image plus the author and machine name.
    """
    out: dict[str, str] = {}
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return out
    offset = 8
    budget = MAX_TOTAL_INFLATED_BYTES
    while offset + 8 <= len(data):
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        kind = data[offset + 4 : offset + 8]
        body = data[offset + 8 : offset + 8 + length]
        if kind == b"tEXt":
            key, _, value = body.partition(b"\x00")
            out[key.decode("latin-1", "replace")] = value.decode("latin-1", "replace")
        elif kind == b"zTXt":
            # zTXt = keyword \x00 compression_method \x00 deflate(data)
            key, _, rest = body.partition(b"\x00")
            _, _, compressed = rest.partition(b"\x00")
            key_name = key.decode("latin-1", "replace")
            text = _inflate(compressed, budget)
            # Charge by the text this chunk actually produced. Reading it back out
            # of `out` measured the wrong thing: a repeated keyword overwrote the
            # previous entry, so the subtraction saw only the newest value and the
            # budget never fell — 40 chunks retained 42 MB against an 8 MB budget.
            budget = max(0, budget - len(text))
            out[key_name] = text
        elif kind == b"iTXt":
            # iTXt. A short or ambiguous chunk is reported as undecodable rather
            # than guessed at: trusting a split that "looks right" is how a
            # compressed payload once got decoded as plain text, so the credential
            # stopped matching and the audit said CLEAN.
            # Structured EXACTLY as PIL.PngImagePlugin.PngImageFile.chunk_iTXt
            # reads it, because that is the reference implementation and two
            # earlier versions of this branch disagreed with it:
            #   k, r = r.split(b"\0", 1);  cf, cm, r = r[0], r[1], r[2:]
            #   lang, tk, v = r.split(b"\0", 2)
            # Note `r[2:]` drops the flag AND the method, so the language
            # field's terminator is the FIRST NUL seen after that — skipping an
            # extra byte here silently eats into the deflate stream, which made
            # every real chunk report as undecodable.
            key, sep, rest = body.partition(b"\x00")
            key_text = key.decode("latin-1", "replace")
            if not sep or len(rest) < 2:
                out[key_text] = "<undecodable compressed text chunk>"
            else:
                comp_flag, comp_method, remainder = rest[0], rest[1], rest[2:]
                parts = remainder.split(b"\x00", 2)
                if len(parts) < 3:
                    out[key_text] = "<undecodable compressed text chunk>"
                else:
                    text = parts[2]
                    if comp_flag != 0:
                        if comp_method == 0:
                            decoded = _inflate(text, budget)
                            budget = max(0, budget - len(decoded))
                            out[key_text] = decoded
                        else:
                            # Only deflate is defined for PNG text chunks.
                            out[key_text] = "<undecodable compressed text chunk>"
                    else:
                        out[key_text] = text.decode("utf-8", "replace")
        if kind == b"IEND":
            break
        offset += 12 + length
    return out


def _pin_pillow_limits() -> None:
    """Refuse to run with Pillow's decompression-bomb guards disabled.

    `Image.MAX_IMAGE_PIXELS = None` is a documented footgun (Pillow's own security
    guidance says not to set it). If the environment has disabled it, this gate
    would decode a 900-megapixel raster from an 873 KB file — measured at 880 MB
    of RSS — so refuse rather than inherit a dangerous default.
    """
    try:
        from PIL import Image

        if Image.MAX_IMAGE_PIXELS is None:
            raise SystemExit(
                "check-image-safety: PIL.Image.MAX_IMAGE_PIXELS is None, which"
                " disables Pillow's decompression-bomb guard. Refusing to run."
            )
    except ImportError:
        # No Pillow: main() refuses on its own with a clear message. Nothing to
        # check here, and raising would pre-empt that better error.
        pass
    except Exception as exc:  # noqa: BLE001
        # A Pillow that EXISTS but cannot load — a missing libjpeg.so, a broken
        # install — raises OSError, not ImportError. Catching only ImportError let
        # that escape main() as a traceback: the gate crashed before it could
        # refuse, so it emitted no verdict at all. A crash on the publish path is
        # the one outcome worse than a refusal, because "no output" reads as
        # "nothing to report". Refuse explicitly instead.
        # Exit 2, not 1: 1 means "findings, do not publish", 2 means "could not
        # audit". `raise SystemExit("message")` exits 1, which would be read as a
        # finding rather than as the inability to audit. Print and exit 2.
        print(
            f"check-image-safety: Pillow is installed but unusable"
            f" ({type(exc).__name__}: {exc}). Refusing to report a verdict."
            "\n  Reinstall it: sudo pacman -S python-pillow"
            "  (or: apt-get install python3-pil)",
            file=sys.stderr,
        )
        raise SystemExit(2)


def _has_pillow() -> bool:
    """True when Pillow can actually be imported AND used.

    Catches Exception rather than ImportError: a Pillow whose shared libraries
    will not load raises OSError, and that was escaping the audit as a traceback
    instead of being reported as a missing tool.
    """
    try:
        import PIL  # noqa: F401
        from PIL import Image  # noqa: F401

        return True
    except Exception:  # noqa: BLE001
        return False


# Pillow is REQUIRED. Auditing an image without it produced FIVE separate
# fail-open paths, each verified: EXIF in a JPEG/WebP was invisible (keyed on the
# suffix, so a JPEG named .png slipped through), a PNG eXIf chunk was invisible
# (only tEXt/zTXt/iTXt are parsed), a GIF was invisible and deliberately omitted
# from the suffix set, a compressed AVIF/HEIC produced no metadata AND no OCR
# with no warning, and a Pillow that failed to load its shared libraries raised
# out of the audit instead of being handled. Rather than patch five holes in an
# optional dependency, the audit refuses to give a verdict without it: "cannot
# inspect" must never be reported as "clean".
REQUIRED_TOOL = "Pillow (python3-pil)"


def _decode_refusal(path: Path) -> str:
    """Why Pillow will not decode this file, or "" when it will.

    A pixel bomb must be REPORTED, not silently skipped: with the pixels capped
    the audit called `_looks_like_an_image` False, logged nothing, and returned a
    clean verdict on an image it never examined — the same fail-open shape as
    every other finding in this file's history.
    """
    try:
        from PIL import Image
    except Exception:  # noqa: BLE001
        return "Pillow is not importable"
    try:
        with Image.open(path) as probe:
            # Same pixel bound as _looks_like_an_image: this function exists to
            # decide whether the file is decodable, and `load()` answers that by
            # allocating the whole raster (~650 MB for a 165-megapixel file). The
            # declared size is checked first, so load() only runs on files within
            # the pixel budget.
            #
            # NOT verify(): `verify()` checks chunk CRCs and passes on a PNG whose
            # IDAT holds non-deflate data — i.e. a file whose pixels cannot be
            # decoded at all. That is the exact fail-open this function exists to
            # close, so it must use the real decoder. Verified: a valid-CRC IDAT
            # of garbage gives verify() OK and load() OSError.
            width, height = probe.size
            if width * height > MAX_IMAGE_PIXELS:
                return (
                    f"image is too large to decode safely"
                    f" ({width}x{height} = {width * height} pixels exceeds the"
                    f" {MAX_IMAGE_PIXELS}-pixel limit)"
                )
            probe.load()
        return ""
    except Image.DecompressionBombError as exc:  # type: ignore[attr-defined]
        return f"image is too large to decode safely ({exc})"
    except Exception:  # noqa: BLE001
        return ""


def _looks_like_an_image(path: Path) -> bool:
    """True when Pillow can actually decode the file.

    Pillow is authoritative and required — hand-rolled magic-byte matching was a
    fail-open: the earlier version accepted a 2-byte prefix, so a text file
    starting with "BM" (`BMW review notes`), "RIFF", "GIF89a" or "II*\0" was
    treated as an image and its credential reported as CLEAN. A signature match
    is not evidence of a decodable image, and a truncated PNG keeps its signature
    while being unreadable. So: ask the real decoder, and require it to load the
    pixel data rather than merely parse a header.

    What this does NOT promise: `load()` decodes PIXELS only. It does not validate
    the text chunks this module exists to read, and a file can pass here while its
    metadata is only partially scanned — that case is reported separately, as a
    "metadata text not fully scanned" finding, not silently accepted.
    """
    try:
        from PIL import Image
    except Exception:  # noqa: BLE001
        return False
    try:
        with Image.open(path) as probe:
            # Bound the decode BEFORE touching pixels. `load()` allocates the full
            # raster, so asking "is this decodable?" on a 165-megapixel file cost
            # ~700 MB of RSS — the metadata this module exists to read needs none
            # of it. Pillow only raises DecompressionBombWarning below twice its
            # own limit, so a merely-huge image is accepted and then decoded at
            # full size. Refuse by declared dimensions instead.
            width, height = probe.size
            if width * height > MAX_IMAGE_PIXELS:
                return False
            probe.load()  # a header alone is not enough; truncated files fail here
        return True
    except Exception:  # noqa: BLE001
        return False


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
            # `im.info` and EXIF are available without touching pixels, but Pillow
            # still allocates the raster for some formats when the image is
            # opened-and-used this way — a 165-megapixel file cost ~650 MB here.
            # Metadata needs the header, not the pixels: cap by declared size and
            # report an unreadable-header finding rather than decoding a bomb.
            width, height = im.size
            if width * height > MAX_IMAGE_PIXELS:
                out["info:oversize"] = (
                    f"{width}x{height} = {width * height} pixels exceeds the"
                    f" {MAX_IMAGE_PIXELS}-pixel limit; metadata not read"
                )
                return out
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
        # `text=True` decodes BOTH streams as UTF-8, and tesseract echoes the
        # filename into stderr — so an image whose name is raw non-UTF8 bytes
        # (a truncated file named by its own signature) raised UnicodeDecodeError
        # out of the whole run. Bytes with an explicit decode cannot do that.
        out = subprocess.run(
            ["tesseract", str(path), "stdout", "--psm", "6"],
            capture_output=True,
            timeout=OCR_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        print(f"  WARNING: tesseract exceeded {OCR_TIMEOUT_SECONDS}s on {path};"
              " pixels were NOT fully read. Treat this result as partial.",
              file=sys.stderr)
        return ""
    if out.returncode != 0:
        # A format tesseract cannot read at all (AVIF, HEIC, PCX, TGA) returns
        # non-zero with empty output. Silently returning "" made an OCR-blind
        # image indistinguishable from an image with no text in it.
        print(f"  WARNING: tesseract could not read {path}"
              f" (exit {out.returncode}); the pixels were NOT scanned."
              " Treat this result as partial.", file=sys.stderr)
        return ""
    return out.stdout.decode("utf-8", "replace")


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
        # A compressed chunk we could not decode is a finding in its own right:
        # we cannot vouch for text we could not read, and treating it as absent
        # is exactly how the gate failed open before.
        if value.startswith("<undecodable"):
            findings.append((f"metadata:{key}", "unreadable compressed metadata", value[:110]))
        elif TRUNCATED_TEXT in value:
            # A partially-inflated chunk is reported, never passed. The sentinel
            # used to be an ordinary-looking string that matched no detector, so a
            # credential placed after the budget was spent produced CLEAN.
            findings.append((
                f"metadata:{key}", "metadata text not fully scanned",
                f"{key}: exceeded the inflation budget, so this image's metadata was"
                " only PARTIALLY scanned",
            ))
        elif interesting and not PLACEHOLDER_VALUE.match(value):
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

    # FAIL CLOSED on an artifact that could not be decoded. The first half of this
    # comment described the days when Pillow was optional ("without it the audit
    # saw no metadata at all") — no longer reachable, since `main()` refuses with
    # exit 2 before audit_image runs when Pillow is absent or broken. What remains
    # true, and is the reason this check exists, is the second half:
    # An image we could not decode AT ALL must not be vouched for. A non-image
    # with an image extension (a mislabelled file, a truncated download, a
    # plaintext file someone renamed) yields no metadata and no OCR text, so the
    # audit saw nothing and said CLEAN — "we detected nothing" and "there was
    # nothing to detect" were indistinguishable. Require evidence that this is a
    # real image before accepting a clean verdict.
    refusal = _decode_refusal(path)
    if refusal:
        findings.append((
            f"metadata:{path.suffix.lower() or 'no-extension'}",
            "not safe to decode",
            f"{path.name}: {refusal} — this file was NOT audited; a small file"
            " that decodes to an enormous raster is a decompression bomb",
        ))
    elif not _looks_like_an_image(path):
        findings.append((
            f"metadata:{path.suffix.lower() or 'no-extension'}",
            "not a decodable image",
            f"{path.name}: no PNG signature or decodable header, so this file was"
            " NOT audited as an image — check what it actually is",
        ))



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


def _safe(text) -> str:
    """Text that can always be written to a stream.

    A filename of raw non-UTF8 bytes arrives surrogate-escaped (os.fsdecode), and
    printing it raises UnicodeEncodeError — so the gate crashed while REPORTING an
    unreadable file and emitted no verdict at all. Every path reaching a stream
    goes through here.
    """
    return str(text).encode("utf-8", "backslashreplace").decode("utf-8", "replace")


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

    # Refuse rather than guess. Without Pillow this audit cannot see EXIF, cannot
    # read a GIF/AVIF/HEIC, and cannot verify a file is an image at all — so a
    # CLEAN verdict would be meaningless in the direction that leaks.
    _pin_pillow_limits()
    if not _has_pillow():
        print(f"check-image-safety: {REQUIRED_TOOL} is required to audit images"
              " (and .jpeg/.webp/.gif/.avif EXIF cannot be read without it)."
              " Refusing to report a verdict.\n"
              "  Install it with: sudo pacman -S python-pillow"
              "  (or: apt-get install python3-pil)", file=sys.stderr)
        return 2

    all_findings: list[tuple[str, str, str]] = []
    for path in paths:
        try:
            all_findings.extend(audit_image(path, explain))
        except Exception as exc:  # noqa: BLE001
            # One unreadable path must not discard the findings already collected
            # for every other image, and must not skip the ones after it.
            #
            # ValueError used to `return 2` immediately, so a single oversized file
            # ANYWHERE in the argument list threw away the findings of every image
            # already audited — a caller publishing on "no FINDING lines" would
            # publish a leaking image. The reverse order discarded silently too.
            # Report it as a finding, keep the verdict fail-closed, and continue.
            print(_safe(f"check-image-safety: could not audit {path}:"
                        f" {type(exc).__name__}: {exc}"), file=sys.stderr)
            all_findings.append((
                f"unreadable:{path.name}",
                "could not be audited",
                f"{path.name}: {type(exc).__name__} — this file was NOT checked,"
                " treat it as unaudited rather than clean",
            ))

    if all_findings:
        print()
        for origin, label, detail in all_findings:
            # Every emitted path goes through _safe: a surrogate-escaped filename
            # in the finding detail raised UnicodeEncodeError HERE, on the verdict
            # line itself, so the gate printed no result at all.
            print(_safe(f"FINDING   {label}  [{origin}]"))
            print(_safe(f"          {detail}"))
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
