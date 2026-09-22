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
    timestamp REAL NOT NULL, content TEXT
);
-- Mirror the live store: messages_fts is an external-content-free fts5 table
-- kept in rowid lockstep with `messages` by triggers. Search joins on rowid.
CREATE VIRTUAL TABLE messages_fts USING fts5(content);
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
        "INSERT INTO messages (session_id, role, timestamp, content) VALUES"
        " ('aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee', 'user', ?, ?)",
        (now - 40, "the parser drops the trailing comma token"),
    )
    conn.execute(
        "INSERT INTO messages (session_id, role, timestamp, content) VALUES"
        " ('aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee', 'assistant', ?, ?)",
        (now - 10, "patched lexer.py to keep the comma"),
    )
    # Keep the fts index in step with `messages`, exactly as the live triggers do.
    conn.execute("INSERT INTO messages_fts(rowid, content) SELECT id, content FROM messages")
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

    def test_pinned_and_hidden_read_from_their_columns(self) -> None:
        """The session query must read `pinned` and filter on `hidden`.

        Both columns exist in the live store (schema v30). A query that
        substitutes a literal `0 AS pinned` reports every pinned session as
        unpinned, and one that drops `hidden = 0` lists sessions the user
        deliberately hid. Neither failure throws, so only an assertion
        catches it.
        """
        self.write_config("model: glm-5.3-flash\n")
        conn = sqlite3.connect(self.home / "state.db")
        conn.execute(
            "INSERT INTO sessions VALUES ('bbbbbbbb-cccc-dddd-eeee-ffffffffffff',"
            " 'cli', 'Pinned session', NULL, 'glm-5.3-flash', 1, ?, ?, ?, 1, 0.0, 0, 0)",
            (self.now - 60, self.now - 30, "/home/u/Work/demo"),
        )
        conn.execute(
            "INSERT INTO sessions VALUES ('cccccccc-dddd-eeee-ffff-000000000000',"
            " 'cli', 'Hidden session', NULL, 'glm-5.3-flash', 1, ?, ?, ?, 0, 0.0, 1, 0)",
            (self.now - 60, self.now - 20, "/home/u/Work/demo"),
        )
        conn.commit()
        conn.close()

        snap = run_collect(self.home, self.state)
        by_title = {session["title"]: session for session in snap["sessions"]}

        self.assertIn("Pinned session", by_title, "a pinned session must still be listed")
        self.assertTrue(by_title["Pinned session"]["pinned"],
                        "pinned must come from the column, not a literal")
        self.assertNotIn("Hidden session", by_title,
                         "a session with hidden = 1 must not be listed")


    def test_search_returns_matching_session_with_snippet(self) -> None:
        """--include-search runs an FTS5 query and returns sessions, not messages.

        The panel wants the conversation to reopen, not the individual rows, so
        results are deduplicated per session with a preview snippet.
        """
        self.write_config("model: glm-5.3-flash\n")
        snap = run_collect(self.home, self.state, "--include-search", "comma")
        results = snap["search"]["results"]
        self.assertEqual(len(results), 1)
        hit = results[0]
        self.assertEqual(hit["id"], "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        self.assertEqual(hit["title"], "Fix the parser bug")
        self.assertIn("comma", hit["preview"].lower())

    def test_search_absent_without_the_flag(self) -> None:
        """The fast path must not carry a search section at all.

        Search is opt-in so the 5s fast snapshot stays the cheap local read it
        was designed to be.
        """
        self.write_config("model: glm-5.3-flash\n")
        snap = run_collect(self.home, self.state)
        self.assertEqual(snap["search"]["query"], "")
        self.assertEqual(snap["search"]["results"], [])

    def test_search_empty_query_returns_nothing(self) -> None:
        self.write_config("model: glm-5.3-flash\n")
        snap = run_collect(self.home, self.state, "--include-search", "   ")
        self.assertEqual(snap["search"]["results"], [])

    def test_search_syntax_error_fails_soft(self) -> None:
        """A malformed FTS5 query must degrade, not abort the snapshot.

        `foo"bar` is an unterminated string to FTS5. A user typing a stray
        quote into the search box must not blank the whole panel.
        """
        self.write_config("model: glm-5.3-flash\n")
        snap = run_collect(self.home, self.state, "--include-search", 'foo"bar')
        self.assertEqual(snap["search"]["results"], [])
        self.assertEqual(snap["search"]["query"], 'foo"bar')

    def test_search_query_is_data_not_syntax(self) -> None:
        """FTS5 operators typed by a user must not change the query's shape.

        Tokens are quoted before they reach MATCH, so `AND`, `NOT` and `*`
        are searched for as words instead of being interpreted.
        """
        self.write_config("model: glm-5.3-flash\n")
        snap = run_collect(self.home, self.state, "--include-search", "comma OR patched")
        self.assertEqual(snap["search"]["results"], [])

    def test_search_snippet_uses_column_zero(self) -> None:
        """messages_fts has exactly one column, so snippet() indexes 0.

        Passing any other index is a hard sqlite3 error ("column index out of
        range") that would silently empty every result.
        """
        self.write_config("model: glm-5.3-flash\n")
        snap = run_collect(self.home, self.state, "--include-search", "patched")
        hit = snap["search"]["results"][0]
        self.assertTrue(hit["preview"], "a snippet must be produced, not an empty string")

    def test_search_matches_across_sessions(self) -> None:
        self.write_config("model: glm-5.3-flash\n")
        conn = sqlite3.connect(self.home / "state.db")
        cursor = conn.execute(
            "INSERT INTO messages (session_id, role, timestamp, content) VALUES"
            " ('11111111-2222-3333-4444-555555555555', 'user', ?, ?)",
            (self.now - 100, "comma handling in the gateway too"),
        )
        # Index only the row just added; re-indexing the whole table would
        # collide with the rowids make_db already inserted.
        conn.execute(
            "INSERT INTO messages_fts(rowid, content) VALUES (?, ?)",
            (cursor.lastrowid, "comma handling in the gateway too"),
        )
        conn.commit()
        conn.close()

        snap = run_collect(self.home, self.state, "--include-search", "comma")
        ids = {hit["id"] for hit in snap["search"]["results"]}
        self.assertEqual(ids, {
            "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "11111111-2222-3333-4444-555555555555",
        })

    def test_search_caps_results(self) -> None:
        self.write_config("model: glm-5.3-flash\n")
        snap = run_collect(self.home, self.state, "--include-search", "comma")
        self.assertLessEqual(len(snap["search"]["results"]), 20)


class InventoryTests(unittest.TestCase):
    """`hermes cron` oversight: 0 of 15 competing plugins do this."""

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

    CRON_FIXTURE = HERE / "fixtures" / "hermes-cron-list.txt"

    def _collect_module(self):
        """Import the collector so its parser can be called directly."""
        import importlib.machinery
        import importlib.util

        loader = importlib.machinery.SourceFileLoader("deck_collect", str(COLLECT))
        spec = importlib.util.spec_from_loader("deck_collect", loader)
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
        return module

    def test_inventory_reports_cron_jobs_with_paused_state(self) -> None:
        snap = run_collect(self.home, self.state, "--include-inventory")
        self.assertIn("cron", snap)
        jobs = snap["cron"]
        self.assertEqual(len(jobs), 2, f"expected 2 jobs, got {jobs!r}")
        by_name = {job["name"]: job for job in jobs}
        self.assertIn("nightly-backup", by_name)
        self.assertIn("weekly-digest", by_name)
        self.assertFalse(by_name["nightly-backup"]["paused"])
        self.assertTrue(by_name["weekly-digest"]["paused"])

    def test_banner_text_is_not_reported_as_a_job(self) -> None:
        """The box banner is decoration, not a job.

        Asserts the parsed names are CORRECT, not merely that some strings are
        absent — the earlier version passed on an empty parse, which is exactly
        what the broken parser produced, so it certified the failure.
        """
        snap = run_collect(self.home, self.state, "--include-inventory")
        names = [job["name"] for job in snap["cron"]]
        self.assertEqual(names, ["nightly-backup", "weekly-digest"],
                         f"banner text or a label leaked into the names: {names!r}")

    def test_fast_path_carries_cached_inventory_without_a_subprocess(self) -> None:
        """The invariant is 'spawns nothing', not 'omits the key'.

        INVARIANT 2: `deck-collect` without --include-inventory spawns no
        `hermes` process. The key is still present — filled from the on-disk
        cache — because the fast tick runs every 5 s and inventory refreshes
        every 60 s: an empty list on the fast tick erased the panel's Cron
        section between refreshes and made it flicker.
        """
        slow = run_collect(self.home, self.state, "--include-inventory")
        self.assertEqual(len(slow["cron"]), 2)

        fast = run_collect(self.home, self.state)  # no inventory flag
        self.assertIn("cron", fast)
        self.assertEqual([j["name"] for j in fast["cron"]],
                         [j["name"] for j in slow["cron"]],
                         "the fast tick lost the inventory the slow tick found")

    def test_fast_path_resolves_no_hermes_binary(self) -> None:
        """Prove it by observation: a recording stub sees no call."""
        stub_dir = Path(self.tmp.name) / "bin"
        stub_dir.mkdir()
        record = Path(self.tmp.name) / "calls.log"
        stub = stub_dir / "hermes"
        stub.write_text(f'#!/usr/bin/env bash\necho "$@" >> {record}\nexit 0\n')
        stub.chmod(0o755)

        run_collect(self.home, self.state, extra_env={"PATH": str(stub_dir)})
        calls = record.read_text().strip() if record.exists() else ""
        self.assertEqual(calls, "", f"the fast path spawned: {calls!r}")

    def test_inventory_survives_a_missing_cli(self) -> None:
        """A broken `hermes` binary must degrade, not crash the panel."""
        snap = run_collect(
            self.home, self.state, "--include-inventory",
            extra_env={"PATH": "/nonexistent"},
        )
        self.assertIn("cron", snap)
        self.assertEqual(snap["cron"], [])

    def test_cron_parser_reads_the_real_output_format(self) -> None:
        """Parse the bytes `hermes cron list` really prints.

        The fixture is NOT hand-written: it is the output of the CLI's own
        `cron_list` / `_job_rows` / `_print_banner` functions from
        hermes_cli/cron.py, rendered with synthetic in-memory job dicts. Two
        earlier versions of this parser were written against a shape that did not
        exist — first an invented table, then the *slash-command* formatter in
        cli_commands_mixin — and both times a hand-built fixture agreed with the
        parser instead of with the CLI, so the tests passed while every user with
        jobs saw an empty section.
        """
        parse = self._collect_module().parse_cron_list
        jobs = parse(self.CRON_FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual([j["name"] for j in jobs], ["nightly-backup", "weekly-digest"],
                         f"wrong names parsed: {jobs!r}")
        self.assertEqual(jobs[0]["schedule"], "0 3 * * *")
        self.assertEqual(jobs[1]["schedule"], "0 9 * * 1")
        self.assertFalse(jobs[0]["paused"], "an [active] job reported as paused")
        self.assertTrue(jobs[1]["paused"], "the [paused] badge was not read")

    def test_cron_parser_ignores_the_banner(self) -> None:
        """The box-drawing banner is not a job."""
        parse = self._collect_module().parse_cron_list
        for job in parse(self.CRON_FIXTURE.read_text(encoding="utf-8")):
            self.assertNotIn("─", job["name"])

    def test_cron_badge_vocabulary_is_the_real_one(self) -> None:
        """Badges are `[active]`/`[paused]`/`[completed]`/`[disabled]`.

        `effective_job_state` returns `scheduled` for an enabled job (not
        `active`), and `cron_list` maps that to the `[active]` badge. An earlier
        version tested for the literal string "active" in the state field, which
        is a vocabulary the source never produces there.
        """
        parse = self._collect_module().parse_cron_list
        for badge, paused in (("[active]", False), ("[paused]", True),
                              ("[completed]", True), ("[disabled]", True)):
            with self.subTest(badge):
                source = f"  abc123 {badge}\n    Name:      job\n    Schedule:  0 1 * * *\n"
                jobs = parse(source)
                self.assertEqual(len(jobs), 1, f"job not parsed for {badge}: {jobs!r}")
                self.assertEqual(jobs[0]["paused"], paused,
                                 f"{badge} mis-derived as paused={jobs[0]['paused']}")
                self.assertEqual(jobs[0]["name"], "job")

    def test_collector_passes_all_so_paused_jobs_are_visible(self) -> None:
        """`--all` is required: bare `cron list` hides disabled jobs entirely.

        A paused job is stored as enabled=False, and list_jobs() filters those
        out by default (cron/jobs.py), so without --all the paused marker this
        section exists to show can never appear. Verified by rendering the real
        cron_list() both ways.
        """
        stub = STUB_BIN / "hermes"
        record = Path(self.tmp.name) / "cron-argv.log"
        wrapper_dir = Path(self.tmp.name) / "rec"
        wrapper_dir.mkdir()
        wrapper = wrapper_dir / "hermes"
        wrapper.write_text(
            f'#!/usr/bin/env bash\nif [[ ${{1:-}} == cron ]]; then echo "$@" >> {record}; fi\n'
            f'exec "{stub}" "$@"\n'
        )
        wrapper.chmod(0o755)
        run_collect(self.home, self.state, "--include-inventory",
                    extra_env={"PATH": f"{wrapper_dir}:{os.environ['PATH']}"})
        calls = record.read_text().strip() if record.exists() else ""
        self.assertIn("cron list --all", calls,
                      f"the collector did not pass --all: {calls!r}")

    def test_job_named_as_a_bracketed_token_survives(self) -> None:
        """A `Name:` row must never be read as a job header.

        With a bare `\S+` id pattern the header regex also matched
        `    Name:      [urgent]`, which discarded that job and blanked the next
        one's fields. Ids are lowercase hex.
        """
        parse = self._collect_module().parse_cron_list
        source = (
            "  abc123def456 [active]\n"
            "    Name:      [urgent]\n"
            "    Schedule:  0 3 * * *\n"
            "\n"
            "  99887766aabb [active]\n"
            "    Name:      beta\n"
            "    Schedule:  0 4 * * *\n"
        )
        jobs = parse(source)
        self.assertEqual([j["name"] for j in jobs], ["[urgent]", "beta"],
                         f"a bracketed job name broke the parse: {jobs!r}")
        self.assertEqual(jobs[1]["schedule"], "0 4 * * *",
                         "the following job's fields were discarded")

    def test_round_trip_against_the_generated_fixture(self) -> None:
        """Parse the generated fixture and check every field is consistent.

        This is the general guard for the class of bug that recurred three times:
        it asserts on the whole parsed set, not on a hand-picked detail, so a
        parser that only half-reads the format fails.
        """
        parse = self._collect_module().parse_cron_list
        jobs = parse(self.CRON_FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(jobs, [
            {"name": "nightly-backup", "schedule": "0 3 * * *", "paused": False},
            {"name": "weekly-digest", "schedule": "0 9 * * 1", "paused": True},
        ])

    def test_generated_fixture_is_not_stale(self) -> None:
        """The fixture must still match what the CLI's formatter produces.

        Skipped when the Hermes source tree is absent (as in CI): the fixture is
        committed for that reason, and this check only has meaning on a machine
        that can re-render it.
        """
        hermes_src = Path.home() / ".hermes" / "hermes-agent"
        if not (hermes_src / "hermes_cli" / "cron.py").is_file():
            self.skipTest("no Hermes source tree to re-render from")
        result = subprocess.run(
            [sys.executable, str(HERE / "fixtures" / "generate-cron-fixture.py"), "--check"],
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0,
                         f"the cron fixture is stale: {result.stdout}{result.stderr}")

    def test_no_field_label_ever_becomes_a_job_name(self) -> None:
        """A guard for the bug class: a label must never be read as a value."""
        parse = self._collect_module().parse_cron_list
        labels = {"Schedule", "State", "Next", "Repeat", "Deliver", "Prompt",
                  "Name", "Last", "Skills", "Script", "Monitor", "Changed",
                  "Mode", "Workdir", "Dispatch", "Execution", "ID"}
        for source in (self.CRON_FIXTURE.read_text(encoding="utf-8"),
                       "  abc123 [active]\n    Name:  real-job\n    Schedule:  0 3 * * *\n"):
            for job in parse(source):
                self.assertNotIn(job["name"].rstrip(":"), labels,
                                 f"a field label leaked into the job name: {job!r}")

    def test_cron_parser_survives_hostile_input(self) -> None:
        """No input may raise: a raise here blanks the whole panel."""
        import importlib.machinery
        import importlib.util

        loader = importlib.machinery.SourceFileLoader("deck_collect", str(COLLECT))
        spec = importlib.util.spec_from_loader("deck_collect", loader)
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)

        for junk in (None, 12345, [], {}, b"bytes", "", "   \n\n  ", "!!!", "Name:"):
            with self.subTest(junk=repr(junk)[:30]):
                self.assertIsInstance(module.parse_cron_list(junk), list)


if __name__ == "__main__":
    unittest.main(verbosity=2)