# Constraints

Last reviewed: 2026-09-22

The bar for this repository, with numbers, so it can be checked mechanically
instead of argued about per-pull-request. Every row names the command that
produces the verdict; a dimension with a number and no command is an
aspiration, not a constraint.

Do not weaken this file to make a change pass. Tightening it should be silent;
loosening it should be loud and discussed.

## Floor (always enforced, no setup required)

- No new suppression comments: `# noqa`, `# type: ignore`, `@ts-ignore`,
  `eslint-disable`, or a `qmllint disable` comment
- No unimplemented stubs: no `throw new Error("Not implemented")`, no empty
  `catch {}` that turns a failure into silence
- No skipped or deleted tests without a reason in the commit message
- No secrets in source
- **No symlinks anywhere in the tree.** A mode-120000 blob is a fatal
  `manifest-invalid` at the marketplace, and their security scanner never
  looks inside `docs/` or `tests/`, so a symlink there is still fatal
- **Every `Text` / `Label` in QML declares `textFormat: Text.PlainText`.**
  Qt's default is `AutoText`, which interprets an HTML subset in whatever it
  renders, and session titles, kanban task titles, model ids, workspace paths
  and subprocess output all arrive from outside this plugin
- This file is not weakened to make a change pass

## Enforced with numbers

| Dimension | Rule | Checked by | Runs at |
|---|---|---|---|
| Secrets / identity | no credential shapes, no author account name, no machine-specific absolute path, no stray artifacts | `python3 tests/check-secrets.py` | every edit, CI |
| Images | no credential, identity or context finding in metadata or pixels, for **every published image** | `python3 tests/check-image-safety.py <image…>` | before any image is committed, CI |
| Collector behaviour | 44/44 pass | `python3 tests/test_collect.py` | every edit |
| Action dispatcher | 25/25 pass | `python3 tests/test_act.py` | every edit |
| Secret + image audit logic | 45/45 pass | `python3 tests/test_image_audit.py` | every edit |
| DeckState JS helpers | 24/24 pass | `node tests/test_deckstate.js` | every edit |
| QML plain-text | zero unmarked `Text`/`Label` | `python3 tests/check-qml-plaintext.py .` | every edit |
| Tree hygiene | no symlinks, one root manifest, root README+LICENSE | `bash tests/check-tree.sh .` | task end |
| Manifest contract | schemaVersion 1, lowercase id, kinds⇔entryPoints, schema keys ⇔ defaults | `bash tests/check-tree.sh .` | task end |
| Scan size | ≤ 512 KiB per file, ≤ 8 MiB total | `bash tests/check-tree.sh .` | task end |
| Syntax | clean | `bash -n scripts/deck-act && python3 -m py_compile scripts/deck-collect` | every edit |
| Plugin validity | exit 0 (silent on success) | `omarchy plugin validate .` | task end, host only |
| Marketplace baseline | `blocksApproval: false` | canonical `security-baseline-scanner.mjs` against the pushed SHA | CI |
| README rules | install + removal + dependencies + license headings, no audit claim, name matches manifest | `bash tests/check-tree.sh .` plus the README greps in `tests/run.sh` | task end |

`tests/run.sh` runs all of the above in one command, in CI's order. **If
`tests/run.sh` and this file ever disagree, this file wins** and the script is
the thing to fix.

### The secret audit reads pushed blobs, not the working copy

`tests/check-secrets.py` reads content from git — `HEAD:<path>`, falling back to
the index for a path not yet committed — because the working copy is not what
publication scans. A file edited after its commit is still listed by
`git ls-files`; scanning the file on disk would report content that was never
pushed. The blob form also means a file that is tracked but deleted from disk is
still scanned, and a file present on disk but untracked is correctly ignored.
Verified in all four directions: an uncommitted secret is ignored, a committed
secret is caught, a staged-but-uncommitted secret is caught, and a
tracked-but-deleted file is still caught.

### An image is audited before it is published, never after

A screenshot is the one artifact in this repository that **no text scanner can
read**. `check-secrets.py` opens text blobs; a PNG is opaque to it. A capture of
a live desktop can carry a session title, a working directory, a hostname, a
terminal scrollback or an API key into a public listing while every text gate
reports clean.

So `tests/check-image-safety.py` audits an image two ways and is a **blocking
gate** on every tracked image:

- **Metadata** — PNG `tEXt`/`zTXt`/`iTXt` chunks, EXIF, and any embedded comment.
  Author names, machine names, tool paths and GPS survive cropping and
  resizing, which is what makes them easy to miss by eye.
- **Pixels** — OCR with tesseract, then the same credential and identity shapes
  `check-secrets.py` applies to text, plus screenshot-specific ones (an absolute
  home path, a shell prompt with a cwd, a git remote URL, a private IP).

