# Reversible Name Atlas — Frozen Build Specification

Status: **FROZEN**

Production goal: **INACTIVE**

Track: **Work & Productivity**

Tagline: **Refactor the collection. Preserve every identity.**

This document is the sole authority for what the Build Week product is, what it
supports, what it must prove, and what "finished" means. The implementation plan
controls sequence; the production goal controls execution authority; `STATE.md`
reports current facts. Neither of those may silently redefine this specification.

## Source precedence and read certificate

Apply sources in this order:

1. The user's current instructions.
2. The activated production goal in `docs/build/GOAL.md`.
3. This frozen specification.
4. The current official OpenAI Build Week rules and FAQ.
5. Verified repository state and fresh product evidence.
6. The implementation plan and material-decisions history.
7. Earlier research, tournament artifacts, and model opinions.

Controlling conversation record:

- Path: `/Users/nikolai/.codex/attachments/77c587ad-c9d0-435e-a91c-be2f2115c38d/pasted-text.txt`
- Lines: `1,944`
- Bytes: `108,954`
- SHA-256: `1e0cc189e75a95d7f5e504799b4bbc14cc03696880ceb96db3201decc518f8b9`
- Read-through-EOF certificate: complete and validated on 17 July 2026.

The certificate may substitute for rereading only while the hash matches. Reread
through EOF if the hash changes, the certificate is absent, an earlier read was
incomplete, exact source language controls the immediate decision, or current
state conflicts with the source. Never qualify evidence from a truncated read.

Official sources rechecked on 17 July 2026:

- Rules: <https://openai.devpost.com/rules>
- FAQ: <https://openai.devpost.com/details/faqs>
- GPT-5.6 Sol: <https://developers.openai.com/api/docs/models/gpt-5.6-sol>
- Archivematica 1.18 manual normalization:
  <https://www.archivematica.org/en/docs/archivematica-1.18/user-manual/ingest/manual-normalization/>
- BagIt RFC 8493: <https://www.rfc-editor.org/rfc/rfc8493>

The official submission deadline is Wednesday 22 July 2026 at 02:00 CEST.
The protected recording-ready boundary is Tuesday 21 July 2026 at 02:00 CEST.

## Product contract

### PRD-001 — Product

Reversible Name Atlas is a local-first migration workbench for linked digital
collections. It previews canonical renames and structural changes, exposes policy
violations, collisions, broken declared links, and possible meaning loss, asks
for human judgment only where mechanics are insufficient, propagates that
judgment through declared metadata and derivatives, and produces a copy-only
staged package with verified forward and reverse maps.

The dangerous operation is not merely changing a filename. It is migrating an
identity shared by files, metadata, derivatives, paths, and manifests.

### PRD-002 — Initial user and job

The initial user is a digital-preservation specialist or processing archivist
preparing a linked born-digital or digitized collection for preservation or
repository ingest.

Job to be done:

> When I must rename or reorganize a digital collection before it enters another
> system, show me the complete change, preserve every declared relationship,
> isolate the exceptions that need judgment, and verify the staged result before
> I commit it.

Workflow position:

`source export or characterization → Reversible Name Atlas → BagIt, Archivematica, or repository ingest`

Name Atlas complements characterization tools, bulk renamers, OpenRefine, BagIt,
and Archivematica. It does not replace or integrate with them in this MVP.

### PRD-003 — Product breadth

The primary transaction is verified migration of a linked collection:

- canonical identifier adoption;
- role-bearing filenames;
- structural moves into repository-ready directories;
- original/derivative synchronization;
- declared metadata-reference propagation;
- target collision prevention;
- original path preservation as provenance; and
- deterministic staging and proof.

Transliteration is one amber Meaning exception demonstrating why a mechanically
valid migration may still require human judgment. It is not the product category.

### PRD-004 — Interface

Build one standalone, loopback-only local browser application with a conventional
Python CLI. Do not build a Codex plugin, MCP server, hosted SaaS, Electron or
Tauri wrapper, accounts, collaboration, permissions, or cloud hosting.

