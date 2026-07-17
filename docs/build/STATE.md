# Reversible Name Atlas — Current Build State

Checkpoint: **17 July 2026 at 17:31:24 CEST**

Phase: **M1 — WALKING SKELETON**

Production goal: **ACTIVE**

H+0: **Friday 17 July 2026 at 17:16:25 CEST**

## Schedule at this checkpoint

- Recording-ready boundary: Tuesday 21 July 2026 at 02:00 CEST
- Submission boundary: Wednesday 22 July 2026 at 02:00 CEST
- Product window at H+0: 80.726 hours
- Total time to submission at H+0: 104.726 hours
- Protected submission reserve: 24 hours
- Full 80-hour activation cutoff: Friday 17 July 2026 at 18:00 CEST
- H+80 target: Tuesday 21 July 2026 at 01:16:25 CEST
- Margin from H+80 to recording boundary: 43 minutes 35 seconds
- Compression: not required; ordinary H+ targets apply
- M1 target: Saturday 18 July 2026 at 05:16:25 CEST

## Verified state

- Repository: `/Users/nikolai/Desktop/Repos/reversible-name-atlas`
- Branch: `main`
- Scaffold baseline locator: unique root commit on `main` with subject
  `chore: establish build operating scaffold`
- Exact baseline commit: `f1c519d215790d9e9949c5991c96826e5a2e295b`
- M0 product-foundation commit:
  `c177663c59efb22fb85f18d021f850fe396b08b6`
- Activation-time working tree: clean before this checkpoint update
- Activated objective: 183 lines, 8,859 bytes, SHA-256
  `ae72b0a75129754f579c0d817d779feab1238dd986b20193dedc259b851fac98`
- Controlling attachment: 1,944 lines, 108,954 bytes, SHA-256
  `1e0cc189e75a95d7f5e504799b4bbc14cc03696880ceb96db3201decc518f8b9`,
  read through EOF
- Official rules, FAQ, and GPT-5.6 Sol model page: rechecked 17 July 2026
- Python: 3.11.9; `uv`: 0.9.28; Git: 2.52.0; GitHub CLI: 2.86.0
- Git identity and GitHub CLI authentication: configured
- Free local disk observed before scaffold creation: approximately 93 GiB
- Spike `SHA256SUMS`: 22/22 passed; selective provenance recorded
- `OPENAI_API_KEY`: **NOT_CONFIGURED**
- M0 dependency lock: 31 packages; Python 3.11.9; OpenAI SDK 2.46.0 with
  `responses.parse` available
- M0 automation: 8 pytest tests passed; Ruff lint and format checks passed;
  `uv sync --frozen` passed
- M0 runtime: replay shell returned a safe health response and listened only on
  `127.0.0.1:8000`; live mode without a credential exited 2 before server start
- M0 visual evidence: full-page screenshot and DOM inspected in the in-app
  browser; mode/model/network status were legible and no console logs existed at
  inspection
- The M0 server was shut down cleanly after visual verification

No API response, product fixture, staged package, public repository, video, or
Devpost artifact exists at this checkpoint. No API funds have been spent.

## Current readiness

- Scaffold implementation: **COMPLETE**
- Production goal: **ACTIVE**
- Product implementation: **M0 COMPLETE; M1 IN PROGRESS**
- Live GPT-5.6 run readiness: **BLOCKED** — local credential not configured and
  no live call has been made

The missing credential does not block deterministic M1 work. It blocks M1
completion and any claim that live GPT-5.6 integration is verified.

## Compact recovery capsule

- Current phase: M1 walking skeleton
- Unresolved product decisions: none
- Evidence: clean scaffold baseline plus verified M0 commit; goal objective read
  through EOF; H+0 and schedule derived from active-goal state
- Budget: USD 0 spent; USD 10 maximum only after activation
- Prohibitions: no discovery/tournament/harness loop; no secrets; no unsupported
  claims; no publication outside the authorized Build Week surfaces
- Next operation: **Implement and verify the deterministic minimum hero-family
  transaction from source snapshot through copy-only staged proof.**
