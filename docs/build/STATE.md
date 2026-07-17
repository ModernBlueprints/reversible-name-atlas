# Reversible Name Atlas — Current Build State

Checkpoint: **17 July 2026 at 19:23:31 CEST**

Phase: **M5 — PRODUCT EXPERIENCE**

Production goal: **ACTIVE**

H+0: **Friday 17 July 2026 at 17:16:25 CEST**

## Schedule at this checkpoint

- Recording-ready boundary: Tuesday 21 July 2026 at 02:00 CEST
- Submission boundary: Wednesday 22 July 2026 at 02:00 CEST
- Product window at H+0: 80.726 hours
- Protected submission reserve: 24 hours
- H+80 target: Tuesday 21 July 2026 at 01:16:25 CEST
- Margin from H+80 to recording boundary: 43 minutes 35 seconds
- Compression: not required; ordinary H+ targets apply
- Product time remaining at this checkpoint: 78 hours 36 minutes 29 seconds
- Total time to submission at this checkpoint: 102 hours 36 minutes 29 seconds
- M5 ordinary target: Sunday 19 July 2026 at 01:16:25 CEST

Milestone targets force integration and scope control; they are not cancellation
timers.

## Verified repository state

- Repository: `/Users/nikolai/Desktop/Repos/reversible-name-atlas`
- Branch: `main`
- Scaffold baseline:
  `f1c519d215790d9e9949c5991c96826e5a2e295b`
- Product foundation:
  `c177663c59efb22fb85f18d021f850fe396b08b6`
- Deterministic walking transaction:
  `83d64fe361747faef4e340c76a2958736d754e5a`
- M2–M4 hardening and complete deterministic proof:
  `1cce39d8c46c62eef96b9baa64b83d16765d5c03`
- Working tree was clean immediately after the M2–M4 product commit; this
  checkpoint and plan update are the current documentation-only changes.
- No Git remote or public repository exists yet.

## Verified product evidence

- Hero package: 12 stable object families, 28 content objects, 30 source
  package members, one `campaña` Meaning-risk family, and one casefold collision
  pair.
- M2 strict input matrix: 41 focused package-import scenarios pass, including
  identifier, UTF-8/CSV, supported-tree, reciprocal relationship, traversal,
  symlink, special-file, and source-change behavior.
- M3 deterministic transaction: bounded evidence, complete fingerprints,
  coordinator-side card validation, exact cache/stale-card behavior, explicit
  human authority, identity-level propagation, persistent conservative spend
  reservation, write-once replay capture, retry without a second provider call,
  and replay-startup evidence compatibility are implemented.
- M4 proof: exact data-member accounting; staged content hashes; declared control
  semantics and references; canonical source snapshot and decision ledger;
  strict forward/reverse maps; reverse dry run; profile and collision checks;
  post-BagIt deterministic rerun; final BagIt validation; blocked failure
  reports; and atomic no-replace promotion are implemented.
- Former false-green reproductions for extra staged data, post-proof payload
  mutation, stale proof after failed re-stage, crafted decision authority, long
  metadata evidence, and tampered state artifacts are regression-tested and
  independently confirmed closed.
- Current automation: `uv sync --frozen` passed; 111 pytest tests passed; Ruff
  lint and format checks passed; `git diff --check` passed.
- Browser transaction at 1440×1000: 12/12 families resolved; 28 objects staged;
  all current proof checks plus Library of Congress BagIt validation rendered
  green; no browser console errors were present.
- Source payloads used in browser QA remained unchanged. Ephemeral browser-QA
  stages were outside the repository and are not product or replay evidence.

## Current blocker and readiness

- `OPENAI_API_KEY`: **NOT_CONFIGURED** at this checkpoint.
- API spend: **USD 0 observed**; no OpenAI request has been made.
- Canonical replay record: **ABSENT**.
- M1: deterministic slice complete; required live evidence pending.
- M2: **COMPLETE**.
- M3: deterministic decision transaction complete; live/replay evidence pending.
- M4: **COMPLETE**.
- M5: **IN_PROGRESS**.
- Live GPT-5.6 run readiness: implementation **GO**; execution **BLOCKED** only
  by local credential configuration.

The missing credential blocks the first real GPT-5.6 card, sanitized canonical
record, replay proof, and completion of M1/M3/M5. It does not block current M5
product-experience, fixture, responsive, accessibility, documentation, or
release work. Never request that the key be pasted into chat.

## Compact recovery capsule

- Current phase: M5 product experience; feature freeze has not yet been declared.
- Unresolved product decisions: none.
- Verified baseline: commit `1cce39d`; 111 tests; Ruff/frozen sync/diff checks;
  1440×1000 full deterministic browser transaction.
- External dependency: local `OPENAI_API_KEY` configuration, then one bounded
  baseline `gpt-5.6` call and exact recorded replay verification.
- Budget: USD 0 observed; USD 10 project cap; conservative reservation is
  committed before any live request.
- Prohibitions: no discovery/tournament/harness loop; no secret exposure; no
  unsupported claims; no silent model substitution.
- Next operation: **Complete the concise M5 exception-queue experience and the
  single tiny negative fixture, verify responsive/browser behavior, then declare
  feature freeze while continuing to check locally for live credential access.**
