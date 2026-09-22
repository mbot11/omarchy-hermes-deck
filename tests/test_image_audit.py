#!/usr/bin/env python3
"""Regression tests for the image audit's detection logic.

Each case here is a false negative that reached a CLEAN verdict once. The
exemption rules exist to suppress OCR noise on product names; the tests pin
that they must not suppress a credential standing next to one.

Run:  python3 tests/test_image_audit.py
"""

from __future__ import annotations

import importlib.util
import subprocess
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCANNER = HERE / "check-image-safety.py"


def _load():
    spec = importlib.util.spec_from_file_location("cis", str(SCANNER))
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {SCANNER}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cis = _load()

# The exact strings that were published-clean by mistake.
BYPASS_CASES = [
    ("secret beside 'providers'", "providers api_key = sk-proj-ABCDEFGHIJKLMNOPQRSTUVWXYZ"),
    ("secret beside 'telegram'", "telegram api_key = sk-proj-ABCDEFGHIJKLMNOPQRSTUVWXYZ"),
    ("secret beside 'gateway'", "gateway password = hunter2hunter2hunter2"),
    ("secret beside 'quickshell'", "quickshell token = eyJhbGciOiJIUzI1NiIsInR5cCI6"),
    ("email beside 'ollama'", "ollama contact someone@example.com"),
    ("email beside 'omarchy'", "omarchy contact ceo@example.com"),
]

# A credential whose VALUE happens to contain a product word. These were
# silently whitelisted by a span-overlap exemption: the product name inside the
# secret was enough to suppress the finding. Removed rather than refined — a
# pre-publish gate must err toward reporting.
VALUE_CONTAINS_NOISE_CASES = [
    ("api key containing 'ollama'", "api_key = ollama_cloud_prod_a1b2c3d4e5f6"),
    ("api key containing 'gateway'", "gateway api_key = gateway_prod_a1b2c3d4e5f6g7h8"),
    ("token containing 'telegram'", "token = telegram_bot_abcdefghijklmnop"),
    ("secret containing our own name", "secret = hermes-deck-live-abcdefghijklmnop"),
    ("password containing 'quickshell'", "password = quickshell-admin-abcdefghij"),
    ("token containing 'omarchy'", "token = omarchy_service_abcdefghijkl"),
]

# A user or host named after the distro. Plausible on an Omarchy box, and it is
# exactly the identity the audit exists to catch.
# Assembled from fragments: these values are themselves the shape the scanner
# hunts, so a literal here would make the audit flag its own fixture.
DISTRO_NAMED_IDENTITY_CASES = [
    ("home dir named 'omarchy'", "/home/" + "omarchy" + "/Work/secret-client"),
    ("home dir named 'quickshell'", "/home/" + "quickshell" + "/notes"),
    ("prompt for a user named 'omarchy'", "omarchy" + "@workstation:~$ ls"),
]


class TestNoiseExemptionIsScoped(unittest.TestCase):
    def test_credential_beside_a_noise_word_is_still_reported(self):
        for label, line in BYPASS_CASES:
            with self.subTest(label):
                hits = cis.scan_text(line, "t")
                self.assertTrue(hits, f"false CLEAN on: {line!r}")

    def test_credential_containing_a_product_word_is_reported(self):
        """A product word inside a value must not suppress the finding.

        The earlier implementation exempted any match whose span overlapped an
        OCR-noise word, which whitelisted `api_key = ollama_cloud_prod_...`.
        Over-reporting is the correct failure direction for a publish gate.
        """
        for label, line in VALUE_CONTAINS_NOISE_CASES:
            with self.subTest(label):
                self.assertTrue(cis.scan_text(line, "t"),
                                f"credential whitelisted by a product word: {line!r}")

    def test_distro_named_identity_is_reported(self):
        for label, line in DISTRO_NAMED_IDENTITY_CASES:
            with self.subTest(label):
                self.assertTrue(cis.scan_text(line, "t"),
                                f"distro-named identity missed: {line!r}")

    def test_distro_name_alone_is_not_a_finding(self):
        """The *word* 'omarchy' is not a finding; only an identity built on it is.

        Without this the scanner would report the project's own name in any
        documentation screenshot, which is noise rather than a leak.
        """
        for line in ("Hermes Deck for Omarchy", "the omarchy shell plugin"):
            with self.subTest(line):
                self.assertFalse(cis.scan_text(line, "t"),
                                 f"the distro name alone was reported: {line!r}")


