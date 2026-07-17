# Reversible Name Atlas — Current Build State

Checkpoint: **Friday 17 July 2026 at 21:34:20 CEST**

Phase: **M8 — RECORDING READINESS**

Production goal: **ACTIVE**

H+0: **Friday 17 July 2026 at 17:16:25 CEST**

## Schedule at this checkpoint

- Recording-ready boundary: Tuesday 21 July 2026 at 02:00 CEST
- Submission boundary: Wednesday 22 July 2026 at 02:00 CEST
- Product time remaining: 76 hours 25 minutes 39 seconds
- Total time to submission: 100 hours 25 minutes 39 seconds
- Protected submission reserve: 24 hours
- Compression: not required; ordinary H+ targets remain in force

Targets force integration and scope control. They are not cancellation timers.

## Verified repository state

- Repository: `/Users/nikolai/Desktop/Repos/reversible-name-atlas`
- Branch: `main`
- Scaffold baseline:
  `f1c519d215790d9e9949c5991c96826e5a2e295b`
- Feature-freeze product commit:
  `819e674ba74fb86d981f390d52214de5b4e4f7a7`
- M6 live/replay release commit:
  `d71b0b903a8259b158e1d674c5735edb88a6c665`
- M7 release candidate was reproduced from:
  `b4a2dd0f7c1c0142901ab1218c06925a4e7d95e3`
- M7 completion checkpoint:
  `79e2836019cc392a02ad0cf04971b091a5c8c8d9`
- M8 submission-package commit:
  `2e808ff9cdb98cd6b13cd9cd8bdd4826a7dbe7d9`
- Remote: `https://github.com/ModernBlueprints/reversible-name-atlas.git`
- Public repository: <https://github.com/ModernBlueprints/reversible-name-atlas>
- GitHub reports visibility **PUBLIC**, default branch `main`, MIT license, and
  seven relevant repository topics. The public page returns HTTP 200.
- The working tree was clean immediately before this synchronized M8 plan/state
  checkpoint; these two documentation files are the only current changes.

## Verified release evidence

- M0 through M7: **COMPLETE**. Feature freeze remains active.
- Hero: 12 stable families, 28 content objects, 30 source-package members, one
  Meaning-risk family, and one casefold collision pair.
- One explicit live request used the exact `gpt-5.6` alias and the complete
  visible hero evidence packet. The returned card passed schema, evidence-ID,
  candidate-path, and advisory-authority validation.
- Canonical replay record:
  `src/name_atlas/recordings/hero_decision_card.json`; SHA-256
  `2fe0da43fe57e72043effcf13dc3a3084b8a262295e132b00109bf767f06ae00`;
  evidence fingerprint
  `0f0b0b7cf923432431e7d184c6881cb34d61a0e5caf578f87cc029494b97d830`.
- Provider-reported usage: 1,676 input tokens, 994 output tokens, and 2,670
  total tokens. Application-measured end-to-end latency: 14.645 seconds.
  Application-estimated model cost: USD 0.0382. Conservative committed budget
  reservation: USD 0.6790 of the USD 10 cap.
- The live transaction reached 12/12 explicit human resolutions, including the
  human-entered `campaign-poster` descriptor, then passed copy-only staging,
  source equality, 28 complete forward/reverse map rows, reverse dry run, every
  deterministic proof check, and Library of Congress `bagit` validation.
- Two subsequent complete replay transactions ran with `OPENAI_API_KEY` absent,
  displayed **Recorded GPT-5.6 response**, made no provider request, and reached
  the same verified result. Their staged data trees and deterministic artifacts
  are byte-identical except for the expected run location/time fields and the
  corresponding verification-report tag hash.
- The tiny negative fixture visibly and mechanically blocks staging while its
  Meaning decision is unresolved.
- Current automation: `uv lock --check`; `uv sync --frozen`; 116 pytest tests;
  Ruff lint and format; `git diff --check`; source/wheel build; local Markdown
  link scan; canonical-record validation; and repository secret scan all pass.
