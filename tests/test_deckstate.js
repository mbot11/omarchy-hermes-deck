#!/usr/bin/env node
// Behavioural tests for js/DeckState.js.
//
// DeckState is a .pragma library of pure helpers, so it is testable without a
// QML engine: load the file into a vm context and call the functions directly.
// This is the only automated coverage the JS layer has — without it a
// mis-shaped snapshot reaches the panel and silently renders blank.

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const SOURCE = path.join(__dirname, "..", "js", "DeckState.js");

function load() {
  // `.pragma library` is QML-only syntax that a JS engine cannot parse, so it
  // is stripped before evaluation. Nothing else about the file is altered:
  // the helpers under test run exactly as the panel runs them.
  const code = fs.readFileSync(SOURCE, "utf8").replace(/^\s*\.pragma\s+library\s*$/m, "");
  // The context's own globalThis must not be shadowed, or the appended export
  // line writes to a null.
  const context = {};
  vm.createContext(context);
  vm.runInContext(code + "\n;globalThis.__deck = { emptyState, accept, cronSummary };",
    context);
  return context.__deck;
}

const deck = load();
let failures = 0;

function check(label, actual, expected) {
  const ok = JSON.stringify(actual) === JSON.stringify(expected);
  if (ok) {
    console.log(`  ok       ${label}`);
  } else {
    failures++;
    console.log(`  FAIL     ${label}\n             expected ${JSON.stringify(expected)}` +
      `\n             actual   ${JSON.stringify(actual)}`);
  }
}

// ── emptyState: the shape the panel reads before the first snapshot ─────────
console.log("emptyState");
const empty = deck.emptyState();
check("declares cron as an empty list", Array.isArray(empty.cron) && empty.cron.length === 0,
  true);

// ── accept: a snapshot carrying inventory ──────────────────────────────────
console.log("accept");
const snapshot = JSON.stringify({
  schemaVersion: 2,
  id: "hermes-deck",
  cron: [
    { name: "nightly-backup", schedule: "0 3 * * *", paused: false, running: false },
    { name: "weekly-digest", schedule: "0 9 * * 1", paused: true, running: false },
  ],
});
const state = deck.accept(snapshot);
check("carries cron through", state.cron.length, 2);

const legacy = deck.accept(JSON.stringify({ schemaVersion: 2, id: "hermes-deck" }));
check("defaults cron when the collector omits it", legacy.cron, []);

const hostile = deck.accept(JSON.stringify({
  schemaVersion: 2, id: "hermes-deck", cron: "not a list",
}));
check("refuses a non-list cron", hostile.cron, []);

const junk = deck.accept(JSON.stringify({
  schemaVersion: 2,
  id: "hermes-deck",
  cron: [
    { name: "good", schedule: "* * * * *", paused: true },
    null,
    { schedule: "* * * * *" },
  ],
}));
check("drops malformed entries rather than the whole list", junk.cron.length, 1);

// ── cronSummary: what the panel header renders ─────────────────────────────
console.log("cronSummary");
check("counts total and paused",
  deck.cronSummary([
    { name: "a", paused: true }, { name: "b", paused: false },
  ]),
  { total: 2, paused: 1 });

check("empty list", deck.cronSummary([]), { total: 0, paused: 0 });
check("tolerates a non-list", deck.cronSummary(undefined), { total: 0, paused: 0 });
check("tolerates junk entries", deck.cronSummary([null, { paused: true }]),
  { total: 1, paused: 1 });

console.log(`\n${failures === 0 ? "PASS" : "FAILED"} (${failures} failure(s))`);
process.exit(failures === 0 ? 0 : 1);