A fourth hole was found the same way, by auditing a real capture of this
machine's own desktop: a shell prompt prints `~/Work/...`, not
`/home/<user>/Work`, so the absolute-path pattern missed the most common real
leak of all — a terminal screenshot showing a working directory. The scan now
matches `~/<something>` while deliberately not matching a bare `~` or a `~` used
as an approximation in prose. That capture is the regression case: the audit
reports a finding on it today and reported nothing before.

**Known limitation, stated rather than hidden:** OCR accuracy is a function of
resolution, and it fails in the direction that matters. At 900×300 the engine
reads `/home/<user>/` as `homel<user>` and `sk-or-v1-…` as `skor 1`, so the
patterns miss and the audit returns CLEAN on an image that plainly leaks. The
script therefore prints an explicit warning below 1000px saying a clean verdict
is weak evidence. **Audit the full-resolution capture, not a downscaled copy** —
and note that the published `preview.png` is 450×360, so its clean verdict is
itself weak.

Consequences for this repository: a screenshot may not be committed, pushed, or
attached to a review thread until it has passed this gate, and a passing verdict
is a filter, not a proof — anything not generated here still needs a human look.

## Measured, not yet enforced (ratchets)

These record where the code stands today and refuse to get worse. They are not
targets: a target of zero is unreachable here, which is exactly why the number
is recorded rather than aspired to.

| Metric | Today | Direction | Checked by |
|---|---|---|---|
| `qmllint` warnings, total | 178 | must not grow | `tests/run.sh` |
| `qmllint` warnings, Panel.qml | 158 | must not grow | `tests/run.sh` |
| `qmllint` warnings, BarWidget.qml | 14 | must not grow | `tests/run.sh` |
| Tracked tree size | ~265 KiB | must not grow past 8 MiB | `tests/check-tree.sh` |

### Why the qmllint number is not a gate at zero

Two thirds of it is not our code's fault and cannot be fixed here:

- **~90 are `missing-property`** against the `qs.Ui` / `qs.Commons` singletons.
  `Style.font` and `Style.spacing` are declared `readonly property QtObject`, so
  qmllint cannot see `.caption` or `.body`. Nothing in this repository can retype
  another package's singletons.
- **~37 are `unqualified`** on `parent.children[N]` and untyped `modelData` —
  the same access pattern Omarchy's own `omarchy.agents/Panel.qml` produces.
- **4 are `signal-handler-parameters`** on `onExited: function(exitCode)`.
  This is a **false positive**: Omarchy's own `agents/Main.qml:318,332` use the
  identical signature.
- **1 is `property-override`** on `Service.qml`'s `property var state`.
  `QQuickItem` does technically own `state`, so the warning is literally true,
  and Omarchy's own `bar/indicators/Dictation.qml:8` declares
  `property string state` the same way. Renaming would touch four call sites for
  no behavioural gain.

For calibration: **Omarchy's own first-party `omarchy.agents/Panel.qml` reports
96 warnings** under the identical invocation. A bar set at zero would be red
forever and would teach everyone to ignore it, which is worse than no gate.

## External constraints

Everything in the table above except the size and hygiene checks is judged by
this project's own tests, which prove we agree with ourselves. The outside
opinion is the marketplace's own scanner:

- `omacom/omarchy-plugin-marketplace` → `scripts/security-baseline-scanner.mjs`,
  `security-baseline-limits.mjs`, `build-catalog.mjs`, `submission.mjs`
- Run from a checkout after `npm ci`. These encode rules we did not write, and
  they are what decides a submission.

`tests/run.sh` is a local approximation of those rules; the canonical scripts
are the authority. Where they disagree, the canonical ones win.

## Exceptions

None. Add a row here with a rule, path, reason, owner and expiry date rather
than deleting a constraint.

## Submission discipline

The marketplace binds approval to one exact commit. Its reviewer, on this
repository's own thread (#6178):

> "approval must bind to the exact commit validated and reviewed."

A history rewrite already cost this repository one full review cycle (#4918),
and the same rule stalled `hermes.companion` (#7330). Therefore:

- All work for a release lands as **one reviewed push**, then HEAD freezes until
  approval. Do not push to `main` incrementally during a review.
- Implement on a branch, never on `main`.
- After a review round-trips, re-verify before assuming the tree still matches
  what was reviewed.

## Competitor code is never vendored or copied

Studying other plugins is allowed for their **published spec** (README, changelog,
marketplace listing, issue threads) and their **user-visible behaviour**. No code,
file, or snippet from any other plugin enters this tree, and no implementation is
copied in paraphrase. A citation comment may reference an upstream *bug report*
whose behaviour this code must not reproduce; that is not copying code.

Enforced by: `tests/check-tree.sh` (tree hygiene) plus the review checklist — any
new file that resembles another plugin's implementation is a blocker, not a nit.