- Six 1280×720 product captures were visually inspected. Their captions now
  describe only visible evidence; the Atlas capture exposes no personal path.
- Two bounded M6 audits found no remaining product, record, claim, secret, or
  documented source-checkout judge-path blocker after one correction pass.
- Fresh clone `/private/tmp/name-atlas-m7.jBLU0S/repo` installed from the lock,
  passed 116 keyless tests, Ruff lint/format, source and wheel builds, local-link
  and secret scans, and remained Git-clean.
- Its complete keyless browser transaction resolved 12/12 families, staged 28
  content objects and 30 data members, produced 28 inverse map rows, passed all
  ten serialized proof checks and a fresh `bagit` validation, and left the
  source unchanged. Startup and server logs show replay mode and no provider
  request.
- A separate clean-clone live startup displayed exact `gpt-5.6`, loopback-only
  binding, credential readiness, and the explicit Generate control. It was
  stopped without generating a card or making a provider request.
- An unauthenticated public clone at commit `79e2836` installed from the lock,
  passed 116 tests and Ruff checks, started keyless replay with the exact
  recorded label/model/hero counts, and remained Git-clean.
- Current Devpost state: the user is already registered; submissions are open;
  the separate Reversible Name Atlas project exists as draft
  <https://devpost.com/software/reversible-name-atlas>; its project copy,
  technology list, public repository link, and processed Atlas thumbnail are
  present. The unrelated Preflight draft was not modified.
- `docs/SUBMISSION_PACKAGE.md` contains the official-rule snapshot, bounded
  Devpost copy, 401-word narration, shot list, custom-field/testing draft,
  claim audit, and final checklist. Narration is estimated at 2 minutes 40
  seconds at 150 words per minute before controlled pauses.

## Credential and release readiness

- Both temporary restricted project keys are revoked. The first was revoked
  before any call; the replacement completed the one live call and M7
  no-request startup smoke before revocation in OpenAI Platform.
- Exactly one provider request was made for this project. No M7 provider request
  was made.
- Ignored local `.env` was removed after revocation. The key value was never
  committed or included in a release artifact.
- M7: **COMPLETE**.
- M8: **IN_PROGRESS**.
- Live GPT-5.6 implementation and recorded replay: **COMPLETE**.
- Public repository: **COMPLETE**.
- Devpost project and thumbnail: **DRAFT COMPLETE**.
- Recording script and shot list: **DRAFT COMPLETE**.
- Screen capture, user voice-over, public video, required personal fields,
  `/feedback` Session ID, and final Devpost submission: **PENDING**.
- Current M8 blocker: **NONE**.

## Compact recovery capsule

- Phase: M8 recording readiness; M0–M7 complete; feature freeze active.
- Public repository: `https://github.com/ModernBlueprints/reversible-name-atlas`;
  M8 content package commit `2e808ff`; this synchronized state is the subsequent
  documentation checkpoint in Git history.
- Product evidence: one real `gpt-5.6` call, one sanitized exact-fingerprint
  record, one verified live transaction, three verified keyless replay
  transactions including a fresh-clone run, 116 tests, clean
  lint/format/build/link/secret checks.
- Budget: one request; USD 0.0382 estimated model cost; USD 0.6790 conservative
  reservation; USD 10 cap.
- Prohibitions: no discovery/tournament/harness loop; no new features; no second
  provider request; no secret exposure; no unsupported claim; no silent model
  substitution; no consumption of the final 24-hour reserve for product work.
- Credential cleanup: both temporary keys revoked; ignored `.env` absent; no
  further live request is planned or authorized without new user direction.
- Next operation: **Commit and push this synchronized M8 checkpoint, capture and
  rehearse the exact keyless product footage without another model call, then
  request only the user-owned voice-over, `/feedback` ID, Submitter Type, and
  Country of Residence needed to finish the video and Devpost submission.**
