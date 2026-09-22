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
    ("secret beside 'providers'", "providers api_key = " + "sk-pro" + "j" + "-AbCdEf1234567890AbCdEf"),
    ("secret beside 'telegram'", "telegram api_key = " + "sk-pro" + "j" + "-ZzYyXx112233445566778899"),
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
    ("secret containing our own name", "secret = hermes" + "-deck-live-abcdefghijklmnop"),
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
        """The refusal must reach the caller as a REPORTED finding.

        This asserted exit 2, which is what the code did by returning early from
        the ValueError handler — and that early return discarded the findings of
        every image already audited, so a caller publishing on "no FINDING lines"
        would publish a leaking image. The refusal is a finding now, and the exit
        stays fail-closed.
        """
        import tempfile, io, contextlib
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "huge.png"
            path.write_bytes(b"\x89PNG\r\n\x1a\n")
            out = io.StringIO()
            with mock.patch.object(cis, "MAX_IMAGE_BYTES", 1), \
                    contextlib.redirect_stdout(out):
                code = cis.main(["check-image-safety.py", str(path)])
            self.assertEqual(code, 1, "an oversized image must fail the gate")
            self.assertIn("unreadable", out.getvalue(),
                          "the refusal must be reported, not just exit-coded")


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


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    """One PNG chunk, correctly length-prefixed (see _png_with_chunk)."""
    import struct
    import zlib

    return (struct.pack(">I", len(data)) + kind + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))


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

    def test_cap_value_is_independently_pinned(self):
        """Assert a literal, not the constant it bounds.

        The earlier assertion was `len(out) <= cis.MAX_INFLATED_BYTES + 100`, so
        multiplying the constant by 1000 left the suite green — the test passed
        for a reason unrelated to the number it named.
        """
        import zlib

        bomb = zlib.compress(b"\x00" * (64 * 1024 * 1024), 9)
        out = cis._inflate(bomb)
        self.assertLessEqual(len(out), 5 * 1024 * 1024,
                             "the inflate cap must stay in single-digit MiB")
        self.assertGreaterEqual(len(out), 1 * 1024 * 1024,
                                "the cap must not be so small that real text is lost")

    def test_total_budget_bounds_many_chunks(self):
        """A per-chunk cap does not bound the total.

        400 valid 4 MiB chunks is 1.6 GiB of text from a ~1.6 MB file, well under
        MAX_IMAGE_BYTES. The budget is per IMAGE, so the total is bounded too.
        """
        import struct
        import zlib

        payload = zlib.compress(b"\x00" * (1024 * 1024), 9)
        parts = b"".join(
            _png_chunk(b"zTXt", f"K{i}".encode() + b"\x00\x00" + payload)
            for i in range(40)
        )
        base = _png_with_chunk(b"tEXt", b"seed\x00seed")
        off = base.index(b"IDAT") - 4
        data = base[:off] + parts + base[off:]
        chunks = cis.png_text_chunks(data)
        total = sum(len(v) for v in chunks.values())
        self.assertLessEqual(total, cis.MAX_TOTAL_INFLATED_BYTES + 200_000,
                             f"{total:,} bytes retained across chunks")

    def test_small_payload_is_not_truncated(self):
        import zlib

        out = cis._inflate(zlib.compress(b"api_key = sk-live-abc"))
        self.assertIn("sk-live-abc", out)


