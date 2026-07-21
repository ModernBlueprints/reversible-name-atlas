# Foldweave build state

Observed: **Tuesday 21 July 2026 at 13:10:18 CEST** using
`oslo_tz = ZoneInfo("Europe/Oslo")`.

Phase: **RECORDING_READY — PUBLIC-`main` RELEASE AND CAPTURE PACKAGE COMPLETE**

Submission hold: **ACTIVE**

Global blocker: **NONE**. `RECORDING_READY` means the accepted Foldweave
product and capture package are ready for the user's recording work. It does not
mean that a user voice-over, public YouTube video, `/feedback` Session ID,
personal/legal attestation, release of the submission hold, or Devpost
submission has occurred.

## History and repository

| Field | Observed state |
|---|---|
| Historical H+0 / R+0 / A+0 / C+0 | `PRESERVED` — exact timestamps remain in the sole plan |
| Foldweave F+0 | Sunday 19 July 2026 at 17:18:14 CEST — `ACTIVE` |
| Current branch | `revision/foldweave-native-review` |
| Accepted product candidate | `4e9ec44b02b25f515017ceb9922fff4fdf84ae46` |
| Public judge-documentation checkpoint | `d5702e1665c1ddb2b88ac68d902e9d6bc63304fd` — fast-forwarded without force to public `main` and the revision branch |
| Recording-readiness locator | subject `docs: record Foldweave recording readiness`, parent `d5702e1665c1ddb2b88ac68d902e9d6bc63304fd`; the resulting SHA belongs in the handoff, not this file |
| Previous revision / portable branch | `1023999f2acc7b806775b407dc01a15af3447e90` and `revision/portable-change-receipt` at `4baec1ed7b8553775527e3be506edab584b2b8b3` — preserved |

## Remaining fixed window

| Boundary | Absolute Oslo time | State at observation |
|---|---|---:|
| Feature freeze | Tuesday 21 July 2026 at 01:00 CEST | passed by 12 hours, 10 minutes, 18 seconds |
| Release candidate | Tuesday 21 July 2026 at 06:00 CEST | passed by 7 hours, 10 minutes, 18 seconds |
| Recording readiness | Tuesday 21 July 2026 at 10:00 CEST | passed by 3 hours, 10 minutes, 18 seconds |
| Submission | Wednesday 22 July 2026 at 02:00 CEST | 12 hours, 49 minutes, 41 seconds remained |

F+0 did not reset the 44-hour envelope. Only release-safe work is now
permitted.

## Current verified status

| Surface | Status |
|---|---|
| A1–A3 and C0–C7 foundation; F0a–F0d and F1–F7 | `VERIFIED COMPLETE — RECORDING_READY` |
| Product and proof | review-before-execution, job v3, exact acceptance, bounded revision, Change File v2, receipt/verifier v3, lineage, convergence, and selected-source reconstruction `VERIFIED COMPLETE` |
| Native macOS app | `VERIFIED COMPLETE` — unsigned/ad-hoc Apple-Silicon judge build; no Developer ID or notarization claim |
| Direct GPT-5.6 | `VERIFIED COMPLETE` — live root and derivative evidence; sole USD 40 ledger preserved |
| ChatGPT | `DEVELOPER_MODE_VERIFIED`; `CONSUMER_PAIRING_VERIFIED`; technical `PUBLICATION_READY`; not submitted for review, approved, published, or publicly listed |
| Replay, unchanged application, MCP, and Codex | `VERIFIED COMPLETE` — model-free replay, `capsule_applied` receiver path, shared bounded MCP, installed-copy qualification, and clean-clone plugin/MCP validation |
| Gateway and companion | `VERIFIED COMPLETE` — deployed `workers.dev` gateway, OAuth/pairing, outbound companion, reconnect, and refusal matrix |
| Regression and source checks | 1,176 Python tests, 80 frontend tests, 50 gateway tests; lock, lint, format, packaging, visual, and claim scans pass as recorded in the plan and build log |
| Public clone judge path | `VERIFIED COMPLETE` — public `main` clone installed with `uv sync --frozen`; keyless replay reached review, served `/review`, accepted the exact preview, verified, and reconstructed source paths and bytes exactly |
| Release visuals and recording package | `VERIFIED COMPLETE` — ten genuine screenshots, current thumbnail, 317-word narration, 11 shots, 2:55 target, and speech-timing margin; this is not a user voice or video |
| Devpost project preparation | `COMPLETE WITHOUT SUBMISSION` — project record `1327974` has Foldweave project copy and thumbnail; category, user fields, video, and submission remain pending |
| Devpost submission | `NOT PERFORMED` |

## Latest verified commands

- `PYTHONDONTWRITEBYTECODE=1 uv run --no-sync pytest -p no:cacheprovider`:
  **1,176 passed**, with one upstream deprecation warning.
- Fresh public `main` clone: `uv sync --frozen`, then
  `uv run foldweave demo --mode replay --root "$DEMO_ROOT"`: passed; the
  24-file immutable review needed no API key or provider call.
- Fresh public clone: `uv run foldweave app --browser --mode development --job "$JOB"`:
  loopback `/review`, preview, and status endpoints served and shut down cleanly.
- Fresh public clone: exact CLI acceptance, `verify-receipt`,
  `restore-receipt`, and `diff -qr`: passed; the selected source was
  reconstructed exactly.

## User-owned work still pending

1. Record the English narration in `docs/SUBMISSION_PACKAGE.md` against the
   frozen 11-shot 2:55 plan.
2. Export a public YouTube video strictly below three minutes; watch its complete
   public playback with audio and provide the URL.
3. Run `/feedback` in this primary Codex task and provide the exact Session ID.
4. Complete submitter type, country, eligibility, ownership, representative, and
   other personal/legal Devpost entries.
5. Reread the final Devpost form and explicitly release the submission hold only
   after every prerequisite is complete.

## Exact next operation

`User records and publicly uploads the strictly-under-three-minute English video
using docs/SUBMISSION_PACKAGE.md, runs /feedback in this primary Codex task,
supplies the resulting Session ID and YouTube URL, completes personal form and
attestation fields, and later explicitly releases the submission hold. Do not
submit before that explicit release.`

## Compact recovery capsule

- **Phase:** `RECORDING_READY`.
- **Branch / checkpoint:** `revision/foldweave-native-review`; product
  `4e9ec44b02b25f515017ceb9922fff4fdf84ae46`; final recording-readiness SHA
  is reported in the handoff.
- **Evidence:** job/preview, Change File/receipt/verifier/reconstruction,
  native/browser, direct/hosted/replay, gateway, MCP/Codex, and screenshots are
  verified; detailed fingerprints remain in the sole plan and build log.
- **Budget:** sole USD 40 ledger; no further direct call is needed for the
  recording package.
- **Release materials:** Foldweave-current and public; the tournament and
  predecessor-fixture provenance are explicitly disclosed.
- **Submission hold:** `ACTIVE`.
- **Blockers:** none globally; remaining actions are deliberately user-owned.