Codex is the development environment and must remain the primary build record for
`/feedback`. Codex is not a runtime dependency.

### PRD-005 — Authority model

| Actor | Authority |
|---|---|
| Deterministic engine | Discovers declared structure, proposes paths, detects mechanical risk, propagates decisions, stages copies, and verifies invariants |
| GPT-5.6 | Converts bounded, mechanically flagged evidence into an evidence-linked human question with explicit uncertainty |
| Human archivist | Supplies semantic truth and the only approval or edited target decision |

GPT prose can never approve a proposal, make a package exportable, or turn an
item green.

## Supported input

The contract uses these terms consistently:

- **logical collection path**: a POSIX-style path relative to the selected source
  root, such as `objects/example.tif`; it never begins with `data/`;
- **content object**: a regular file referenced exactly once as an original,
  access derivative, or preservation derivative;
- **declared control file**: `metadata/metadata.csv` or the optional
  `normalization.csv`; and
- **source-package member**: either a content object or a declared control file.

Product proof files and BagIt tag files are generated output, not source-package
members.

### IO-001 — Package root

Support one selected local directory containing ordinary Unicode-visible regular
files. Apart from the two declared control files, every regular file below
`objects/`, `manualNormalization/access/`, or
`manualNormalization/preservation/` is a content object and must be accounted
for reciprocally by the declared CSV references. The supported structure is:

```text
<root>/
├── objects/
│   └── ... originals ...
├── manualNormalization/
│   ├── access/
│   │   └── ... optional access derivatives ...
│   └── preservation/
│       └── ... optional preservation derivatives ...
├── metadata/
│   └── metadata.csv
└── normalization.csv  # optional when no derivative mapping is required
```

The selected root may contain only the shown control files, content roots, and
ordinary directories needed to contain referenced content objects. Unexpected
regular files or directories block import; empty content directories are
permitted.

Every source-package member must be a regular file. Symbolic links, sockets,
devices, FIFOs, resource forks treated as separate special files, and source
paths outside the selected root are unsupported and block the transaction.

### IO-002 — `metadata/metadata.csv`

The metadata CSV must:

- be UTF-8;
- contain a header;
- have `filename` as the first column exactly once;
- contain `dc.identifier` exactly once;
- contain no duplicate or blank header;
- contain exactly one row for every regular file below `objects/`, with no row
  referring outside `objects/`;
- use relative `filename` values beginning with `objects/`;
- resolve each `filename` to exactly one in-scope original; and
- contain no duplicate original reference or unreferenced file below `objects/`.

`dc.identifier` must be:

- non-empty;
- Unicode NFC-normalized;
- unique across original object families; and
- matched by `[A-Za-z0-9][A-Za-z0-9._-]{0,63}`.

`dc.title`, `dc.description`, `dc.language`, and other uniquely named UTF-8
columns may be present. All extra columns and values are preserved exactly as
parsed and serialized. Only the declared `filename` field may be rewritten in
the staged CSV.

### IO-003 — `normalization.csv`

The optional root-level `normalization.csv` is case-sensitive, UTF-8, has no
header, and has exactly three fields per row:

1. original path;
2. access-derivative path, possibly blank; and
3. preservation-derivative path, possibly blank.

Rules:

- the original must resolve to exactly one metadata-declared original;
- each nonblank access derivative must resolve to exactly one regular file below
  `manualNormalization/access/`;
- each nonblank preservation derivative must resolve to exactly one regular file
  below `manualNormalization/preservation/`;
- each original may have at most one access and one preservation derivative;
- one derivative may belong to only one original family;
- every regular file below either derivative root must appear exactly once in
  the matching derivative field;
- duplicate rows, duplicate roles, many-to-many relationships, and orphaned
  references block the transaction; and
- every path is relative, normalized, inside the selected root, and free of `.`
  and `..` segments.

`normalization.csv` may be absent only when both derivative roots contain no
regular files. If it is present, every row must declare at least one derivative.

