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

    # ── session management (pin / unpin / rename) ────────────────────────
    # Hermes stores the pin flag in the sessions table, and the same flag
    # drives the Desktop sidebar's Pinned section, so a pin set here is
    # visible there. These assert the exact argv and the argument gate.

    def test_pin_session_exec_exact_command(self) -> None:
        proc = self.run_act("pin-session", VALID_HERMES_ID)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.recorded_argv(), ["hermes", "sessions", "pin", VALID_HERMES_ID])

    def test_unpin_session_exec_exact_command(self) -> None:
        proc = self.run_act("unpin-session", VALID_UUID)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.recorded_argv(), ["hermes", "sessions", "unpin", VALID_UUID])

    def test_rename_session_exec_exact_command(self) -> None:
        proc = self.run_act("rename-session", VALID_HERMES_ID, "Gateway work")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            self.recorded_argv(),
            ["hermes", "sessions", "rename", VALID_HERMES_ID, "Gateway work"],
        )

    def test_rename_session_title_is_one_argv_entry(self) -> None:
        """A title with spaces and punctuation must stay a single argv entry.

        Titles reach this from the panel, where the user typed them; they may
        contain anything. Argument-vector exec is what keeps that safe.
        """
        title = "bug: parser drops 'x' & y > z; rm -rf /"
        proc = self.run_act("rename-session", VALID_HERMES_ID, title)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        argv = self.recorded_argv()
        self.assertEqual(len(argv), 5)
        self.assertEqual(argv[4], title)

    def test_rename_session_rejects_overlong_title(self) -> None:
        proc = self.run_act("rename-session", VALID_HERMES_ID, "x" * 201)
        self.assertNotEqual(proc.returncode, 0)
        self.assertNotIn(b"x" * 201, proc.stdout)

    def test_rename_session_rejects_empty_title(self) -> None:
        self.assertNotEqual(self.run_act("rename-session", VALID_HERMES_ID, "").returncode, 0)

    def test_pin_session_rejects_shell_metacharacters(self) -> None:
        for bad in ("abc; rm -rf /", "abc$(whoami)", "abc|tee", "abc`id`", "abc&"):
            with self.subTest(bad=bad):
                self.assertNotEqual(self.run_act("pin-session", bad).returncode, 0)

    def test_pin_session_rejects_short_and_option_like(self) -> None:
        for bad in ("short", "-flag", "--all", ""):
            with self.subTest(bad=bad):
                self.assertNotEqual(self.run_act("pin-session", bad).returncode, 0)

    def test_session_actions_require_an_argument(self) -> None:
        for action in ("pin-session", "unpin-session", "rename-session"):
            with self.subTest(action=action):
                self.assertNotEqual(self.run_act(action).returncode, 0)

    def test_archive_session_is_not_an_action(self) -> None:
        """`hermes sessions archive` is bulk-only and takes no session id.

        A per-session `archive-session` action was written, reviewed, and
        removed: the real CLI rejects a positional id ("unrecognized
        arguments", exit 2), so the action could never archive anything. The
        stub now mirrors that contract, and this pins that we do not re-add a
        button the CLI cannot serve.
        """
        self.assertNotEqual(self.run_act("archive-session", "sess-abc").returncode, 0)
        output = self.run_act().stderr.decode() + self.run_act().stdout.decode()
        self.assertNotIn("archive-session", output)

    # Measured against the real CLI: (argv, expected exit code).
    ARCHIVE_CONTRACT = [
        ([], 0),                                       # refuses, but exits 0
        (["--dry-run"], 0),                            # --dry-run is not a filter
        (["--older-than"], 2),                         # flag with no value
        (["--older-than", "30d"], 0),
        (["--yes", "--older-than", "30d"], 0),          # --yes is value-less
        (["--title=foo"], 0),                          # --flag=value form
        (["sess-abc"], 2),                             # positional id rejected
        (["--include-pinned", "--older-than", "30d"], 2),  # not an archive flag
    ]

    def test_stub_models_the_real_archive_contract(self) -> None:
        """Guard the guard: the fixture must fail exactly where the CLI does.

        An earlier stub assumed every `--flag` consumes a value, which certified
        argv shapes the real CLI rejects and rejected one it accepts. These cases
        were measured against `hermes sessions archive` on this machine.
        """
        for argv, expected in self.ARCHIVE_CONTRACT:
            with self.subTest(argv):
                result = subprocess.run(
                    [str(STUB_BIN / "hermes"), "sessions", "archive", *argv],
                    capture_output=True, timeout=20, stdin=subprocess.DEVNULL,
                )
                self.assertEqual(result.returncode, expected,
                                 f"stub disagrees with the real CLI for {argv}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