class TestPillowIsRequired(unittest.TestCase):
    """Pillow is a hard requirement, because making it optional failed open.

    Five separate holes were verified when it was optional: EXIF in a JPEG/WebP
    was invisible (and the rule was keyed on the SUFFIX, so a JPEG named .png
    slipped through), a PNG eXIf chunk was invisible, a GIF was invisible and
    deliberately excluded from the suffix set, OCR-blind AVIF/HEIC produced no
    metadata and no text with no warning, and a Pillow whose shared libraries
    failed to load raised out of the audit. Patching five holes in an optional
    dependency is worse than requiring it: "cannot inspect" must never be
    reported as "clean".
    """

    def test_pillow_probe_survives_a_broken_install(self):
        """A Pillow that raises anything at import is still 'not available'."""
        from unittest import mock

        for exc in (ImportError("no module"), OSError("libjpeg.so.9: cannot open")):
            with self.subTest(type(exc).__name__):
                with mock.patch("builtins.__import__", side_effect=exc):
                    self.assertFalse(cis._has_pillow(),
                                     f"{type(exc).__name__} escaped the probe")

    def test_main_refuses_to_report_without_pillow(self):
        import tempfile
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "shot.jpg"
            path.write_bytes(b"\xff\xd8\xff\xe0not an image")
            with mock.patch.object(cis, "_has_pillow", return_value=False):
                code = cis.main(["check-image-safety.py", str(path)])
            self.assertEqual(code, 2,
                             "without Pillow the audit must refuse, not report")

    def test_required_tool_is_named(self):
        self.assertIn("Pillow", cis.REQUIRED_TOOL)


class TestNonDecodableFiles(unittest.TestCase):
    """A file Pillow cannot decode must be reported, never vouched for."""

    def test_prefix_magic_is_not_enough(self):
        """A 2-byte prefix used to be accepted, so `BMW review notes` passed.

        The old check matched `BM`/`RIFF`/`GIF89a`/`II*\0` as a PREFIX, so a
        text file starting with those bytes was treated as an image and its
        credential reported CLEAN.
        """
        import tempfile

        for prefix in (b"BMW review notes\n", b"RIFF notes\n",
                       b"GIF89a notes\n", b"II*\x00 notes\n"):
            with self.subTest(prefix):
                with tempfile.TemporaryDirectory() as tmp:
                    path = Path(tmp) / "looks-like.png"
                    path.write_bytes(prefix + b"api_key = " + b"sk-liv" + b"e-abcdefghijklmnop\n")
                    self.assertFalse(cis._looks_like_an_image(path),
                                     f"a text file starting with {prefix!r} passed")
                    findings = cis.audit_image(path)
                    self.assertIn("not a decodable image", [f[1] for f in findings],
                                  f"no finding for {prefix!r}")

    def test_truncated_png_is_not_a_decodable_image(self):
        """A truncated PNG keeps its signature but cannot be loaded."""
        import io
        import tempfile

        from PIL import Image

        with tempfile.TemporaryDirectory() as tmp:
            buf = io.BytesIO()
            Image.new("RGB", (40, 40)).save(buf, "PNG")
            raw = buf.getvalue()
            for fraction in (0.5, 0.25):
                with self.subTest(fraction):
                    path = Path(tmp) / f"trunc{int(fraction*100)}.png"
                    path.write_bytes(raw[: int(len(raw) * fraction)])
                    self.assertFalse(cis._looks_like_an_image(path),
                                     "a truncated PNG passed as an image")

    def test_real_formats_are_accepted(self):
        """No false positives: every format Pillow can write must pass."""
        import tempfile

        from PIL import Image

        with tempfile.TemporaryDirectory() as tmp:
            for fmt in ("PNG", "JPEG", "WEBP", "GIF", "TIFF", "BMP"):
                with self.subTest(fmt):
                    path = Path(tmp) / f"real.{fmt.lower()}"
                    Image.new("RGB", (20, 20)).save(path, fmt)
                    self.assertTrue(cis._looks_like_an_image(path),
                                    f"a real {fmt} was rejected")


