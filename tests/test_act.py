#!/usr/bin/env python3
"""Behavioral tests for scripts/deck-act — the action allowlist boundary.

Runs deck-act as a subprocess with stub `hermes` and `omarchy-launch-tui`
binaries on PATH. The stubs record their argv so every test asserts the
exact command that would run. No Hermes state is touched.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ACT = HERE.parent / "scripts" / "deck-act"
STUB_BIN = HERE / "fixtures" / "bin"

VALID_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
VALID_HERMES_ID = "20260904_204532_c3ca2e"


class ActTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.record = Path(self.tmp.name) / "argv.json"
        env = dict(os.environ)
        env["DECK_ACT_RECORD"] = str(self.record)
        env["OMARCHY_LAUNCH_TUI"] = str(STUB_BIN / "omarchy-launch-tui")
        env["OMARCHY_BIN"] = str(STUB_BIN / "omarchy")
        env["UWSM_APP"] = str(STUB_BIN / "uwsm-app")
        env["HERMES_DESKTOP"] = "/usr/bin/hermes-desktop"
        env["PATH"] = f"{STUB_BIN}:{env['PATH']}"
        self.env = env

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_act(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [str(ACT), *args],
            capture_output=True,
            timeout=20,
            env=self.env,
            stdin=subprocess.DEVNULL,
        )
    def recorded_argv(self) -> list:
        lines = [line for line in self.record.read_text().splitlines() if line.strip()]
        argv = json.loads(lines[-1])
        # deck-act resolves hermes to an absolute path before exec; only the
        # binary's basename is part of the contract under test.
        if argv and argv[0].endswith("/hermes"):
            argv[0] = "hermes"
        return argv
    def test_resume_session_valid_hermes_id(self) -> None:
        """Real Hermes ids are YYYYMMDD_HHMMSS_xxxxxx — the panel's session rows."""
        proc = self.run_act("resume-session", VALID_HERMES_ID)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        argv = self.recorded_argv()
        self.assertEqual(argv[0], "omarchy-launch-tui")
        self.assertIn(VALID_HERMES_ID, argv)

    def test_resume_session_valid_uuid(self) -> None:
        proc = self.run_act("resume-session", VALID_UUID)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        argv = self.recorded_argv()
        self.assertEqual(argv[0], "omarchy-launch-tui")
        self.assertIn("--app-id=org.omarchy.agent", argv)
        self.assertIn("hermes", argv)
        self.assertIn("--resume", argv)
        self.assertIn(VALID_UUID, argv)


    def test_resume_session_rejects_shell_metacharacters(self) -> None:
        for bad in ("a;rm -rf /", "$(id)", "`id`", "x && y", "a\nb", "uuid; touch pwn"):
            with self.subTest(bad=bad):
                proc = self.run_act("resume-session", bad)
                self.assertEqual(proc.returncode, 65)

    def test_resume_session_rejects_short_and_option_like(self) -> None:
        for bad in ("abc", "--help", "-h", "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeeX" * 2):
            with self.subTest(bad=bad):
                proc = self.run_act("resume-session", bad)
                self.assertEqual(proc.returncode, 65)

    # ── model id boundary ────────────────────────────────────────────────

    def test_set_model_valid(self) -> None:
        proc = self.run_act("set-model", "gpt-oss:120b")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        argv = self.recorded_argv()
        self.assertEqual(argv[:4], ["hermes", "config", "set", "model.default"])
        self.assertEqual(argv[4], "gpt-oss:120b")

    def test_set_model_rejects_leading_dash(self) -> None:
        for bad in ("-h", "--json", "--insecure", "-"):
            with self.subTest(bad=bad):
                proc = self.run_act("set-model", bad)
                self.assertEqual(proc.returncode, 65)

    def test_set_model_rejects_shell_metacharacters(self) -> None:
        for bad in ("model;id", "a b", "$(id)", "a\nb"):
            with self.subTest(bad=bad):
                proc = self.run_act("set-model", bad)
                self.assertEqual(proc.returncode, 65)

    # ── argv fidelity of remaining actions ───────────────────────────────

    def test_pause_and_resume_exec_exact_command(self) -> None:
        for action, expected in (("pause", ["hermes", "pause"]), ("resume", ["hermes", "resume"])):
            with self.subTest(action=action):
                proc = self.run_act(action)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(self.recorded_argv(), expected)

    def test_gateway_restart_exec_exact_command(self) -> None:
        proc = self.run_act("gateway-restart")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.recorded_argv(), ["hermes", "gateway", "restart"])

    def test_launch_desktop_exec_exact_command(self) -> None:
        proc = self.run_act("launch-desktop")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.recorded_argv(), ["uwsm-app", "--", "/usr/bin/hermes-desktop"])

    def test_set_default_agent_exec_exact_command(self) -> None:
        proc = self.run_act("set-default-agent")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.recorded_argv(), ["omarchy", "default", "agent", "hermes"])

    def test_new_session_prompt_is_single_argv_entry(self) -> None:
        nasty = "write a file; $(rm -rf /) `id` | cat"
        proc = self.run_act("new-session", nasty)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        argv = self.recorded_argv()
        self.assertIn("-z", argv)
        z_index = argv.index("-z")
        self.assertEqual(argv[z_index + 1], nasty)
        self.assertNotIn("|", argv)
        self.assertNotIn(";", argv)

    def test_unknown_action_fails(self) -> None:
        proc = self.run_act("format-disk")
        self.assertNotEqual(proc.returncode, 0)

    def test_missing_argument_fails(self) -> None:
        self.assertNotEqual(self.run_act("resume-session").returncode, 0)
        self.assertNotEqual(self.run_act("set-model").returncode, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
