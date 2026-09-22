#!/usr/bin/env python3
"""Regression tests for the image audit's detection logic.

Each case here is a false negative that reached a CLEAN verdict once. The
exemption rules exist to suppress OCR noise on product names; the tests pin
that they must not suppress a credential standing next to one.

Run:  python3 tests/test_image_audit.py
"""

from __future__ import annotations

import importlib.util
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
    def test_ocr_has_a_timeout(self):
        import inspect

        self.assertIn("timeout", inspect.getsource(cis.ocr_text),
                      "subprocess.run without timeout can hang the gate forever")


class TestReadBounds(unittest.TestCase):
    def test_oversized_image_is_refused_not_read(self):
        import inspect

        source = inspect.getsource(cis.image_metadata)
        self.assertIn("MAX_IMAGE_BYTES", source,
                      "image_metadata reads an unbounded file into memory")


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


class TestLeakDetectors(unittest.TestCase):
    """The exemption is exercised through audit_image, not just scan_text."""

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