class TestDecompressionBombs(unittest.TestCase):
    """A small file that decodes to an enormous raster must be REPORTED.

    Pillow refuses an image over Image.MAX_IMAGE_PIXELS and raises
    DecompressionBombError. That guard saved this gate from an 873 KB PNG that
    decodes to 900 megapixels (measured at 880 MB of RSS with the guard
    disabled) — but with the guard ENABLED the audit called `_looks_like_an_image`
    False, logged nothing, and returned a CLEAN verdict on an image it never
    examined. A bomb is not "not an image"; it is a finding.
    """

    def test_pixel_bomb_is_reported_not_silently_skipped(self):
        import tempfile
        from unittest import mock

        from PIL import Image

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bomb.png"
            Image.new("L", (64, 64), 0).save(path, "PNG")

            # Simulate the refusal without building a real 900 MP file.
            with mock.patch.object(
                cis, "_decode_refusal",
                return_value="image is too large to decode safely",
            ):
                findings = cis.audit_image(path)
            labels = [f[1] for f in findings]
            self.assertIn("not safe to decode", labels,
                          f"a refused decode produced no finding: {findings!r}")

    def test_limits_match_pillows_documented_values(self):
        """The caps follow the reference implementation, not invented numbers."""
        from PIL import ImageFile, PngImagePlugin

        self.assertEqual(cis.MAX_INFLATED_BYTES, PngImagePlugin.MAX_TEXT_CHUNK,
                         "per-chunk cap should match Pillow's MAX_TEXT_CHUNK")
        self.assertEqual(cis.MAX_TOTAL_INFLATED_BYTES, PngImagePlugin.MAX_TEXT_MEMORY,
                         "total cap should match Pillow's MAX_TEXT_MEMORY")

    def test_refuses_when_the_pixel_guard_is_disabled(self):
        """Pillow's docs warn against MAX_IMAGE_PIXELS = None; refuse instead."""
        from PIL import Image

        original = Image.MAX_IMAGE_PIXELS
        try:
            Image.MAX_IMAGE_PIXELS = None
            with self.assertRaises(SystemExit):
                cis._pin_pillow_limits()
        finally:
            Image.MAX_IMAGE_PIXELS = original

    def test_decodes_normally_with_the_guard_intact(self):
        import tempfile

        from PIL import Image

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fine.png"
            Image.new("RGB", (64, 64)).save(path)
            self.assertEqual(cis._decode_refusal(path), "",
                             "a normal image must not be reported as refused")


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


class TestSecretScannerInvariants(unittest.TestCase):
    """The secret gate's own guards. Each of these caught a real defect."""

    def test_oversize_blob_is_reported_not_skipped(self):
        """A blob over the cap must be a FINDING, not a silent pass.

        'Too large to inspect' reading the same as 'clean' is the fail-open
        shape this file has produced repeatedly.
        """
        import subprocess, tempfile, os
        with tempfile.TemporaryDirectory() as d:
            subprocess.run(["git", "init", "-q", "."], cwd=d, check=True)
            big = os.path.join(d, "big.dat")
            with open(big, "w") as fh:
                fh.write("x" * (5 * 1024 * 1024))
            subprocess.run(["git", "add", "-A"], cwd=d, check=True)
            subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=a",
                            "commit", "-qm", "big"], cwd=d, check=True)
            scanner = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "check-secrets.py")
            out = subprocess.run(["python3", scanner, "."], cwd=d,
                                 capture_output=True, text=True)
            self.assertEqual(out.returncode, 1, "an un-scanned file must fail the gate")
            self.assertIn("OVERSIZE", out.stdout)
            self.assertIn("NOT inspected", out.stdout)

    def test_identity_lookup_is_resolved_once_per_file(self):
        """`author_usernames()` must not be called inside the per-line loop.

        It spawns two `git config` subprocesses per call; calling it per line made
        a 8.7k-line audit take 20.5s instead of 0.4s.
        """
        src = (Path(__file__).parent / "check-secrets.py").read_text()
        body = src.split("def audit()", 1)[1]
        loop = body.split("for line_number, line in enumerate", 1)[1]
        self.assertNotIn("author_usernames()", loop,
                         "author_usernames() is back inside the per-line loop")


