# Reversible Name Atlas — Material Decisions

Status: **HISTORICAL RATIONALE ONLY**

This is an append-only-in-substance record of why material choices or deviations
occurred. It is not an active specification, plan, goal, or checkpoint. Current
truth must be written in `BUILD_SPEC.md` or `IMPLEMENTATION_PLAN.md`; current
facts must be verified and summarized in `STATE.md`. A record here never
overrides those artifacts.

## D-001 — Broader linked-collection migration

- Date: 17 July 2026
- Decision: Position the product around verified migration of linked collection
  identities, not transliteration.
- Rationale: Renames affect files, metadata, derivatives, paths, and manifests.
  Transliteration demonstrates one important ambiguity but does not express the
  complete user transaction.
- Active authority: `PRD-001`–`PRD-003`.

## D-002 — Standalone local browser interface

- Date: 17 July 2026
- Decision: Use a loopback-only standalone browser application; exclude a Codex
  plugin and MCP server.
- Rationale: The complete Atlas, Decisions, and Proof workflow needs a persistent
  visual state, and the official requirement is meaningful Codex use in building
  the project rather than a mandatory Codex runtime.
- Active authority: `PRD-004` and `UX-001`–`UX-004`.

## D-003 — Whole-package fail-closed export

- Date: 17 July 2026
- Decision: Any unsupported input, blocker, refused proposal, or unresolved
  required decision blocks the complete selected package.
- Rationale: A partial stage could sever declared identity relationships while
  appearing complete.
- Active authority: `IO-005`, `TX-006`, and `TX-008`.

## D-004 — One hero and one tiny negative fixture

- Date: 17 July 2026
- Decision: Build one polished synthetic hero package and one minimal blocking
  fixture, not multiple polished collections.
- Rationale: One complete judge-facing transaction is higher value within the
  schedule than several shallow demonstrations.
- Active authority: `UX-005`.

## D-005 — `path_plan.csv` excluded

- Date: 17 July 2026
- Decision: Exclude `path_plan.csv` from the MVP, required tests, hero flow, and
  video.
- Rationale: The fixed repository-ready profile already demonstrates canonical
  renaming and structural moves. A future adapter remains possible through
  `PathProposal` without consuming Build Week scope.
- Active authority: `IO-004` and `TX-004`.

## D-006 — Governance repository before activation

- Date: 17 July 2026
- Decision: Initialize the fresh repository and commit only the operating
  scaffold before activating product work.
- Rationale: This creates one canonical home for governance artifacts without
  starting H+0, dependencies, code, API use, or publication.
- Active authority: the user-approved meta-implementation plan and `GOAL.md`.

## D-007 — Absolute 80/24 timing model

- Date: 17 July 2026
- Decision: Protect Tuesday 21 July 2026 at 02:00 CEST as recording-ready and
  Wednesday 22 July 2026 at 02:00 CEST as submission-confirmed.
- Rationale: A full 80-hour product period plus 24-hour submission reserve fits
  only if activation occurs by Friday 17 July 2026 at 18:00 CEST. Later
  activation compresses targets and optional scope, not required outcomes or the
  final reserve.
- Active authority: `IMPLEMENTATION_PLAN.md` timing model.

## D-008 — Fixed identifier and profile

- Date: 17 July 2026
- Decision: Require one unique NFC `dc.identifier` per family and use it as the
  canonical target prefix with the original stem-derived descriptor and role.
- Rationale: This closes the identity source and transformation algorithm so the
  hero transaction, collision checks, and proof are implementable.
- Active authority: `IO-002` and `TX-002`–`TX-005`.

## D-009 — Selective spike adaptation

- Date: 17 July 2026
- Decision: Treat the earlier spike as a catalog of mechanical behaviors and
  focused scenarios, not as the new application's foundation.
- Rationale: Its snapshot, path, mapping, and transaction lessons are useful,
  while its monolithic transaction, raw-path bundle, semantic executable, and
  tournament evaluator are incompatible with the product.
- Active authority: `docs/PREEXISTING_WORK.md`.

## D-010 — Earlier enormous goal superseded

- Date: 17 July 2026
- Decision: The earlier comprehensive project-discovery/execution `/goal` is
  superseded and inactive. Only the concise committed `GOAL.md` may be activated.
- Rationale: Product truth, delivery sequence, authority, and current state need
  separate owners to prevent contradictions and validation-harness recursion.
- Active authority: `GOAL.md` after explicit activation.

## D-011 — Root-commit baseline locator

- Date: 17 July 2026
- Decision: Identify the governance baseline inside committed artifacts as the
  unique root commit on branch `main` with subject
  `chore: establish build operating scaffold`. Report its exact SHA-1 in the
  handoff and resolve it from Git after activation.
- Rationale: A commit cannot contain its own hash: inserting that hash changes
  the commit. This corrects the accepted plan's self-referential requirement
  while preserving exactly one scaffold commit and an unambiguous immutable
  baseline.
- Active authority: `GOAL.md` and fresh Git history.
