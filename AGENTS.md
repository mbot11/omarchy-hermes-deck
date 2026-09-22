# AGENTS.md

Instructions for AI coding agents working in this repository.

**Read [CONSTRAINTS.md](CONSTRAINTS.md) before writing code. Do not weaken it
to make a change pass.**

## What this is

An Omarchy Quattro shell plugin (`service` + `bar-widget`) that surfaces Hermes
Agent state: a bar glyph, a panel with status/sessions/usage/kanban/controls,
and a drop-down TUI modal.

## Layout

| Path | Role |
|---|---|
| `manifest.json` | Plugin manifest. `barWidget.defaults` keys must exactly equal `barWidget.schema` keys |
| `Service.qml` | Persistent state daemon: collector cadence, action queue, notifications, IPC |
| `BarWidget.qml` | Bar glyph; loads `Panel.qml` |
| `Panel.qml` | The panel UI |
| `Modal.qml` | Drop-down TUI on a Hyprland special workspace |
| `DeckAdapter.qml` | Shell-injection and path isolation |
| `js/DeckState.js` | Pure helpers, no QML imports |
| `scripts/deck-collect` | Python 3, **stdlib only**. Emits one JSON snapshot |
| `scripts/deck-act` | Bash. The only component allowed to change Hermes state |
| `tests/` | See below |

## Non-negotiable invariants

1. **Python stdlib only.** No pip packages, no virtualenv, in the collector or
   the test scripts. The plugin must run on a stock Omarchy install.
2. **No `hermes` CLI on the fast path.** `deck-collect` without `--include-slow`
   must never spawn a `hermes` process — a cold Hermes CLI costs seconds of
   startup. It DOES spawn two `/usr/bin/systemctl --user` calls for the gateway's
   active/enabled state; those are cheap and deliberate. The test asserts the
   exact process set, so adding any third spawn fails the suite rather than
   passing unnoticed.
3. **Fixed-argument allowlist.** `deck-act` takes an action name, never a
   command string. Every dynamic value is regex-validated before use.
4. **No credential access.** Never read, copy, or print `auth.json`, `.env`,
   tokens, or key files. `hermes auth list` is the one nuance: Hermes reads its
   own auth state and the plugin keeps only provider names and counts.
5. **`textFormat: Text.PlainText` on every `Text`/`Label`.** See CONSTRAINTS.md.
6. **Read-only against Hermes' state.** `state.db` is opened `mode=ro`.
7. **Never edit `/usr/share/omarchy/`.** Read it freely; it is overwritten on
   update.
8. **Audit every image before it is committed, pushed, or attached anywhere.**
   A screenshot is the one artifact no text scanner can read, and a capture of a
   live desktop carries working directories, session titles, hostnames and
   possibly keys. Run `python3 tests/check-image-safety.py <image>` first — and
   use the **full-resolution capture**, because OCR misreads small text and a
   clean verdict on a downscaled image is weak evidence. Never publish a
   screenshot that has not passed this gate, including in a review thread or a
   chat.

## Running the gates

```bash
bash tests/run.sh          # everything: syntax, both suites, QML floor,
                           # tree hygiene, the official validator, qmllint
omarchy plugin validate .  # silent on success; test $?
```

Individual gates:

```bash
python3 tests/test_collect.py
python3 tests/test_act.py
python3 tests/check-qml-plaintext.py .
bash tests/check-tree.sh .
```

## Working on a change

- Implement on a branch, never on `main`.
- Test-first for collector and `deck-act` behaviour: write the failing test,
  run it, watch it fail for the right reason, then implement.
- Commit in small, verifiable steps.
- If you add a gate, add it to CONSTRAINTS.md, to `tests/run.sh`, and to
  `.github/workflows/ci.yml` — all three, or it is not a gate.

## Live checks (need a real host)

```bash
~/.config/omarchy/plugins/io.github.mbot11.hermes-deck/scripts/deck-collect | jq .
omarchy-shell shell rescanPlugins && omarchy restart shell
journalctl --user -b | grep omarchy-shell   # look for load errors
```

## Submission

The marketplace binds approval to one exact commit. Do not push incrementally
to `main` while a review is open — see the Submission discipline section of
CONSTRAINTS.md.