The hero package must contain this file and exercise both derivative roles or at
least one derivative role across multiple families.

### IO-004 — Explicit exclusions

The MVP does not support:

- `path_plan.csv`;
- arbitrary schema mapping;
- spreadsheet import other than the two declared CSV contracts;
- many-to-many derivative relationships;
- external catalogs or databases;
- ArchivesSpace, AtoM, or live Archivematica integration;
- embedded-link discovery in PDFs, office files, databases, or media;
- legacy raw filename-byte recovery;
- source mutation; or
- partial package export.

### IO-005 — Fail-closed input behavior

Block before copying when any required input is missing, empty, malformed,
non-UTF-8, ambiguous, duplicated, outside the root, unsupported, unresolved, or
not reciprocally accounted for by the CSV graph. Errors must identify the
offending path, row, column, relationship, or invariant. No source-package
member may be silently omitted.

## Deterministic transformation

### TX-001 — Source snapshot

At scan time, inventory every source-package member with:

- original POSIX-style relative path;
- role;
- byte size;
- streamed SHA-256 payload digest; and
- member kind (`content_object` or `declared_control_file`).

Re-scan immediately before staging. Any added, removed, renamed, resized, or
content-changed source member blocks staging. Never retain the API key or source
payload bytes in product artifacts.

### TX-002 — Stable object family

Create one `ObjectFamily` per metadata-declared original. The stable internal
family ID is the lowercase SHA-256 hex digest of canonical UTF-8 bytes:

`"family\0" + dc.identifier + "\0" + original_relative_path`

The ID is deterministic across repeated scans of the unchanged package and does
not change when a proposed or staged path changes. Identical payloads remain
distinct when their identifiers or original paths differ.

### TX-003 — Repository-ready profile

Use exactly one profile named **Repository-ready identity profile**.

For each family:

1. Take the final filename stem of the original path as the descriptor source.
2. Normalize it with Unicode NFKD.
3. Remove Unicode combining marks and record every removed mark as a Meaning
   risk signal.
4. Lowercase ASCII letters.
5. Preserve ASCII digits.
6. Map whitespace, ASCII `.`, `_`, `-`, and unsupported punctuation to `-`.
7. Remove remaining non-ASCII code points from the projected descriptor while
   recording each removal as a Meaning risk signal.
8. Collapse repeated `-` characters and trim leading/trailing `-`.
9. Reject an empty descriptor.
10. Lowercase each file's existing final extension and require it to match
    `\.[a-z0-9]{1,16}`.

This intentionally projects `campaña` to `campana` and records removal of the
combining tilde as a Meaning risk.

Target leaves are:

`{dc.identifier}__{descriptor}__{role}{lowercase_extension}`

Target directories are:

- original: `objects/`;
- access: `manualNormalization/access/`; and
- preservation: `manualNormalization/preservation/`.

All members of a family use the descriptor derived from the original. Each member
retains its own lowercased extension. The profile changes only staged paths and
declared staged references.

### TX-004 — `PathProposal`

Each serialized proposal is role-specific and contains:

- stable family ID;
- canonical identifier;
- role;
- original relative path;
- proposed relative path;
- proposal source (`repository_ready_profile` or `human_edit`);
- ordered transformation steps;
- affected declared references;
- Policy, Collision, Links, and Meaning risk signals;
- human-resolution state;
- verification state; and
- evidence addresses.

All proposals remain visible. Low-risk proposals may be grouped, but batch
approval is an explicit human action. Nothing passes silently.

### TX-005 — Collision and profile comparison

Before any copy, prove target uniqueness independently under:

- exact Unicode scalar comparison;
- NFC-normalized comparison; and
- NFC-normalized Unicode `casefold()` comparison.

Any duplicate under any comparison is a red blocker. Also block a target that is
absolute, escapes the package, contains empty/`.`/`..` segments, violates the
profile, or collides with a product-generated metadata or BagIt path.

### TX-006 — Human decisions

