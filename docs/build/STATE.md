# Foldweave build state

Observed: **Sunday 19 July 2026 at 16:53:02 CEST** using
`oslo_tz = ZoneInfo("Europe/Oslo")`.

Phase: **WAITING_FOR_FOLDWEAVE_GOAL_ACTIVATION**

Submission hold: **ACTIVE**

Blocker: **NONE**

## Activation and repository

| Field | Observed state |
|---|---|
| Historical H+0 | Friday 17 July 2026 at 17:16:25 CEST — `PRESERVED` |
| Historical R+0 | Saturday 18 July 2026 at 00:51:51 CEST — `PRESERVED` |
| Historical A+0 | Saturday 18 July 2026 at 15:37:55 CEST — `PRESERVED` |
| Historical C+0 | Saturday 18 July 2026 at 23:31:39 CEST — `PRESERVED` |
| Preceding Connected Change goal | `COMPLETED THROUGH C7; SUPERSEDED FOR FUTURE EXECUTION` |
| Amended Foldweave goal | `INACTIVE` |
| Foldweave F+0 | `NOT_STARTED` |
| Current branch | `revision/foldweave-native-review` |
| Exact predecessor | `1023999f2acc7b806775b407dc01a15af3447e90` |
| Governance commit locator | Parent `1023999f2acc7b806775b407dc01a15af3447e90`; subject `docs: establish Foldweave native-review scaffold`; exact SHA belongs in the handoff |
| `main` | `1023999f2acc7b806775b407dc01a15af3447e90` — unchanged |
| `origin/main` | `1023999f2acc7b806775b407dc01a15af3447e90` — unchanged |
| Previous local revision | `revision/ai-first-folder-refactor` at `1023999f2acc7b806775b407dc01a15af3447e90` — unchanged |
| Previous remote revision | `origin/revision/ai-first-folder-refactor` at `1023999f2acc7b806775b407dc01a15af3447e90` — unchanged |
| Historical portable branch | local and remote `revision/portable-change-receipt` at `4baec1ed7b8553775527e3be506edab584b2b8b3` — unchanged |

The exact governance commit SHA, clean post-commit state, and remote Foldweave
branch SHA cannot be asserted by the file contained in that commit. They must be
reported from fresh post-commit evidence in the scaffold handoff.

## Remaining fixed windows at the observed time

| Boundary | Absolute Oslo time | Remaining |
|---|---|---:|
| Feature freeze | Tuesday 21 July 2026 at 01:00 CEST | 1 day, 8 hours, 6 minutes, 58 seconds |
| Release candidate | Tuesday 21 July 2026 at 06:00 CEST | 1 day, 13 hours, 6 minutes, 58 seconds |
| Recording readiness | Tuesday 21 July 2026 at 10:00 CEST | 1 day, 17 hours, 6 minutes, 58 seconds |
| Submission | Wednesday 22 July 2026 at 02:00 CEST | 2 days, 9 hours, 6 minutes, 58 seconds |

These windows continue to elapse. F+0 will record activation and calculate
scaled targets; it will not reset the 44-hour envelope.

## Observed implementation status

| Surface | Status |
|---|---|
| A1–A3 and C0–C7 inherited foundation | `VERIFIED COMPLETE` |
| Foldweave branding | `NOT STARTED` |
| Job v3 and immutable preview | `NOT STARTED` |
| Review, exact acceptance, and bounded revision | `NOT STARTED` |
| Change File v2 and receipt/verifier v3 | `NOT STARTED` |
| Serial derivative collaboration | `NOT STARTED` |
| Native Foldweave app | `NOT STARTED` |
| Keychain settings | `NOT STARTED` |
| New direct GPT planner evidence | `NOT STARTED` |
| ChatGPT developer integration | `NOT STARTED` |
| Consumer gateway and companion | `NOT STARTED` |
| ChatGPT distribution states | `NOT STARTED` |
| Reviewed MCP and Codex update | `NOT STARTED` |
| Budget migration | `NOT STARTED` |
| Feature freeze | `PENDING AFTER F+0` |
| Foldweave release materials | `STALE FOR THE FOLDWEAVE RELEASE — PRESERVED VERIFIED NAME ATLAS PREDECESSOR MATERIAL; MUST BE REGENERATED AFTER FOLDWEAVE FEATURE FREEZE` |
| Devpost submission | `NOT PERFORMED` |

## Current environment and budget facts

- The process environment contains no `OPENAI_API_KEY`; no value was read or
  exposed.
- Ignored `.env.local` exists with owner-only mode `0600`; its contents were not
  read.
- The sole ledger remains `.name-atlas/api_budget.json`, schema
  `gpt-budget.v1`, model `gpt-5.6`, SHA-256
  `c76f578db7d571b8297b9ba48467b8680e5759979370a81c978b0d72d31edecb`.
- The ledger remains unchanged at USD 10 monetary authority, call cap 13, 9
  requests reserved, 9 provider attempts reserved, USD 9.736060 conservative
  committed exposure, and USD 0.605515 reported estimated cost.
- Cloudflare CLI, verified account credentials, deployment, gateway URL, and
  pairing evidence are absent.
- No Apple Developer ID code-signing identity is installed.
- The macOS ChatGPT application and the current Codex desktop environment are
  present. A standalone `/Applications/Codex.app` is absent, and the discovered
  legacy `codex` shell executable fails to start; this is an implementation-time
  Codex installation/qualification risk, not a scaffold blocker.
- The installed personal plugin is still branded Name Atlas.
- There is no observed active Name Atlas/Foldweave server process.

## Latest verified commands

- `uv lock --check` — passed; 48 packages resolved.
- `PYTHONDONTWRITEBYTECODE=1 uv run --no-sync pytest -p no:cacheprovider` —
  passed; 822 tests in 56.64 seconds.
- `uv run --no-sync ruff check .` — passed.
- `uv run --no-sync ruff format --check .` — passed; 154 files already
  formatted.
- `git diff --check` — passed before editing.
- `git diff --cached --check` — passed before editing.

## Exact next operation

`User explicitly activates the complete amended docs/build/GOAL.md in this primary Codex task at the governance commit reported in the scaffold handoff.`

## Compact recovery capsule

- **Phase:** `WAITING_FOR_FOLDWEAVE_GOAL_ACTIVATION`
- **Branch / predecessor:** `revision/foldweave-native-review` /
  `1023999f2acc7b806775b407dc01a15af3447e90`
- **Current F milestone:** none; F+0 is `NOT_STARTED`
- **Latest verified commands:** lock passed; 822 tests passed; Ruff lint and
  format passed; pre-edit diff checks passed
- **Job / preview:** `NOT STARTED`
- **Change File / receipt / verifier / reconstruction:** predecessor evidence
  complete; Foldweave v2/v3 work `NOT STARTED`
- **Native / browser:** predecessor browser complete; Foldweave native work
  `NOT STARTED`
- **Direct / live / replay:** predecessor direct/replay evidence complete; new
  Foldweave qualification `NOT STARTED`
- **ChatGPT / gateway / companion:** `NOT STARTED`
- **MCP / Codex:** predecessor installed plugin complete; Foldweave update
  `NOT STARTED`
- **Budget:** unchanged sole USD 10 ledger; USD 40 migration `NOT STARTED`
- **Feature freeze:** pending; absolute boundary Tuesday 21 July 2026 at 01:00
  CEST
- **Release materials:** stale for Foldweave; predecessor materials preserved
- **Submission hold:** `ACTIVE`
- **Blockers:** none
- **Next operation:** explicit user activation of the complete amended goal at
  the handoff-reported governance commit
