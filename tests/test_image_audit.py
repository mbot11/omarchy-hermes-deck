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

# These MUST stay exempt: OCR reading a product name as an address.
BENIGN_CASES = [
    ("product name as an address", "deepseek-v4.1-flash@ollama-cloud"),
]


class TestNoiseExemptionIsScoped(unittest.TestCase):
    def test_credential_beside_a_noise_word_is_still_reported(self):
        for label, line in BYPASS_CASES:
            with self.subTest(label):
                hits = cis.scan_text(line, "t")
                self.assertTrue(hits, f"false CLEAN on: {line!r}")

    def test_product_name_address_stays_exempt(self):
        for label, line in BENIGN_CASES:
            with self.subTest(label):
                self.assertFalse(
                    cis.scan_text(line, "t"),
                    f"benign product name reported as a finding: {line!r}",
                )


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