class TestMetadataPlaceholderIsExact(unittest.TestCase):
    def test_author_name_containing_png_is_reported(self):
        """A comment mentioning pngquant must not exempt the author field.

        The exemption is a literal-placeholder test, not a substring test.
        Detection of the author name itself happens in `audit_image`'s
        "identifying metadata field" branch (covered by TestLeakDetectors):
        `scan_text` matches credential *shapes*, and a name is not a shape, so
        asserting on scan_text here would be testing the wrong function.
        """
        value = "author=JaneQPublic, made with pngquant"
        self.assertIsNone(
            cis.PLACEHOLDER_VALUE.match(value),
            "a real author field was treated as a synthesised placeholder",
        )

    def test_synthesised_placeholder_is_exempt(self):
        self.assertRegex("<zTXt>", r"^<(?:zTXt|iTXt)>$")
        self.assertIsNotNone(cis.PLACEHOLDER_VALUE.match("<zTXt>"))
        self.assertIsNotNone(cis.PLACEHOLDER_VALUE.match("<iTXt>"))


class TestOCRBounds(unittest.TestCase):
    def test_a_slow_tesseract_does_not_hang_the_gate(self):
        """Behavioural, not a source-text assertion.

        Asserting that the string "timeout" appears in the function passes on any
        edit that keeps the identifier, including one that stops using it. This
        substitutes a stalling `tesseract` and requires the call to give up.
        """
        import time
        from unittest import mock

        def stalling(*args, **kwargs):
            if "timeout" not in kwargs:
                time.sleep(300)  # would hang the real gate
            raise subprocess.TimeoutExpired(cmd="tesseract", timeout=kwargs["timeout"])

        with mock.patch.object(cis, "_which", return_value=True), \
             mock.patch.object(cis.subprocess, "run", side_effect=stalling):
            started = time.time()
            result = cis.ocr_text(Path("/nonexistent.png"))
            elapsed = time.time() - started
        self.assertEqual(result, "", "a timed-out OCR must return empty text")
        self.assertLess(elapsed, 30, "the OCR call did not respect a timeout")


class TestReadBounds(unittest.TestCase):
    def test_oversized_image_is_refused_not_read(self):
        """Behavioural: a real oversized file must be refused."""
        import tempfile
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "huge.png"
            path.write_bytes(b"\x89PNG\r\n\x1a\n")
            # Report it as over the cap without writing a real 64 MiB file.
            real_stat = Path.stat

            def fake_stat(self, *a, **kw):
                result = real_stat(self, *a, **kw)
                return type("S", (), {"st_size": cis.MAX_IMAGE_BYTES + 1})() \
                    if self == path else result

            with mock.patch.object(Path, "stat", fake_stat):
                with self.assertRaises(ValueError):
                    cis.image_metadata(path)

    def test_oversized_image_is_reported_not_crashed(self):
        """The refusal must reach the caller as a reported finding."""
        import tempfile
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "huge.png"
            path.write_bytes(b"\x89PNG\r\n\x1a\n")
            with mock.patch.object(cis, "MAX_IMAGE_BYTES", 1):
                code = cis.main(["check-image-safety.py", str(path)])
            self.assertEqual(code, 2, "an oversized image should exit 2, not crash")