`HumanDecision` is a family-level record containing the stable family ID, action,
human input, and a complete `resolved_targets` map keyed by every role present in
that family. Decision states are:

- `pending`;
- `approved`;
- `edited`;
- `refused`; and
- `unresolved`.

`approved` atomically copies every role-specific proposed target for the family
into `resolved_targets`.

`edited` accepts one human-entered family descriptor matching
`[a-z0-9]+(?:-[a-z0-9]+)*`. The decision service derives a complete target for
every present role using the fixed identifier, directory, role, and extension
rules in `TX-003`, validates the complete family and global target set under
`TX-005`, and then stores that immutable role-to-target map in
`resolved_targets`. It never silently normalizes the human input.

`refused` records an explicit refusal and has no resolved targets.
`pending` and `unresolved` also have no resolved targets. All three block the
complete package. A decision cannot be partially resolved by role.

### TX-007 — Identity-level propagation

Apply an approved or edited family decision from its stored
`resolved_targets` map to:

- original staged path;
- access and preservation staged paths;
- the metadata row's `filename` field;
- all three fields of the applicable `normalization.csv` row;
- the decision ledger;
- the forward map; and
- the reverse map.

No downstream consumer independently recomputes a final target from strings
after the decision service stores the complete map.

### TX-008 — Copy-only staging

Staging is an all-or-nothing local transaction whose final directory is a BagIt
1.0 bag. Logical collection paths remain the product and reference namespace.
Physical BagIt payload paths are exactly `data/{logical_collection_path}`.
Metadata values and forward/reverse maps never include the physical `data/`
prefix.

The transaction:

1. Refuse a pre-existing destination unless the user explicitly selects a new
   empty destination.
2. Create a new pending directory.
3. Re-scan and compare the source snapshot.
4. Copy every content object to its resolved physical BagIt payload path without
   editing its bytes.
5. Write rewritten declared control files below `data/`, changing only their
   declared logical path-reference fields.
6. Generate product proof artifacts as BagIt tag files below
   `name-atlas/`, outside `data/`.
7. Write `bagit.txt`, `bag-info.txt`, SHA-256 payload manifests, and SHA-256 tag
   manifests.
8. Run all product checks and the package validator.
9. Promote the pending directory only after every invariant passes.
10. Preserve a failure report and do not expose a failed stage as exportable.

## GPT-5.6 decision cards

### AI-001 — Model and provider boundary

Use the official OpenAI Responses API with model alias `gpt-5.6`. At scaffold
time the alias routes to GPT-5.6 Sol and supports structured outputs. Do not
silently substitute another model.

Define a structural `DecisionCardProvider` protocol equivalent in behavior to:

`generate(packet: EvidencePacket) -> DecisionCard`

Implementations:

- live GPT-5.6 provider;
- recorded GPT-5.6 replay provider; and
- deterministic test double.

Model transport and prompting must not be coupled to source scanning, decisions,
staging, or verification.

### AI-002 — Bounded evidence packet

The packet may contain only:

- stable family ID;
- original and proposed relative paths;
- exact transformation steps;
- mechanically supplied candidate paths;
- selected neighboring relative paths;
- linked metadata values with evidence IDs;
- derivative relationships with evidence IDs;
- deterministic risk signals; and
- profile description.

Do not upload payload bytes. Before a live call, display the complete outbound
text to the user and require an explicit Generate action.

### AI-003 — Structured output

`DecisionCard` contains only:

- possible interpretations;
- possible meaning loss;
- evidence IDs supporting each observation;
- explicit uncertainty;
- why the distinction matters;
- one discriminating human question; and
- explanations of supplied candidate paths.

The schema contains no field or executable authority for `safe`, `correct`,
`verified`, `approved`, `final_target`, `exportable`, or semantic truth.

### AI-004 — Validation and failure

Validate that:

- every evidence ID exists in the submitted packet;
- every referenced candidate path was mechanically supplied;
- required fields and cardinalities match the Pydantic contract; and
- the response is bound to the complete evidence fingerprint.

