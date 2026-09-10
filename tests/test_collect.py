#!/usr/bin/env python3
"""Behavioral tests for scripts/deck-collect.

Each test builds a throwaway HERMES_HOME (SQLite state.db, config.yaml,
ESTOP sentinel) plus a stub `hermes` CLI on PATH, runs the collector as a
subprocess, and asserts the JSON contract. No live Hermes state is touched.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
COLLECT = HERE.parent / "scripts" / "deck-collect"
STUB_BIN = HERE / "fixtures" / "bin"

SCHEMA_SESSIONS = """
CREATE TABLE sessions (
    id TEXT PRIMARY KEY, source TEXT NOT NULL, title TEXT, display_name TEXT,
    model TEXT, message_count INTEGER DEFAULT 0, started_at REAL NOT NULL,
    last_activity_at REAL, cwd TEXT, pinned INTEGER DEFAULT 0,
    estimated_cost_usd REAL, hidden INTEGER DEFAULT 0, archived INTEGER DEFAULT 0
);
CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT,
    timestamp REAL NOT NULL
);
CREATE TABLE session_model_usage (
    session_id TEXT, model TEXT, billing_provider TEXT DEFAULT '',
    input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0,
    cache_read_tokens INTEGER DEFAULT 0, cache_write_tokens INTEGER DEFAULT 0,
    estimated_cost_usd REAL DEFAULT 0,
    PRIMARY KEY (session_id, model, billing_provider)
);
"""

def make_db(path: Path, now: float) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA_SESSIONS)
    conn.execute(
        "INSERT INTO sessions VALUES ('aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',"
        " 'cli', 'Fix the parser bug', NULL, 'glm-5.3-flash', 4, ?, ?, ?, 1, 0.25, 0, 0)",
        (now - 120, now - 30, "/home/u/Work/demo"),
    )
    conn.execute(
        "INSERT INTO sessions VALUES ('11111111-2222-3333-4444-555555555555',"
        " 'telegram', 'Gateway chat', 'GW user', 'grok-4.6', 2, ?, ?, NULL, 0, 0.10, 0, 0)",
        (now - 86400, now - 86000),
    )
    conn.execute(
        "INSERT INTO messages (session_id, role, timestamp) VALUES"
        " ('aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee', 'user', ?)",
        (now - 40,),
    )
    conn.execute(
        "INSERT INTO messages (session_id, role, timestamp) VALUES"
        " ('aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee', 'assistant', ?)",
        (now - 10,),
    )
    conn.execute(
        "INSERT INTO session_model_usage VALUES"
        " ('aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee', 'glm-5.3-flash', '', 900000, 200000, 82000, 1000, 0.25)"
    )
    conn.execute(
        "INSERT INTO session_model_usage VALUES"
        " ('11111111-2222-3333-4444-555555555555', 'grok-4.6', '', 50000, 2000, 0, 0, 0.10)"
    )
    conn.commit()
    conn.close()


def run_collect(home: Path, state_home: Path, *flags: str, extra_env: dict | None = None) -> dict:
    env = dict(os.environ)
    env["HERMES_HOME"] = str(home)
    env["XDG_STATE_HOME"] = str(state_home)
    env["PATH"] = f"{STUB_BIN}:{env['PATH']}"
    if extra_env:
        env.update(extra_env)
    proc = subprocess.run(
        [sys.executable, str(COLLECT), *flags],
        capture_output=True,
        timeout=60,
        env=env,
        stdin=subprocess.DEVNULL,
    )
    payload = json.loads(proc.stdout.decode())
    assert isinstance(payload, dict), "collector must emit one JSON object"
    return payload


class CollectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.home = base / "hermes"
        self.home.mkdir()
        self.state = base / "state"
        self.now = time.time()
        make_db(self.home / "state.db", self.now)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write_config(self, body: str) -> None:
        (self.home / "config.yaml").write_text(body)

    def test_full_snapshot_shape(self) -> None:
        self.write_config("model: glm-5.3-flash\n")
        snap = run_collect(self.home, self.state, "--include-slow")
        self.assertTrue(snap["installed"])
        self.assertEqual(snap["schemaVersion"], 2)
        self.assertEqual(snap["model"], "glm-5.3-flash")
        self.assertEqual(snap["modelSource"], "config")
        self.assertEqual(snap["version"], "stub 1.2.3")
        self.assertFalse(snap["paused"])
        self.assertIn("serviceState", snap["gateway"])
        # usage aggregates
        today = snap["usage"]["today"]
        self.assertEqual(today["tokens"], 900000 + 200000 + 82000 + 1000)
        self.assertEqual(today["prompts"], 1)
        self.assertEqual(today["sessions"], 1)
        self.assertAlmostEqual(today["costUsd"], 0.25, places=4)
        self.assertAlmostEqual(snap["usage"]["week"]["costUsd"], 0.35, places=4)
        # sessions: recent first, gateway session included
        ids = [s["id"] for s in snap["sessions"]]
        self.assertEqual(ids[0], "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        self.assertIn("11111111-2222-3333-4444-555555555555", ids)
        gateway = next(s for s in snap["sessions"] if s["source"] == "telegram")
        self.assertEqual(gateway["title"], "Gateway chat")
        # activity: last message 10s ago → working
        self.assertTrue(snap["activity"]["working"])
        # auth from stub CLI
        self.assertEqual(
            [p["provider"] for p in snap["auth"]], ["openai-codex", "xai-oauth"]
        )

    def test_nested_default_model(self) -> None:
        self.write_config("model:\n  default: grok-4.6\n  temperature: 0.7\n")
        snap = run_collect(self.home, self.state)
        self.assertEqual(snap["model"], "grok-4.6")
        self.assertEqual(snap["modelSource"], "config")

    def test_dict_format_model_falls_back_to_cli(self) -> None:
        # hermes-harness#2 regression: custom-endpoint dict blocks must not
        # produce a wrong answer; the collector defers to the CLI fallback.
        self.write_config(
            "model:\n  provider: custom-endpoint\n  base_url: https://example.internal/v1\n"
        )
        snap = run_collect(self.home, self.state, "--include-slow")
        self.assertEqual(snap["model"], "cli-resolved-model")
        self.assertEqual(snap["modelSource"], "cli")
    def test_missing_db_is_fail_muted(self) -> None:
        (self.home / "state.db").unlink()
        self.write_config("model: glm-5.3-flash\n")
        snap = run_collect(self.home, self.state)
        self.assertTrue(snap["installed"])
        self.assertEqual(snap["usage"], {})
        self.assertEqual(snap["sessions"], [])
        self.assertTrue(any("state.db" in e for e in snap["errors"]))

    def test_estop_engaged_with_reason(self) -> None:
        self.write_config("model: glm-5.3-flash\n")
        (self.home / "ESTOP").write_text(
            json.dumps({"reason": "deploy window", "engaged_at": 123.5})
        )
        snap = run_collect(self.home, self.state)
        self.assertTrue(snap["paused"])
        self.assertEqual(snap["reason"], "deploy window")
        self.assertEqual(snap["engagedAt"], 123.5)

    def test_estop_malformed_still_reports_engaged(self) -> None:
        self.write_config("model: glm-5.3-flash\n")
        (self.home / "ESTOP").write_text("garbage{{")
        snap = run_collect(self.home, self.state)
        self.assertTrue(snap["paused"])
        self.assertEqual(snap["reason"], "")

    def test_kanban_from_stub_cli(self) -> None:
        self.write_config("model: glm-5.3-flash\n")
        snap = run_collect(self.home, self.state, "--include-kanban")
        self.assertTrue(snap["kanban"]["available"])
        self.assertEqual(snap["kanban"]["boards"][0]["slug"], "default")

    def test_kanban_flag_off_keeps_fresh_data_out(self) -> None:
        self.write_config("model: glm-5.3-flash\n")
        snap = run_collect(self.home, self.state)
        self.assertFalse(snap["kanban"]["available"])

    def test_hermes_missing_reports_offline(self) -> None:
        env = dict(os.environ)
        env["HERMES_HOME"] = str(self.home)
        env["XDG_STATE_HOME"] = str(self.state)
        env["HOME"] = str(self.tmp.name)
        env["PATH"] = "/nonexistent"
        proc = subprocess.run(
            [sys.executable, str(COLLECT)], capture_output=True, timeout=60, env=env
        )
        snap = json.loads(proc.stdout.decode())
        self.assertFalse(snap["installed"])
        self.assertEqual(snap["auth"], [])
        self.assertEqual(snap["kanban"]["available"], False)
    def test_default_agent_detected(self) -> None:
        self.write_config("model: glm-5.3-flash\n")
        cfg_dir = Path(self.tmp.name) / "config" / "omarchy" / "defaults"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "agent").write_text("hermes\n")
        snap = run_collect(self.home, self.state, extra_env={"XDG_CONFIG_HOME": str(Path(self.tmp.name) / "config")})
        self.assertTrue(snap.get("isDefaultAgent"))

    def test_default_agent_not_hermes(self) -> None:
        self.write_config("model: glm-5.3-flash\n")
        cfg_dir = Path(self.tmp.name) / "config" / "omarchy" / "defaults"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "agent").write_text("omp\n")
        snap = run_collect(self.home, self.state, extra_env={"XDG_CONFIG_HOME": str(Path(self.tmp.name) / "config")})
        self.assertFalse(snap.get("isDefaultAgent"))

    def test_desktop_available_detection(self) -> None:
        self.write_config("model: glm-5.3-flash\n")
        bin_dir = Path(self.tmp.name) / "mock_bin"
        bin_dir.mkdir()
        desktop_bin = bin_dir / "hermes-desktop"
        desktop_bin.write_text("#!/bin/sh\nexit 0\n")
        desktop_bin.chmod(0o755)
        # Prepend mock_bin to PATH
        snap = run_collect(self.home, self.state, extra_env={"PATH": f"{bin_dir}:{STUB_BIN}:{os.environ['PATH']}"})
        self.assertTrue(snap.get("desktopAvailable"))

    def test_gateway_platforms_and_active_agents(self) -> None:
        self.write_config("model: glm-5.3-flash\n")
        gw_file = self.home / "gateway_state.json"
        gw_file.write_text(json.dumps({
            "gateway_state": "running",
            "active_agents": 3,
            "platforms": {
                "telegram": {"state": "connected"},
                "slack": {"state": "disconnected"}
            }
        }))
        snap = run_collect(self.home, self.state)
        gw = snap.get("gateway", {})
        self.assertEqual(gw.get("activeAgentsCount"), 3)
        self.assertEqual(gw.get("connectedPlatforms"), ["telegram"])

    def test_gateway_state_corrupt_fails_soft(self) -> None:
        self.write_config("model: glm-5.3-flash\n")
        gw_file = self.home / "gateway_state.json"
        gw_file.write_text("garbage{invalid")
        snap = run_collect(self.home, self.state)
        gw = snap.get("gateway", {})
        self.assertEqual(gw.get("activeAgentsCount"), 0)
        self.assertEqual(gw.get("connectedPlatforms"), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