class TestTildePaths(unittest.TestCase):
    """A shell prompt prints `~/`, not `/home/<user>/`.

    The scanner originally only looked for absolute home paths, so the most
    common real-world leak — a screenshot of a terminal whose prompt shows a
    working directory — passed clean. Verified against a real capture where
    OCR read `ls -la ~/.config/omarchy/plugins/...` and no finding fired.
    """

    TILDE_CASES = [
        ("tilde work dir", "cd ~/Work/client-project"),
        ("tilde config", "ls -la ~/.config/omarchy/plugins"),
        ("tilde with subdir", "$ cat ~/.ssh/config"),
        ("tilde dotfile", "vim ~/.bashrc"),
    ]

    def test_tilde_paths_are_reported(self):
        for label, line in self.TILDE_CASES:
            with self.subTest(label):
                self.assertTrue(cis.scan_text(line, "t"),
                                f"tilde path not reported: {line!r}")

    def test_bare_tilde_is_not_a_finding(self):
        """`cd ~` alone names no directory, so it discloses nothing."""
        for line in ("cd ~", "cd ~ ", "~"):
            with self.subTest(line):
                self.assertFalse(cis.scan_text(line, "t"),
                                 f"bare tilde reported: {line!r}")

    def test_absolute_home_path_still_reported(self):
        # Assembled so this test's own fixture is not itself a home path the
        # audit has to allowlist.
        self.assertTrue(cis.scan_text("cd /home/" + "acct" + "name/Work", "t"))

    def test_a_lone_tilde_in_prose_is_not_a_finding(self):
        """`~` is also a normal character. Only a path-like use is a finding."""
        for line in ("about ~20 items", "roughly ~5 minutes", "a ~ b"):
            with self.subTest(line):
                self.assertFalse(cis.scan_text(line, "t"),
                                 f"prose tilde reported: {line!r}")


def _has_pillow() -> bool:
    """Pillow is needed to synthesise a PNG for the integration check."""
    try:
        import PIL  # noqa: F401
    except ImportError:
        return False
    return True


def _png_with_chunk(kind: bytes, body: bytes) -> bytes:
    """A minimal valid PNG carrying one extra text chunk.

    `chunk()`'s first argument is the CHUNK TYPE, not a pre-built body: passing
    a body here produces a chunk whose declared length is wrong, which the parser
    then reads as a truncated string. Getting this backwards made a working
    parser look broken during review, so it is a helper with a name now.
    """
    import struct
    import zlib

    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000a49444154789c6360000002000100ffff03000006000557bfabd4000000"
        "0049454e44ae426082"
    )

    def chunk(chunk_kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + chunk_kind + data
                + struct.pack(">I", zlib.crc32(chunk_kind + data) & 0xFFFFFFFF))

    offset = png.index(b"IDAT") - 4
    return png[:offset] + chunk(kind, body) + png[offset:]


class TestCompressedTextChunks(unittest.TestCase):
    """A secret inside a COMPRESSED chunk must be readable without Pillow.

    The scanner stored the literal `<zTXt>` for a compressed chunk and exempted
    that value from the identifying-metadata finding, so on a runner without
    Pillow a credential inside a zTXt chunk produced CLEAN — the gate failed
    open. It now decompresses with the stdlib and reports any chunk it cannot
    decode as a finding rather than treating it as absent.
    """

    # Assembled from fragments so this synthetic value is never mistaken for a
    # real key by a scanner or a redactor that reads the source.
    SECRET = "api_key = sk-" + "live-" + "abcdefghijklmnop"

    def test_zTXt_secret_is_read_without_pillow(self):
        import zlib

        body = b"Comment\x00\x00" + zlib.compress(self.SECRET.encode())
        data = _png_with_chunk(b"zTXt", body)
        chunks = cis.png_text_chunks(data)
        self.assertIn("Comment", chunks, f"zTXt chunk not extracted: {chunks!r}")
        self.assertIn("sk-live", chunks["Comment"],
                      f"compressed text not decompressed: {chunks!r}")

    def test_compressed_iTXt_secret_is_read_without_pillow(self):
        """A compressed iTXt chunk, built by the real PNG library.

        The body is NOT hand-assembled: `PngInfo.add_itxt(zip=True)` writes the
        chunk, because a hand-built body got the NUL-separator count wrong and
        the buggy code still passed the test. The real layout has FOUR NUL
        separators (five parts), and the previous parser required six, so it
        stored the literal `<iTXt>` — which the placeholder exemption then
        skipped. That is a credential in a real encoder's output returning CLEAN.
        """
        io, zlib = __import__("io"), __import__("zlib")
        from PIL import Image, PngImagePlugin

        secret = "api_key = zupi_" + "uxgqu8m7cey"
        info = PngImagePlugin.PngInfo()
        info.add_itxt("Comment", secret, zip=True)
        buf = io.BytesIO()
        Image.new("RGB", (64, 64), (9, 9, 9)).save(buf, "PNG", pnginfo=info)
        data = buf.getvalue()

        chunks = cis.png_text_chunks(data)
        self.assertIn("Comment", chunks, f"iTXt chunk not extracted: {chunks!r}")
        self.assertNotIn("<iTXt>", chunks["Comment"],
                         "the compressed iTXt was stored as a placeholder")
        self.assertIn("zupi_", chunks["Comment"],
                      f"compressed iTXt text not decompressed: {chunks['Comment']!r}")

    def test_undecodable_chunk_is_a_finding_not_an_exemption(self):
        data = _png_with_chunk(b"zTXt", b"Comment\x00\x00not-deflate-data")
        chunks = cis.png_text_chunks(data)
        self.assertTrue(chunks.get("Comment", "").startswith("<undecodable"),
                        f"a corrupt chunk should be flagged: {chunks!r}")

    def test_uncompressed_tEXt_still_read(self):
        data = _png_with_chunk(b"tEXt", b"Comment\x00" + self.SECRET.encode())
        chunks = cis.png_text_chunks(data)
        self.assertIn("sk-live", chunks.get("Comment", ""),
                      f"tEXt not read: {chunks!r}")