Unknown evidence, unknown candidates, malformed output, API failure, model
unavailability, or cap exhaustion leaves the item explicitly unresolved. Never
fabricate a card or approve a proposal after failure.

### AI-005 — Calls, cache, and cost

Call GPT only for mechanically flagged Meaning risk. Cache only by SHA-256 of the
complete canonical evidence packet plus model and schema version. Changed evidence
invalidates the cache. Expose:

- cards requested;
- live calls made;
- replay cards used;
- cache hits;
- calls avoided by deterministic triage;
- model;
- tokens when reported;
- latency;
- estimated cost; and
- configured call/cost cap.

The activated goal caps total project OpenAI API spend at USD 10. Reaching the cap
leaves manual-review states and never changes authority.

### AI-006 — Replay truthfulness

Replay data must originate from a real, validated `gpt-5.6` response generated
during this build. The UI and README display **Recorded GPT-5.6 response**. The
record includes model, evidence fingerprint, response schema version, generation
timestamp in Europe/Oslo, and a sanitized response. It contains no API key or
sensitive source data.

## User experience

### UX-001 — Atlas

Show collection and family counts, source structure, canonical identifiers,
original/access/preservation relationships, metadata links, profile, proposal
counts, structural moves, and risk counts across Policy, Collision, Links, and
Meaning.

### UX-002 — Decisions

Show before/after paths, exact transformation steps, affected links, evidence,
outbound GPT packet preview, neutral decision card, human approve/edit/refuse
controls, explicit unresolved state, low-risk batch approval, and export-readiness
progress.

### UX-003 — Proof

Show source snapshot status, staged location, content-object byte equality,
declared-reference resolution, target uniqueness, profile validity,
forward/reverse-map status,
reverse dry run, BagIt validation, unresolved/failed items, and open/download
controls for proof artifacts.

### UX-004 — Visual authority

- Green: deterministic fact verified after required human resolution.
- Amber: human judgment is still required.
- Red: mechanical blocker or failed invariant.
- Neutral: GPT explanation and uncertainty.

GPT prose alone never changes status color.

### UX-005 — Hero and negative fixtures

Create one redistributable synthetic hero package with approximately 12 object
families and 25–35 regular files. It must demonstrate canonical renaming,
directory moves, metadata rewrites, original/derivative propagation, one collision
that is resolved, `campaña → campana` resolved by the human after a live GPT card,
and a successful staged package.

Create one tiny negative fixture proving that an unresolved semantic decision,
collision, malformed input, or orphaned reference blocks export. Do not create
multiple polished collections. Record sample provenance.

## Verification and product artifacts

### VER-001 — Generated artifacts

Generate at least:

- `name-atlas/source_snapshot.json`;
- `name-atlas/decision_ledger.json`;
- `name-atlas/forward_path_map.csv`;
- `name-atlas/reverse_path_map.csv`;
- `name-atlas/verification_report.json`;
- a human-readable summary below `name-atlas/`; and
- `bagit.txt`, `bag-info.txt`, `manifest-sha256.txt`, and
  `tagmanifest-sha256.txt`.

The complete transformed logical collection, including content objects and
rewritten declared control files, is below `data/`. Forward and reverse maps have
one inverse row per content object and use logical collection paths. The fixed
control-file paths are not map rows; their source and staged hashes plus
field-level changes are recorded in `verification_report.json`.

### VER-002 — Integrity claim

The product may display **Verified round-trip integrity within the supported
package contract** only when all are true:

- the fresh pre-staging snapshot equals the initial snapshot;
- the application did not mutate any source-package member;
- every staged content object's SHA-256 equals its corresponding source content
  object's SHA-256;
- declared control files retain all rows, columns, non-path values, and ordering,
  and only declared path-reference fields change;
- every declared staged metadata and derivative reference resolves;
- every target satisfies the profile;
- every target is unique under all declared comparisons;
- forward and reverse maps are complete and exact inverses over the complete
  content-object logical path set;