class TestTruncationIsNeverClean(unittest.TestCase):
    """An exhausted inflation budget must not read as CLEAN.

    A credential whose text chunk sat AFTER the budget was spent was stored as
    '<truncated at the size cap>' — a string matching no detector — so the audit
    reported CLEAN on a fully decodable PNG carrying a live AWS key. The marker
    is a sentinel now, and the audit turns it into a finding.
    """

    def _png(self, chunks):
        import zlib, struct
        def ch(t, d):
            return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
        blob = b"\x89PNG\r\n\x1a\n"
        for k, v in chunks:
            blob += ch(b"zTXt", k + b"\x00\x00" + zlib.compress(v))
        return blob

    def test_exhausted_budget_yields_the_sentinel_not_a_plain_string(self):
        self.assertIn(cis.TRUNCATED_TEXT, cis._inflate(b"x", 0))

    def test_chunk_after_budget_is_visible_as_truncated(self):
        orig_budget = cis.MAX_TOTAL_INFLATED_BYTES
        cis.MAX_TOTAL_INFLATED_BYTES = 200_000
        try:
            blob = self._png([(b"p0", b"A" * 100_000), (b"p1", b"A" * 100_000),
                              (b"note", b'aws = "' + b"AKIA" + b'IOSFODNN7EXAMPLE"')])
            out = cis.png_text_chunks(blob)
        finally:
            cis.MAX_TOTAL_INFLATED_BYTES = orig_budget
        self.assertIn("note", out, "the chunk must still be reported as present")
        self.assertIn(cis.TRUNCATED_TEXT, out["note"],
                      "a partially scanned chunk must be marked, not silently clean")

    def test_truncated_metadata_becomes_a_finding(self):
        """The audit must report a partially-scanned chunk rather than pass it."""
        import tempfile, os
        tmp = tempfile.mkdtemp()
        try:
            pth = os.path.join(tmp, "t.png")
            blob = self._png([(b"p0", b"A" * 100_000), (b"p1", b"A" * 100_000),
                              (b"note", b"x")])
            # a minimal valid PNG so the decodability check passes
            import zlib, struct
            def ch(t, d):
                return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
            w = h = 8
            raw = b"".join(b"\x00" + bytes([i % 256 for i in range(w * 3)]) for i in range(h))
            full = (b"\x89PNG\r\n\x1a\n"
                    + ch(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                    + blob[8:]
                    + ch(b"IDAT", zlib.compress(raw, 6)) + ch(b"IEND", b""))
            with open(pth, "wb") as fh:
                fh.write(full)
            orig_budget = cis.MAX_TOTAL_INFLATED_BYTES
            cis.MAX_TOTAL_INFLATED_BYTES = 200_000
            try:
                findings = cis.audit_image(Path(pth))
            finally:
                cis.MAX_TOTAL_INFLATED_BYTES = orig_budget
            labels = " ".join(l for _o, l, _d in findings)
            self.assertIn("not fully scanned", labels,
                          "a truncated metadata scan must be reported, not passed")
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


class TestSecretScanDecodesRealEncodings(unittest.TestCase):
    """A NUL-bearing file must still be scanned in the right codec.

    The scanner's own comment names a UTF-16 file as the reason to scan
    NUL-containing blobs, and the code then decoded them as UTF-8 — which turns
    UTF-16 into NUL-separated characters, so no pattern could match and a real
    key sat in the tree un-reported.
    """

    def _scan(self, blob: bytes) -> str:
        import subprocess, tempfile, os, shutil
        d = tempfile.mkdtemp()
        try:
            with open(os.path.join(d, "f.txt"), "wb") as fh:
                fh.write(blob)
            subprocess.run(["git", "init", "-q", "."], cwd=d, check=True)
            subprocess.run(["git", "add", "-A"], cwd=d, check=True)
            out = subprocess.run(
                ["python3", str(HERE / "check-secrets.py"), "."],
                cwd=d, capture_output=True, text=True)
            return out.stdout
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def _key(self) -> bytes:
        return ("OPENAI_API_KEY=" + "sk-pro" + "j-AbCdEf1234567890AbCdEf1234567890\n").encode()

    def test_utf16_le_with_bom(self):
        self.assertIn("SECRET", self._scan(self._key().decode().encode("utf-16")))

    def test_utf16_le_without_bom(self):
        self.assertIn("SECRET", self._scan(self._key().decode().encode("utf-16-le")))

    def test_utf16_be_without_bom(self):
        self.assertIn("SECRET", self._scan(self._key().decode().encode("utf-16-be")))

    def test_plain_utf8_control(self):
        self.assertIn("SECRET", self._scan(self._key()))


class TestGateRobustness(unittest.TestCase):
    """Regressions for defects the adversarial review demonstrated."""

    def _bomb(self, tmp: Path) -> Path:
        """A tiny PNG declaring an enormous raster, built OUT of process.

        Building it here needs a 495 MB raw buffer, which would dominate whichever
        process measures RSS — the test would then measure its own fixture rather
        than the code under test. Generate it in a short-lived child.
        """
        import subprocess, sys, textwrap
        pth = tmp / "bomb.png"
        gen = (
            "import zlib, struct\n"
            "def ch(t, d):\n"
            "    return struct.pack('>I', len(d)) + t + d + "
            "struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)\n"
            "w, h = 16500, 10000\n"
            "raw = (b'\\x00' + bytes(w * 3)) * h\n"
            "out = (b'\\x89PNG\\r\\n\\x1a\\n'\n"
            "       + ch(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0))\n"
            "       + ch(b'IDAT', zlib.compress(raw, 9)) + ch(b'IEND', b''))\n"
            f"open({str(pth)!r}, 'wb').write(out)\n"
        )
        subprocess.run([sys.executable, "-c", gen], check=True)
        return pth

    def _png_with_text(self, pth: Path, key: bytes, value: bytes) -> Path:
        """A real, decodable PNG carrying a tEXt chunk."""
        import zlib, struct
        def ch(t, d):
            return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
        w = h = 8
        raw = b"".join(b"\x00" + bytes([i % 256 for i in range(w * 3)]) for i in range(h))
        blob = (b"\x89PNG\r\n\x1a\n"
                + ch(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                + ch(b"tEXt", key + b"\x00" + value)
                + ch(b"IDAT", zlib.compress(raw, 6)) + ch(b"IEND", b""))
        pth.write_bytes(blob)
        return pth

    def test_pixel_bomb_does_not_allocate_the_raster(self):
        """`_looks_like_an_image` must not decode a 165-megapixel file.

        `load()` allocates the full raster: the gate peaked at ~650 MB answering
        "is this decodable?" for a 481 KB file. The declared size is checked
        before any pixel work.
        """
        import tempfile, subprocess, sys, textwrap
        with tempfile.TemporaryDirectory() as tmp:
            bomb = self._bomb(Path(tmp))
            # Measure in a FRESH process. Building the 495 MB raw buffer here
            # would inflate this process's RSS and measure the wrong thing.
            code = textwrap.dedent(f"""
                import importlib.util, resource, sys
                from pathlib import Path
                spec = importlib.util.spec_from_file_location("cis", {str(SCANNER)!r})
                cis = importlib.util.module_from_spec(spec); spec.loader.exec_module(cis)
                r = cis._looks_like_an_image(Path({str(bomb)!r}))
                sys.stderr.write(f"{{r}} {{resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024}}")
            """)
            out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
            verdict, mb = out.stderr.strip().splitlines()[-1].split()
            self.assertEqual(verdict, "False", "a 165 Mpx file is not a usable image")
            self.assertLess(int(mb), 200, f"pixel check allocated {mb} MB")

    def test_oversize_file_does_not_discard_earlier_findings(self):
        """A finding collected before an oversized path must still be printed."""
        import tempfile, subprocess, sys
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            leak = self._png_with_text(
                d / "leak.png", b"note", b'aws = "' + b"AKIA" + b'IOSFODNN7EXAMPLE"')
            big = d / "big.bin"
            big.write_bytes(b"x" * (65 * 1024 * 1024 + 1024))
            for order in ([leak, big], [big, leak]):
                with self.subTest(order=[p.name for p in order]):
                    out = subprocess.run([sys.executable, str(SCANNER)] + [str(p) for p in order],
                                         capture_output=True, text=True)
                    self.assertEqual(out.returncode, 1)
                    self.assertIn("aws access key", out.stdout,
                                  "a finding before an oversized file was discarded")

    def test_non_utf8_filename_still_yields_a_verdict(self):
        """A surrogate-escaped path must not crash the reporting path.

        Printing it raised UnicodeEncodeError, so the gate emitted NO verdict —
        it crashed while reporting, which reads as "nothing to report".
        """
        import tempfile, subprocess, os, sys
        with tempfile.TemporaryDirectory() as tmp:
            raw = os.fsencode(tmp) + b"/\xff\xfe\x80\x81.png"
            with open(raw, "wb") as fh:
                fh.write(b"\x89PNG\r\n\x1a\n" + b"truncated")
            out = subprocess.run([sys.executable, str(SCANNER), os.fsdecode(raw)],
                                 capture_output=True)
            self.assertNotIn(b"Traceback", out.stderr)
            self.assertNotEqual(out.returncode, 2, "must not be a crash")
            self.assertTrue(b"FINDING" in out.stdout or b"CLEAN" in out.stdout,
                            "no verdict was emitted")


class TestBrokenPillowInstall(unittest.TestCase):
    """The refusal must hold on a REAL broken install, not a mock.

    `test_main_refuses_to_report_without_pillow` patches `cis._has_pillow`, so it
    asserts that main() honours the flag — it cannot see whether `_has_pillow()`
    actually DETECTS a broken install. A Pillow that imports but cannot load its
    shared library raises OSError, not ImportError, and an `except ImportError`
    would let the gate proceed into an audit that reads no metadata at all. That
    is the documented case, so it is exercised here with a real shadow package.
    """

    def test_real_broken_install_is_detected_and_refused(self):
        import os, subprocess, sys, tempfile
        with tempfile.TemporaryDirectory() as tmp:
            shadow = Path(tmp) / "shadow"
            (shadow / "PIL").mkdir(parents=True)
            # A PIL that raises OSError at import — the shape of a missing
            # libjpeg.so, which is the real-world broken-install case.
            (shadow / "PIL" / "__init__.py").write_text(
                'raise OSError("libjpeg.so.9: cannot open shared object file")\n'
            )
            img = Path(tmp) / "shot.png"
            img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"truncated")
            env = dict(os.environ, PYTHONPATH=str(shadow))
            out = subprocess.run([sys.executable, str(SCANNER), str(img)],
                                 capture_output=True, text=True, env=env)
            self.assertEqual(out.returncode, 2,
                             "a broken Pillow must refuse, not audit blindly")
            self.assertIn("refusing to report a verdict", out.stderr.lower(),
                          f"the refusal must state it is refusing: {out.stderr!r}")
            self.assertNotIn("CLEAN", out.stdout,
                             "a broken install must never produce a CLEAN verdict")
            self.assertNotIn("Traceback", out.stderr,
                             "the detection must not crash out of the audit")


class TestSecretScanCoversWhatAPushPublishes(unittest.TestCase):
    """The gate must scan what a clone receives, not just what is staged.

    `git ls-files` lists the INDEX. A path removed from the index while its blob
    still exists in HEAD (a `git rm --cached`, the delete half of a rename) was
    never visited: the gate reported CLEAN while `git cat-file -p HEAD:<path>`
    returned the credential.
    """

    def test_path_in_head_but_not_the_index_is_still_scanned(self):
        import os, shutil, subprocess, sys, tempfile
        d = tempfile.mkdtemp()
        try:
            def run(*args):
                return subprocess.run(list(args), cwd=d, capture_output=True,
                                      text=True)
            run("git", "init", "-q", ".")
            leak = os.path.join(d, "leak.txt")
            with open(leak, "w") as fh:
                fh.write('aws = "' + "AKIA" + 'IOSFODNN7EXAMPLE"\n')
            run("git", "add", "-A")
            run("git", "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "x")
            # remove from the index, keeping the blob in HEAD
            run("git", "rm", "--cached", "-q", "leak.txt")
            os.unlink(leak)

            self.assertEqual(run("git", "ls-files").stdout.strip(), "",
                             "precondition: nothing is tracked")
            self.assertIn("AKIA", run("git", "cat-file", "-p", "HEAD:leak.txt").stdout,
                          "precondition: the blob is still in HEAD")

            out = subprocess.run([sys.executable, str(HERE / "check-secrets.py"), "."],
                                 cwd=d, capture_output=True, text=True)
            self.assertEqual(out.returncode, 1,
                             "a credential in HEAD must fail the gate")
            self.assertIn("SECRET", out.stdout,
                          f"the HEAD-only path was not scanned: {out.stdout!r}")
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)