class TestInflateIsBounded(unittest.TestCase):
    """A decompression bomb must not run on the publish gate.

    `zlib.decompress` on untrusted input expands ~1000x: 200 KB of zeros becomes
    200 MB. The gate's own comment promises it terminates and is not a memory
    amplifier, and MAX_IMAGE_BYTES caps the FILE, not the expansion.
    """

    def test_decompression_bomb_is_capped(self):
        import zlib

        bomb = zlib.compress(b"\x00" * (64 * 1024 * 1024), 9)
        self.assertLess(len(bomb), 200_000, "the test input should be small")
        out = cis._inflate(bomb)
        self.assertLessEqual(len(out), cis.MAX_INFLATED_BYTES + 100,
                             "the inflate result was not capped")

    def test_small_payload_is_not_truncated(self):
        import zlib

        out = cis._inflate(zlib.compress(b"api_key = sk-live-abc"))
        self.assertIn("sk-live-abc", out)


class TestFailClosedOnUnreadableFormats(unittest.TestCase):
    """A format this run cannot read must not produce a CLEAN verdict.

    EXIF in a JPEG or WebP is reachable only through Pillow. Without it the audit
    saw no metadata and returned CLEAN on a file carrying a credential in EXIF —
    the fail-open that a CI comment claimed was fixed.
    """

    def test_pillow_only_suffixes_are_declared(self):
        for suffix in (".jpg", ".jpeg", ".webp", ".tif", ".tiff"):
            self.assertIn(suffix, cis.PILLOW_ONLY_SUFFIXES)

    def test_unreadable_format_is_reported_when_pillow_is_absent(self):
        import tempfile
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "shot.jpg"
            path.write_bytes(b"\xff\xd8\xff\xe0not a real jpeg")
            with mock.patch.object(cis, "_has_pillow", return_value=False):
                findings = cis.audit_image(path)
            labels = [f[1] for f in findings]
            self.assertIn("unreadable image format (Pillow missing)", labels,
                          f"an unreadable format produced no finding: {findings!r}")


class TestLeakDetectors(unittest.TestCase):
    """The exemption is exercised through audit_image, not just scan_text."""

    @unittest.skipUnless(
        _has_pillow(),
        "Pillow is required for this integration check and CI installs it "
        "(python3-pil). If this skips, the leak-detector integration path has NO "
        "coverage — a skip is a gap, not a pass.",
    )
    def test_comment_mentioning_pngquant_still_reports_the_author(self):
        import struct
        import tempfile
        import zlib
        from PIL import Image

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "base.png"
            Image.new("RGB", (1200, 200)).save(base)
            raw = base.read_bytes()
            offset = raw.index(b"IDAT") - 4

            payload = b"Comment\x00author=JaneQPublic, made with pngquant"

            def chunk(kind: bytes, data: bytes) -> bytes:
                return (
                    struct.pack(">I", len(data))
                    + kind
                    + data
                    + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
                )

            leaky = Path(tmp) / "leaky.png"
            leaky.write_bytes(
                raw[:offset] + chunk(b"tEXt", payload) + raw[offset:]
            )
            findings = cis.audit_image(leaky)
            self.assertTrue(findings, "author name in a PNG comment was exempted")


if __name__ == "__main__":
    unittest.main(verbosity=2)