- a reverse dry run reconstructs the original content-object logical paths and
  declared reference values;
- no required decision, unsupported input, or failed invariant remains;
- the Library of Congress `bagit` validator passes; and
- the Proof UI and serialized verification report agree.

### VER-003 — Required automated scenarios

Automate these scenarios, with UI-level verification where appropriate:

1. Valid hero-package import creates stable object families and declared edges.
2. A non-lossy proposal stays visible, supports explicit batch approval, and
   makes no GPT call.
3. `campaña → campana` creates a Meaning-risk evidence packet.
4. GPT can explain interpretations and ask a question but cannot approve or set a
   final target.
5. Unknown GPT evidence IDs and candidate paths are rejected.
6. Malformed output, API absence, model failure, or cap exhaustion remains
   unresolved.
7. Identical complete evidence reuses a cached card; changed evidence does not.
8. An unresolved, pending, or refused required decision blocks the whole export.
9. Exact, NFC, or casefold target collision blocks before copying.
10. Invalid/duplicate/missing `dc.identifier` blocks import.
11. Malformed/non-UTF-8 CSV, path traversal, symlink, or special file fails closed.
12. Orphaned, duplicated, or many-to-many derivative/reference data fails closed.
13. One family decision rewrites original, derivatives, metadata, and normalization
    data from the same stable identity.
14. A changed source tree blocks at the pre-staging re-scan.
15. Copy failure never promotes an exportable stage.
16. All source-package members and logical paths remain unchanged.
17. Staged content-object hashes equal source content-object hashes, while
    control-file changes are limited to declared path-reference fields.
18. Forward/reverse maps round-trip the complete content-object logical path set
    and reconstruct the declared reference values without a `data/` prefix.
19. BagIt validation succeeds for the resolved hero package.
20. The Proof UI equals `verification_report.json`.
21. Replay mode launches without an API key and is visibly labeled.
22. Live mode generates and displays a real validated `gpt-5.6` card.
23. A clean clone can run the documented replay judge path.

### VER-004 — Unsupported proof claims

Do not claim preservation of filesystem ACLs, extended attributes, creation or
modification timestamps, resource forks, undeclared external references,
arbitrary embedded links, every filesystem's byte-level filename representation,
live Archivematica acceptance, universal reversibility, or semantic correctness.

## Architecture and judge contract

### REL-001 — Technology

Use:

- Python 3.11;
- `uv` and a committed lockfile;
- FastAPI;
- Jinja2;
- small, dependency-light vanilla JavaScript and CSS;
- Pydantic v2 at serialized/external boundaries;
- official OpenAI Python SDK;
- pytest;
- Ruff;
- Library of Congress `bagit`; and
- standard-library hashing and filesystem primitives where sufficient.

Support and test the judge path on macOS with Python 3.11. Keep POSIX/Linux
compatibility where ordinary `pathlib` behavior permits it. Windows support is
not an MVP claim.

### REL-002 — Modules

Keep bounded modules for:

- source snapshot and identity;
- strict package import;
- object-family graph;
- path proposals and risk detection;
- GPT decision cards;
- human decisions;
- staging transaction;
- verification and BagIt validation;
- product artifacts; and
- web presentation.

Use `Protocol` at the GPT provider and package-validator boundaries. Avoid a God
transaction module, circular dependencies, ceremonial abstractions, and a new
framework.

### REL-003 — Judge commands

The final repository must support:

