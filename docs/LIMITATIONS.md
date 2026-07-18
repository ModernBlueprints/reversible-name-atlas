# Limitations and claim boundaries

Reversible Name Atlas is a Build Week MVP with one strict linked-package
contract and one repository-ready transformation profile. Its verification and
restore claims apply only to the supported transaction in
[`build/BUILD_SPEC.md`](build/BUILD_SPEC.md). They are not production-readiness,
compliance, semantic-correctness, historical-source-authenticity, or universal
preservation claims.

## Local Migration Case versus portable handoff

The local `migration-case.v1` file is the sole mutable workflow authority. It may
contain sender-local absolute source, output, case, and handoff paths. It is not
portable or executable on another machine.

The completed bag contains a path-neutral `portable-change-receipt.v1` and its
receipt-bound machine artifacts. The receipt describes one finalized historical
transaction; it does not provide `apply-case`, cross-machine case execution,
source reconciliation, case rebasing, or decision carry-forward.

Before finalization, an added, removed, renamed, resized, content-changed, or
unreadable source makes the case terminally stale and blocks mutation and
staging. The supported recovery is to preserve that case and create a fresh one
at a different absent path. A `handoff_ready` case is read-only and is not
silently rewritten when the sender's source later changes.

## BagIt and Name Atlas prove different things

Library of Congress `bagit` validation checks the bag's declared payload and tag
fixity and completeness. It does not reconstruct or validate Name Atlas's
proposal, evidence, decision, map, profile, and original-control semantics.

The independent receiver verifier additionally validates the receipt-core
fingerprint, raw artifact commitments, staged-data commitment, strict schemas,
path neutrality, deterministic proposal authority, evidence/card/human bindings,
declared control rewrites, payload and map relationships, producer findings, and
non-authoritative derived views.

A handoff can therefore pass ordinary BagIt validation while Name Atlas blocks
it. The release demonstrates this with a disposable copy whose decision ledger
is changed and whose BagIt tag manifest is regenerated: BagIt passes, while Name
Atlas reports `artifact_digest_mismatch:decision_ledger` because the ledger no
longer matches the immutable receipt.

This does not provide cryptographic sender identity, signatures, human-authorship
authentication, institutional authorization, or resistance to an actor who
rewrites every artifact and issues a new internally consistent receipt.

## Source-free verification boundary

`verify-receipt RECEIVED_BAG` requires no original source, local case, API key,
GPT call, network, or browser. It proves internal transaction consistency
against the source description committed by the receipt. It does **not** prove
that this description represents an authentic historical source.

`verify-receipt RECEIVED_BAG --source SOURCE_ROOT` adds a current, exact
path/role/size/SHA-256 comparison between the supplied supported source and the
portable snapshot. Even that comparison does not authenticate who supplied the
source or when it existed.

## Restore boundary

`restore-receipt` is a verify-first, copy-only **logical package restore**. It
reconstructs every in-scope source-package member through the reverse map and
byte-exact original declared controls, strictly reimports the pending directory,
proves equality with the portable snapshot, and promotes only to a new absent
destination.

The bounded permitted claim is:

> Reconstructs every in-scope source-package member byte-for-byte within the
> supported Name Atlas package contract.

It does not restore or claim preservation of:

- filesystem access-control lists;
- ownership or permissions beyond what is needed to create the new local copy;
- file creation or modification timestamps;
- extended attributes;
- resource forks;
- undeclared external references;
- arbitrary embedded links;
- every filesystem's byte-level filename representation; or
- arbitrary filesystem state outside the supported package members.

Restore does not edit the received handoff, overwrite an existing destination,
or place its `restore-report.v1` result into the immutable bag.

## GPT-5.6 evidence boundary

One real `gpt-5.6` card was generated from the exact visible hero packet and
persisted as a sanitized replay record. The record is bound to its model alias,
schema version, and complete evidence fingerprint; it cannot be used for another
source. This proves one bounded live response and deterministic replay for the
included fixture. It does not establish general model reliability, semantic
correctness, workflow coverage, or scalability.

GPT-5.6 receives only the bounded text evidence displayed before a live request;
it receives no source payload bytes. Its card can explain possible
interpretations, possible meaning loss, uncertainty, and one discriminating
question. It cannot:

- determine the correct name;
- establish semantic truth;
- approve, edit, refuse, or resolve a family;
- select or set a final target;
- resolve a collision;
- verify safety or correctness;
- make a package exportable; or
- override a deterministic blocker.

Only an explicit human action creates decision authority. Missing credentials,
unknown evidence, malformed output, API failure, model unavailability,
cost-cap exhaustion, or a mismatched replay record leaves the family unresolved.

## Supported scope only

The MVP supports regular files inside one selected local root, required UTF-8
`metadata/metadata.csv`, optional UTF-8 `normalization.csv`, at most one access
and one preservation derivative per original, one fixed identifier-based path
profile, explicit whole-family human decisions, and all-or-nothing copy-only
BagIt staging.

The MVP does not support:

- `path_plan.csv`, arbitrary schema mapping, or a general policy/profile builder;
- spreadsheets other than the two declared CSV contracts;
- many-to-many derivative relationships;
- external catalogs, databases, or repository connectors;
- ArchivesSpace, AtoM, or live Archivematica integration;
- embedded-link discovery in PDFs, office files, databases, or media;
- legacy raw filename-byte recovery;
- source mutation or partial package export;
- case reconciliation, rebasing, destructive reset, or decision carry-forward;
- `apply-case` or another cross-machine executable case;
- signatures, sender authentication, or institutional authorization;
- accounts, collaboration, permissions, hosted deployment, or cloud storage;
- a Codex plugin or MCP runtime interface;
- NER, ReFinED, entity linking, AI-training-data, JSONL, Parquet, Hugging Face,
  or generic adapter work;
- direct repository integration, RO-Crate, or another metadata platform;
- React, Vite, a client-side state framework, or a Node judge path;
- Linux or Windows as tested judge platforms;
- multiple polished collections; or
- million-file scalability.

Unsupported, malformed, ambiguous, orphaned, colliding, refused, unresolved,
or changed input blocks the complete package. The product does not attempt a
best-effort partial migration.

## Exact integrity claim boundary

The phrase **Verified round-trip integrity within the supported package
contract** is permitted only when every requirement in `VER-002` passes. It
means that:

- the source snapshot remained equal through staging;
- content-object hashes match;
- only the supported declared reference fields changed;
- declared links resolve;
- targets satisfy the one profile without exact, NFC, or casefold collisions;
- forward and reverse maps are complete inverses;
- the reverse dry run reconstructs original logical paths and declared values;
- every required decision is approved or edited by the human;
- no unsupported input or failed invariant remains;
- producer and receiver machine records agree; and
- BagIt validation passes.

It does not prove semantic correctness, universal or mathematical
reversibility, full filesystem preservation, authentic historical provenance,
live Archivematica acceptance, downstream repository acceptance, archival or
legal-record assurance, or regulatory compliance.

## Claims this project does not make

Reversible Name Atlas does not claim:

- that the problem is a critical or universal crisis;
- that archivists constantly experience it;
- 50% or any other unmeasured time saving;
- faster recurring work;
- that GPT-5.6 determines the correct name;
- that AI verifies semantic correctness or safety;
- that wrong transformations cannot occur;
- mathematical or universal reversibility;
- sender identity, human authorship, institutional authorization, signatures,
  or cryptographic authentication;
- full filesystem preservation;
- compliance certification or legal-record assurance;
- Archivematica certification, compatibility, or live integration;
- that Archivematica expects clean input or lacks filename handling;
- that all bulk renamers break metadata;
- support for arbitrary schemas or every archival workflow;
- production readiness;
- institutional acceptance or adoption;
- that OpenAI or another AI lab uses or needs this workflow;
- AI-training-data or model-curation readiness;
- proven superiority over ordinary Codex;
- a proven high probability of winning; or
- that nothing else exists.

No practitioner-prevalence, institutional-adoption, time-saving, recurring-speed,
or large-scale performance claim has been measured. Public descriptions
should report only observed transaction facts such as families, objects,
references, moves, decisions, risk triggers, calls, cache hits, collisions,
rewrites, fingerprints, validation outcomes, reported token usage, latency, and
estimated model cost.
