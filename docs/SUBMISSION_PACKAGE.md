# Reversible Name Atlas — Build Week Submission Package

Status: **M8 DRAFT — PRODUCT AND PUBLIC REPOSITORY VERIFIED; VIDEO AND FINAL
SUBMISSION PENDING**

This is the single working package for the demo, Devpost entry, and final
submission audit. It is not another product specification or implementation
plan. Product claims remain controlled by `docs/build/BUILD_SPEC.md` and
`docs/LIMITATIONS.md`.

## Official requirement snapshot

Rechecked on Friday 17 July 2026 against the
[Official Rules](https://openai.devpost.com/rules),
[FAQ](https://openai.devpost.com/details/faqs), current Devpost submission-form
schema, announcements, and judging criteria:

- submissions close Wednesday 22 July 2026 at 02:00 CEST;
- the project must be working and use Codex and GPT-5.6 meaningfully;
- the selected project must fit one track;
- the video must be public on YouTube, contain a clear working demo with audio,
  cover the project plus Codex and GPT-5.6 use, and remain under three minutes;
- the submission needs a repository URL, a README with setup/sample/testing and
  specific Codex/GPT-5.6 contributions, and the primary task's `/feedback`
  Session ID; and
- the four equally weighted criteria are Technological Implementation, Design,
  Potential Impact, and Quality of the Idea.

Current Devpost state: the user is already registered; submissions are open; no
rule-changing announcement was found; the existing unrelated **Preflight**
draft will not be overwritten. Reversible Name Atlas will use a separate
Devpost project.

## Frozen submission identity

- **Project:** Reversible Name Atlas
- **Tagline:** Refactor the collection. Preserve every identity.
- **Track:** Work & Productivity
- **One-line summary:** A local-first workbench that previews, resolves, and
  proves linked-collection renames without modifying the source package.
- **Public repository:**
  <https://github.com/ModernBlueprints/reversible-name-atlas>
- **License:** MIT
- **Judge path:** `uv sync --frozen`, then
  `uv run name-atlas demo --mode replay`
- **Built with:** Codex, GPT-5.6, OpenAI Responses API, Python 3.11, FastAPI,
  Jinja2, Pydantic v2, vanilla JavaScript/CSS, BagIt, pytest, Ruff, and uv
- **Video URL:** `[PENDING PUBLIC YOUTUBE VIDEO]`
- **Primary `/feedback` Session ID:**
  `[USER ACTION: run /feedback in this primary Codex task and supply the exact ID]`

## Devpost description draft

### The problem

A linked digital object is more than one filename. An original can also have an
access derivative, a preservation derivative, a metadata row, and a declared
relationship file. A bulk rename can make the visible filename cleaner while
leaving those references inconsistent—or it can silently flatten language that
carried meaning.

For the supported package contract, a preservation specialist therefore needs
more than a rename command. They need to see the complete proposed transaction,
separate mechanical blockers from questions requiring judgment, keep every
declared family member synchronized, leave the source untouched, and retain
evidence that the staged result is internally consistent.

### What Reversible Name Atlas does

Reversible Name Atlas is a standalone, loopback-only migration workbench. It
indexes one strict linked-collection package, assembles stable object families,
previews a fixed identifier-based rename and directory profile, and displays
Policy, Collision, Links, and Meaning risks before any copy transaction begins.

The workflow has three connected states:

1. **Atlas** exposes the source structure, canonical identifiers, derivative
   relationships, proposed paths, and complete risk counts.
2. **Decisions** shows every transformation step and affected reference. Routine
   proposals can be approved explicitly as a batch. Mechanical collisions must
   be edited mechanically. A Meaning risk can receive a neutral GPT-5.6
   decision card, but only the human can approve, edit, refuse, or leave the
   family unresolved.
3. **Proof** becomes available only after all 12 hero families are resolved and
   no deterministic blocker remains. The product stages copies, rewrites only
   declared path fields, generates forward and reverse maps, performs a reverse
   dry run, compares payload hashes, rechecks the source snapshot, validates a
   BagIt 1.0 package, and promotes the staged result only when every in-scope
   invariant passes.

The source package is never renamed, edited, or deleted by the staging
transaction. An unresolved, refused, colliding, changed, malformed, or
unsupported item blocks the whole package.

### Why GPT-5.6 is inside the workflow

GPT-5.6 is used only where deterministic mechanics have already identified a
possible loss of meaning. Before the request, Name Atlas shows the complete
bounded outbound packet: stable family ID, exact source and proposed paths,
transformation trace, linked metadata values, derivative relationships,
neighboring paths, mechanically supplied candidates, and evidence IDs. Payload
bytes and approval fields never leave the application.

The release contains a sanitized, evidence-bound record from one real
`gpt-5.6` call over the hero packet. That call received 14 addressed facts and
returned a structured advisory card explaining the possible loss in changing
`campaña` to `campana`, its uncertainty, why the distinction matters, and one
discriminating question. It did not choose a target. The demonstrated final
descriptor, `campaign-poster`, was entered by the human.

For reliable judging, replay mode loads that exact validated record only when
the model, schema, and complete evidence fingerprint match. It is visibly
labeled **Recorded GPT-5.6 response** and needs no API key. No fallback model is
substituted.

### How Codex was used

Codex with GPT-5.6 was the primary development environment and integrator. The
primary task translated a frozen product contract into dependency-ordered
vertical slices, implemented the modular Python application, integrated bounded
parallel reviews, exercised the browser product, and maintained the release
state through a public clean-clone check.

Codex materially accelerated the requirement-to-working-transaction loop by
keeping the product runnable after each slice and turning reproduced proof
failures into regression tests. The most important decisions made explicit in
that collaboration were: local-first instead of hosted; one strict package
contract and profile instead of a general policy builder; GPT-5.6 as advisory
rather than authoritative; whole-package fail-closed behavior; and copy-only
staging with deterministic evidence.

The current suite has 116 tests. Earlier false-green paths involving extra
staged data, post-proof payload mutation, stale proof after failure, crafted
decision authority, and tampered state artifacts are now regression-tested.
The full dated chronology is in `docs/CODEX_BUILD_LOG.md`; selective mechanical
adaptation from an earlier feasibility spike is disclosed in
`docs/PREEXISTING_WORK.md`.

### Working release evidence

The included synthetic hero package demonstrates the complete supported
contract:

- 12 stable object families;
- 28 content objects and 30 source-package members;
- original, access, and preservation derivative propagation;
- one casefold collision resolved by a human edit;
- one `campaña` to `campana` Meaning review resolved by a human edit;
- 28 complete forward and reverse map rows;
- unchanged source and equal staged payload hashes;
- complete declared-reference resolution and reverse dry run; and
- a valid Library of Congress `bagit` result.

One live transaction and three complete keyless replay transactions, including
a fresh-clone run, reached the verified result. Public-clone installation, all
116 tests, Ruff checks, and replay startup also pass from the repository's
`main` commit selected for submission.

### Design and potential impact

The product deliberately assigns authority visually and operationally. Green
means deterministic verification after required human decisions. Amber means
judgment is still required. Red means a mechanical blocker or failed invariant.
GPT prose remains neutral.

The potential value is a more inspectable handoff for a digital-preservation
specialist preparing a linked collection for repository ingest: instead of
trusting an opaque batch rename, the specialist can review one family-level
transaction, preserve declared relationships, and retain a source snapshot,
decision ledger, two-way path maps, verification report, human summary, and
BagIt manifests. This Build Week version demonstrates that bounded transaction;
it does not claim measured time savings, production readiness, universal
archival-workflow support, or semantic correctness.

### What makes the idea different

Name Atlas is not a generic bulk renamer and not a chat wrapper. Deterministic
code owns package parsing, identity, proposal generation, collision detection,
staging, and proof. GPT-5.6 is invoked only for a mechanically identified
meaning exception and cannot approve anything. The human owns semantic intent.
The output is not just a renamed tree: it is a copy-only staged package plus a
durable forward map, reverse map, decision ledger, and machine-readable proof.

### Challenges, learning, and next steps

The hardest engineering problem was not path generation; it was preventing a
plausible-looking result from becoming a false green. That required strict
read-back of staged control files and evidence artifacts, complete data-member
accounting, repeated source snapshots, independent collision comparisons, and
post-BagIt verification.

The central product learning was that AI authority should shrink as the claim
becomes more consequential. GPT-5.6 is valuable for framing a bounded ambiguity,
but the human must decide and deterministic code must prove the mechanical
result.

After Build Week, possible next steps are additional package adapters behind the
existing proposal boundary, practitioner testing, measured workflow studies,
and platform validation beyond the currently tested macOS judge path. Those are
future directions, not current support claims.

## Devpost custom-field draft

| Field | Draft value |
|---|---|
| Submitter Type | `[USER CONFIRM: Individual / Team of Individuals / Organization]` |
| Country of Residence | `[USER CONFIRM; do not infer for submission]` |
| Category | `Work & Productivity` |
| Code repository | `https://github.com/ModernBlueprints/reversible-name-atlas` |
| Testing link/instructions | `Clone the public repository on macOS with Python 3.11 and uv. Run uv sync --frozen, then uv run name-atlas demo --mode replay. Open http://127.0.0.1:8000. The included synthetic hero package and exact recorded GPT-5.6 card require no account or API key. Follow README → Hero workflow. Full checks: uv run pytest; uv run ruff check .; uv run ruff format --check .` |
| `/feedback` Session ID | `[USER ACTION REQUIRED]` |
| Plugin/developer-tool instructions | `Not applicable — submitted as a Work & Productivity standalone local browser application.` |

## Demo voice-over draft

Target: **401 spoken words** and approximately **2 minutes 40 seconds at 150
words per minute**. With a three-second opening and controlled pauses, target an
exported duration of **2 minutes 43–47 seconds**. The cues are in the shot list
and are not spoken.

For an archivist preparing a linked collection for repository ingest, renaming
is not a single-file operation. The same identity can appear in an original,
access and preservation derivatives, metadata rows, declared relationships, and
manifests. Change only one path, and the collection can become inconsistent.

Reversible Name Atlas is a local-first migration workbench. It previews the
complete supported transaction, asks for human judgment where mechanics stop,
then stages copies and proves the result. The source is never edited.

This Atlas has indexed a complete synthetic package: twelve stable families and
twenty-eight content objects. Every proposal adopts a canonical identifier,
retains a readable descriptor, adds the object role, and propagates through
declared metadata and derivative links. Before any copy is made, we see two
target collisions and one possible meaning loss.

Nine low-risk families are approved in one explicit action, with no GPT call.
The casefold collision is mechanical, so the model is skipped. I rename one
family descriptor `harbor-map-north`; its counterpart becomes valid, and I
approve it.

The remaining exception is `campaña` becoming `campana`: removing the Spanish ñ
may change meaning. Before GPT sees anything, Name Atlas exposes the outbound
packet: source and proposed paths, transformation trace, metadata, derivative
links, neighboring paths, and addressed evidence. No payload bytes or approval
fields leave the app. This is the visibly labeled replay of one real GPT-5.6
response, bound to this exact evidence fingerprint.

GPT-5.6 surfaces possible interpretations, possible meaning loss, uncertainty,
and one discriminating question. It cannot approve, verify, or choose a final
target. That authority stays with the human. I enter `campaign-poster` for the
complete family.

With all twelve families resolved, I run copy-only staging. Green appears only
after the source snapshot remains equal, all twenty-eight content hashes match,
all thirty staged data members are accounted for, rewritten references resolve,
targets pass exact, NFC, and casefold checks, forward and reverse maps are
complete inverses, reverse dry run succeeds, and Library of Congress BagIt
validation passes.

Codex with GPT-5.6 was the primary development environment. In one primary task,
it helped freeze the strict contract, implement vertical slices, run bounded
reviews in parallel, exercise the browser, reproduce proof defects, and turn
them into regression tests. The release suite has one hundred sixteen passing
tests.

The result is an inspectable migration package with human decisions,
deterministic proof, and durable forward, reverse, and verification evidence.
Reversible Name Atlas: refactor the collection; preserve every identity.

## Shot list and capture plan

| Time | Visual | Required state/action | Narration focus |
|---:|---|---|---|
| 0:00–0:07 | Opening title and runtime card | Replay mode; `Recorded GPT-5.6 response` and `gpt-5.6` visible | Identity and local-first premise |
| 0:07–0:17 | Smooth move to Atlas | Product navigation only | Linked-file problem |
| 0:17–0:30 | Atlas summary | 12 families, 28 objects, Collision 2, Meaning 1 visible | Complete package graph |
| 0:30–0:43 | `NA-0001` paths | Original/access/preservation proposals visible | `campaña` to `campana` risk |
| 0:43–0:52 | Decisions batch control | Click nine-family approval | Explicit routine authority |
| 0:52–1:08 | Collision card | Enter `harbor-map-north`; approve newly eligible counterpart | Mechanical edit; no GPT |
| 1:08–1:22 | Outbound-evidence panel | Show paths, trace, metadata, 14 evidence facts | Bounded model input |
| 1:22–1:27 | Live metrics insert | Use `docs/screenshots/02-live-metrics.png` | One real `gpt-5.6` call |
| 1:27–1:42 | Recorded decision card | Load record; show exact label and human question | GPT advisory role |
| 1:42–1:52 | Human controls | Enter `campaign-poster` and apply | Human owns semantic intent |
| 1:52–2:06 | Proof | Click stage; show verified claim, hashes, inverse maps, and BagIt | Deterministic result |
| 2:06–2:18 | README/build log | Show Built with Codex and key decisions | Specific Codex contribution |
| 2:18–2:37 | Terminal and history | Show `116 passed` and concise commits | Non-trivial implementation and regression work |
| 2:37–2:47 | Proof title and end card | Repository URL readable | Bounded value proposition |

Capture rules:

- record at 1920×1080 or 2560×1440 and export at 1920×1080;
- keep the browser at a recording-tested desktop width and zoom so all text is
  readable at normal YouTube playback;
- use only synthetic fixture content and the public repository;
- show no API key, account page, personal path, private notification, or raw
  provider/account identifier;
- do not perform another live request; use the exact recorded response;
- use no copyrighted music or third-party trademark footage;
- retain the natural product color authority: green deterministic, amber human,
  red blocker, neutral GPT;
- target 2:47 or shorter after editing, and verify the exported duration is
  strictly under three minutes; and
- play the final public YouTube upload from beginning to end with audio before
  submitting.

## Final submission checklist

- [x] Working project on the supported platform
- [x] Work & Productivity track selected
- [x] Public MIT-licensed repository
- [x] README setup, sample data, testing, Codex, key decisions, and GPT-5.6 use
- [x] Keyless replay judge path
- [x] Real GPT-5.6 evidence and exact recorded replay
- [x] Pre-existing-work disclosure
- [x] Public-clone install, tests, and replay startup
- [x] Six factual product screenshots
- [ ] User confirms Submitter Type
- [ ] User confirms Country of Residence
- [ ] User runs `/feedback` and supplies the generated Session ID
- [ ] User records or approves the final English voice-over
- [ ] Final video is strictly under three minutes
- [ ] Video is uploaded to YouTube as Public
- [ ] Public YouTube playback and audio are verified
- [ ] Devpost thumbnail is uploaded and rendered
- [ ] Devpost fields and custom answers are complete
- [ ] User-owned eligibility/personal attestations are confirmed
- [ ] Submitted Devpost receipt is captured before Wednesday 22 July 2026 at
      02:00 CEST

## Claim audit for the video and Devpost copy

Do not add claims of measured time savings, recurring speed, semantic
correctness, universal reversibility, production readiness, compliance,
Archivematica integration or certification, universal archival-workflow
support, institutional adoption, AI-lab usage, proven superiority over ordinary
Codex, absence of competitors, or a proven probability of winning.

Permitted positive claims must remain bounded to the observed supported
transaction, the exact live/replay evidence, the public judge path, and the
serialized proof described above.