```text
uv sync --frozen
uv run name-atlas demo --mode replay
uv run name-atlas demo --mode live
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

Replay must require no API key. Live mode must fail clearly when the key is
absent and must never substitute another model.

### REL-004 — Recording-ready definition

By Tuesday 21 July 2026 at 02:00 CEST:

- every required product behavior works end to end;
- a real GPT-5.6 card has been generated, validated, displayed, and recorded;
- GPT has no approval or verification authority;
- the source remains unmodified;
- hero staging and all proof invariants pass;
- live and visibly labeled replay modes work;
- the full required automated suite, lint, and formatting pass;
- the UI has been visually verified at recording resolution;
- the hero and negative fixtures and provenance are final;
- the repository is public, MIT-licensed, clean, and points to the final commit;
- a clean clone runs from the README;
- README, limitations, pre-existing-work disclosure, Codex build log, screenshots,
  setup, judge commands, and troubleshooting are complete;
- the exact demonstration has been rehearsed;
- a 2:35–2:45 English narration draft and shot list are timed;
- Devpost title, track, description, technologies, repository URL, screenshots,
  Codex/GPT explanation, and claim language are drafted;
- the `/feedback` path is known;
- current submission requirements have been checked; and
- no code, design, or feature work remains planned.

The following 24 hours are reserved for the user's voice-over, final capture and
editing, upload/playback verification, `/feedback`, due diligence, final clean
run, Devpost submission, and receipt confirmation.

## Claims

### CLAIM-001 — Permitted only after implementation and verification

- Makes mechanically risky collection-path transformations visible and reviewable.
- One human decision propagates through supported declared links.
- Creates a copy-only staged package.
- Verifies content-object hashes, declared references, collisions, profile
  compliance, and in-scope logical-path round trips.
- GPT-5.6 synthesizes bounded linked context into an evidence-linked human
  decision card.
- Supports one documented package shape and one repository-ready profile.
- Keeps the source untouched by the staging transaction.
- In the tools reviewed, this exact bounded transaction was not found.

### CLAIM-002 — Forbidden

Do not claim:

- a critical or universal crisis;
- that archivists constantly experience the problem;
- 50% or any other unmeasured time saving;
- faster recurring work;
- GPT-5.6 determines the correct name;
- AI verifies semantic correctness or safety;
- no wrong transformations;
- mathematical or universal reversibility;
- full filesystem preservation;
- compliance certification or legal-record assurance;
- Archivematica certification, compatibility, or live integration;
- that Archivematica expects clean input or lacks filename handling;
- all bulk renamers break metadata;
- arbitrary schemas or every archival workflow are supported;
- million-file scalability;
- production readiness;
- institutional acceptance;
- OpenAI or another AI lab uses or needs the workflow;
- proven superiority over ordinary Codex;
- a high probability of winning; or
- nothing else exists.

Record actual demonstration facts instead: objects, families, references,
proposals, moves, risk triggers, calls avoided/made, cache hits, collisions,
rewrites, unresolved decisions, validation outcomes, latency, and measured API
cost.

## Explicit product exclusions

Do not build during Build Week:

- another project-discovery process, tournament, benchmark, or validation harness;
- a general archive-management, DAM, ETL, data-catalog, or enterprise file tool;
- a naming-policy language or drag-and-drop schema builder;
- arbitrary input adapters or `path_plan.csv`;
- a repository connector platform;
- cloud synchronization, hosting, accounts, collaboration, or permissions;
- automatic source renaming;
- automatic semantic approval or AI-generated canonical identifiers;
- general deduplication or entity resolution;
- live Archivematica integration;
- AI-lab-specific workflow; or
- functionality unrelated to the complete three-minute submission story.

## Final completion surfaces

The activated production goal may be marked complete only when every surface is
complete and the Devpost submission is confirmed:

- Product implementation: `COMPLETE` or `PARTIAL`.
- Deterministic integrity proof: `COMPLETE` or `PARTIAL`.
- GPT-5.6 integration: `COMPLETE` or `PARTIAL`.
- Judge execution path: `COMPLETE` or `PARTIAL`.
- Recording-ready package: `COMPLETE` or `PARTIAL`.
- Submission materials: `COMPLETE` or `PARTIAL`.
- Devpost submission: `COMPLETE` or `BLOCKED`.
- Overall Build Week delivery: `COMPLETE` only when all required surfaces and the
  confirmed submission are complete.

Time exhaustion, substantial code, or a nearly complete project is not completion.
