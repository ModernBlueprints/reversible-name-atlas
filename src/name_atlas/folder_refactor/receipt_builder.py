"""Pure builders for generic-folder proof artifacts and offline receipt views."""

from __future__ import annotations

import hashlib
import html
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath

from pydantic import BaseModel

from name_atlas.domain import PackageValidationResult
from name_atlas.folder_refactor.compiler import PlanCompilationError, compile_plan
from name_atlas.folder_refactor.contracts import (
    FolderAcceptedPlan,
    FolderInventory,
    FolderVerificationReport,
)
from name_atlas.folder_refactor.markdown_contracts import FolderReferenceGraph
from name_atlas.folder_refactor.planner_contracts import (
    FolderPlannerTurnInput,
    SubmitPlanCall,
    planner_history_item,
)
from name_atlas.folder_refactor.portable_artifacts import (
    ACCEPTED_PLAN_PATH,
    CHANGE_LEDGER_PATH,
    EVIDENCE_LEDGER_PATH,
    FORWARD_PATH_MAP_PATH,
    REFERENCE_GRAPH_PATH,
    REVERSE_PATH_MAP_PATH,
    SOURCE_SNAPSHOT_PATH,
    USER_REQUEST_PATH,
    VERIFICATION_REPORT_PATH,
    canonical_portable_json_bytes,
    render_folder_path_map,
)
from name_atlas.folder_refactor.portable_artifacts import (
    staged_data_commitment as portable_staged_data_commitment,
)
from name_atlas.folder_refactor.receipt_contracts import (
    PROOF_HTML_PATH,
    RECEIPT_CLAIM_BOUNDARIES,
    RECEIPT_JSON_PATH,
    FolderArtifactCommitment,
    FolderChangeEntry,
    FolderChangeLedger,
    FolderEvidenceLedger,
    FolderPathMapRow,
    FolderReceiptCore,
    FolderReceiptEnvelope,
    FolderStagedDataMember,
    FolderUserRequestArtifact,
    build_folder_receipt_envelope,
)
from name_atlas.folder_refactor.serialization import (
    canonical_json_bytes,
    canonical_sha256,
    request_fingerprint,
)

_FILE_URI = re.compile(
    r"(?<![A-Za-z0-9+.-])file:(?=/{1,3}|[A-Za-z]:[\\/])",
    flags=re.IGNORECASE,
)
_POSIX_ABSOLUTE_FRAGMENT = re.compile(r"(?<![A-Za-z0-9./])/(?!/)[^\s\"'<>|]+")
_WINDOWS_ABSOLUTE_FRAGMENT = re.compile(
    r"(?<![A-Za-z0-9])(?:[A-Za-z]:[\\/][^\s\"'<>|]+|"
    r"\\\\[^\\/\s\"'<>|]+[\\/][^\s\"'<>|]+)"
)

_FORWARD_MAP_FIELDS = (
    "file_id",
    "original_path",
    "result_path",
    "original_size",
    "original_sha256",
    "result_size",
    "result_sha256",
    "protected",
    "markdown_rewritten",
)
_EXCLUDED_RECEIPT_COMMITMENTS = frozenset(
    {
        RECEIPT_JSON_PATH,
        PROOF_HTML_PATH,
        "tagmanifest-sha256.txt",
    }
)
_REQUIRED_REPORT_CHECKS = frozenset(
    {
        "bagit_validation",
        "complete_file_bijection",
        "empty_directories_preserved",
        "payload_hashes_preserved",
        "protected_paths_preserved",
        "result_is_separate",
        "source_unchanged",
        "supported_markdown_links_resolve",
    }
)


class FolderReceiptBuilderError(ValueError):
    """Raised when portable authorities do not describe one exact transaction."""


@dataclass(frozen=True, slots=True)
class ObservedResultFile:
    """Exact observed relative path, size, and digest of one staged user file."""

    relative_path: str
    size: int
    sha256: str


def build_folder_user_request_artifact(request: str) -> FolderUserRequestArtifact:
    """Build one exact request artifact after enforcing path neutrality."""

    if contains_sender_local_path(request):
        raise FolderReceiptBuilderError(
            "The portable user request contains a sender-local absolute path."
        )
    return FolderUserRequestArtifact(
        request=request,
        request_fingerprint=request_fingerprint(request),
    )


def build_folder_path_rows_and_change_ledger(
    *,
    inventory: FolderInventory,
    accepted_plan: FolderAcceptedPlan,
    reference_graph: FolderReferenceGraph,
    observed_result_files: Mapping[str, ObservedResultFile],
) -> tuple[tuple[FolderPathMapRow, ...], FolderChangeLedger]:
    """Build the complete path rows and receipt-bound deterministic change ledger."""

    _require_portable_authorities(inventory, accepted_plan, reference_graph)
    source_by_id = {item.file_id: item for item in inventory.files}
    mapping_by_id = {item.file_id: item for item in accepted_plan.file_mappings}
    expected_ids = set(source_by_id)
    if set(observed_result_files) != expected_ids:
        missing = sorted(expected_ids - set(observed_result_files))
        unexpected = sorted(set(observed_result_files) - expected_ids)
        raise FolderReceiptBuilderError(
            "Observed result facts do not account for every source file exactly "
            f"once; missing={missing!r}, unexpected={unexpected!r}."
        )

    rewritten_by_source: dict[str, list[str]] = defaultdict(list)
    for reference in reference_graph.references:
        if reference.verification_status == "pending":
            raise FolderReceiptBuilderError(
                "A finalized change ledger cannot contain pending Markdown links."
            )
        if reference.verification_status == "rewritten":
            rewritten_by_source[reference.source_file_id].append(reference.reference_id)

    rows: list[FolderPathMapRow] = []
    entries: list[FolderChangeEntry] = []
    for source in inventory.files:
        mapping = mapping_by_id[source.file_id]
        observed = observed_result_files[source.file_id]
        rewritten_reference_ids = tuple(
            sorted(rewritten_by_source.get(source.file_id, ()))
        )
        markdown_rewritten = bool(rewritten_reference_ids)
        if observed.relative_path != mapping.target_path:
            raise FolderReceiptBuilderError(
                "Observed result path differs from the accepted target for "
                f"{source.relative_path!r}."
            )
        if not markdown_rewritten and (
            observed.size != source.size or observed.sha256 != source.sha256
        ):
            raise FolderReceiptBuilderError(
                "A file without an accepted Markdown rewrite changed bytes: "
                f"{source.relative_path!r}."
            )
        if markdown_rewritten and observed.sha256 == source.sha256:
            raise FolderReceiptBuilderError(
                "A declared Markdown rewrite did not change the observed bytes: "
                f"{source.relative_path!r}."
            )
        row = FolderPathMapRow(
            file_id=source.file_id,
            original_path=source.relative_path,
            result_path=observed.relative_path,
            original_size=source.size,
            original_sha256=source.sha256,
            result_size=observed.size,
            result_sha256=observed.sha256,
            protected=source.protected,
            markdown_rewritten=markdown_rewritten,
        )
        rows.append(row)
        entries.append(
            FolderChangeEntry(
                **row.model_dump(mode="python"),
                path_changed=source.relative_path != observed.relative_path,
                rewritten_reference_ids=rewritten_reference_ids,
                original_content_path=(
                    f"name-atlas/original-content/{source.file_id}.bin"
                    if markdown_rewritten
                    else None
                ),
            )
        )

    complete_rows = tuple(rows)
    ledger = FolderChangeLedger(
        source_commitment=inventory.source_commitment,
        request_fingerprint=accepted_plan.request_fingerprint,
        evidence_fingerprint=accepted_plan.evidence_fingerprint,
        accepted_plan_fingerprint=canonical_sha256(accepted_plan),
        reference_graph_fingerprint=canonical_sha256(reference_graph),
        entries=tuple(entries),
        file_count=len(entries),
        source_bytes=sum(entry.original_size for entry in entries),
        result_bytes=sum(entry.result_size for entry in entries),
        path_change_count=sum(entry.path_changed for entry in entries),
        protected_file_count=sum(entry.protected for entry in entries),
        supported_link_count=len(reference_graph.references),
        rewritten_link_count=sum(
            reference.verification_status == "rewritten"
            for reference in reference_graph.references
        ),
        rewritten_markdown_file_count=len(rewritten_by_source),
    )
    return complete_rows, ledger


def render_forward_path_map_csv(rows: Sequence[FolderPathMapRow]) -> bytes:
    """Render the fixed complete source-to-result CSV bytes."""

    complete_rows = _require_sorted_path_rows(rows)
    return render_folder_path_map(complete_rows, reverse=False)


def render_reverse_path_map_csv(rows: Sequence[FolderPathMapRow]) -> bytes:
    """Render the exact inverse result-to-source CSV bytes."""

    complete_rows = _require_sorted_path_rows(rows)
    return render_folder_path_map(complete_rows, reverse=True)


def compute_folder_staged_data_commitment(
    members: Sequence[FolderStagedDataMember],
) -> str:
    """Hash one uniquely path-sorted complete staged-data member list."""

    complete_members = tuple(members)
    paths = tuple(member.path for member in complete_members)
    if not complete_members:
        raise FolderReceiptBuilderError("Staged data must contain at least one file.")
    if paths != tuple(sorted(paths)) or len(paths) != len(set(paths)):
        raise FolderReceiptBuilderError(
            "Staged data members must be uniquely path-sorted."
        )
    return portable_staged_data_commitment(complete_members)


def build_folder_receipt(
    *,
    job_id: str,
    inventory: FolderInventory,
    user_request: FolderUserRequestArtifact,
    evidence_ledger: FolderEvidenceLedger,
    accepted_plan: FolderAcceptedPlan,
    reference_graph: FolderReferenceGraph,
    path_rows: Sequence[FolderPathMapRow],
    change_ledger: FolderChangeLedger,
    verification_report: FolderVerificationReport,
    artifact_commitments: Sequence[FolderArtifactCommitment],
    staged_data_members: Sequence[FolderStagedDataMember],
    staged_data_commitment: str,
    producer_bagit_validation: PackageValidationResult,
) -> FolderReceiptEnvelope:
    """Build one strict, acyclic, path-neutral generic-folder receipt."""

    rows = _require_sorted_path_rows(path_rows)
    commitments = tuple(artifact_commitments)
    staged_members = tuple(staged_data_members)
    portable_values = (
        inventory,
        user_request,
        evidence_ledger,
        accepted_plan,
        reference_graph,
        rows,
        change_ledger,
        verification_report,
        commitments,
        staged_members,
        producer_bagit_validation,
    )
    if contains_sender_local_path(portable_values):
        raise FolderReceiptBuilderError(
            "A portable receipt authority contains a sender-local absolute path."
        )

    _require_portable_authorities(inventory, accepted_plan, reference_graph)
    _require_evidence_bindings(
        job_id=job_id,
        inventory=inventory,
        user_request=user_request,
        evidence_ledger=evidence_ledger,
        accepted_plan=accepted_plan,
        reference_graph=reference_graph,
    )
    _require_change_bindings(
        inventory=inventory,
        accepted_plan=accepted_plan,
        reference_graph=reference_graph,
        path_rows=rows,
        change_ledger=change_ledger,
    )
    _require_report_bindings(
        inventory=inventory,
        accepted_plan=accepted_plan,
        change_ledger=change_ledger,
        verification_report=verification_report,
        staged_data_commitment=staged_data_commitment,
        producer_bagit_validation=producer_bagit_validation,
    )
    _require_staged_data_bindings(
        path_rows=rows,
        staged_data_members=staged_members,
        staged_data_commitment=staged_data_commitment,
    )
    _require_artifact_commitments(
        inventory=inventory,
        user_request=user_request,
        evidence_ledger=evidence_ledger,
        accepted_plan=accepted_plan,
        reference_graph=reference_graph,
        path_rows=rows,
        change_ledger=change_ledger,
        verification_report=verification_report,
        artifact_commitments=commitments,
    )

    core = FolderReceiptCore(
        job_id=job_id,
        source_commitment=inventory.source_commitment,
        source_file_count=len(inventory.files),
        source_directory_count=inventory.directory_count,
        source_bytes=inventory.total_bytes,
        request_fingerprint=user_request.request_fingerprint,
        evidence_fingerprint=evidence_ledger.evidence_fingerprint,
        accepted_plan_fingerprint=canonical_sha256(accepted_plan),
        reference_graph_fingerprint=canonical_sha256(reference_graph),
        model_alias=evidence_ledger.model_alias,
        provider_kind=evidence_ledger.provider_kind,
        returned_model_ids=evidence_ledger.returned_model_ids,
        store_false=evidence_ledger.store_false,
        clarification_question=evidence_ledger.clarification_question,
        clarification_answer=evidence_ledger.clarification_answer,
        staged_data_commitment=staged_data_commitment,
        staged_data_file_count=len(staged_members),
        staged_data_bytes=sum(member.size for member in staged_members),
        artifact_commitments=commitments,
        map_row_count=len(rows),
        path_change_count=change_ledger.path_change_count,
        supported_link_count=change_ledger.supported_link_count,
        rewritten_link_count=change_ledger.rewritten_link_count,
        rewritten_markdown_file_count=(change_ledger.rewritten_markdown_file_count),
        producer_bagit_validation=producer_bagit_validation,
        claim_boundaries=RECEIPT_CLAIM_BOUNDARIES,
    )
    return build_folder_receipt_envelope(core)


def render_folder_proof_html(
    envelope: FolderReceiptEnvelope,
    change_ledger: FolderChangeLedger,
    verification_report: FolderVerificationReport,
) -> bytes:
    """Render deterministic self-contained offline proof from finalized facts."""

    _require_receipt_view_bindings(envelope, change_ledger, verification_report)
    _require_receipt_provider_truthfulness(envelope.receipt)
    if contains_sender_local_path((envelope, change_ledger, verification_report)):
        raise FolderReceiptBuilderError(
            "Offline proof inputs contain a sender-local absolute path."
        )
    receipt = envelope.receipt
    origin_label, origin_detail = _provider_origin_copy(receipt)
    clarification = ""
    if receipt.clarification_question is not None:
        clarification = (
            "<section><h2>One clarification was used</h2>"
            f"<p><strong>Question:</strong> {_escape(receipt.clarification_question)}"
            "</p>"
            f"<p><strong>Answer:</strong> {_escape(receipt.clarification_answer or '')}"
            "</p></section>"
        )

    change_rows = "".join(
        "<tr>"
        f"<td><code>{_escape(entry.original_path)}</code></td>"
        f"<td><code>{_escape(entry.result_path)}</code></td>"
        f"<td>{_entry_status(entry)}</td>"
        "</tr>"
        for entry in change_ledger.entries
    )
    checks = "".join(
        "<li><strong>Passed:</strong> "
        f"{_escape(check.check_id)} — {_escape(check.detail)}</li>"
        for check in verification_report.checks
    )
    claims = "".join(f"<li>{_escape(claim)}</li>" for claim in receipt.claim_boundaries)
    bagit_messages = (
        "".join(
            f"<li>{_escape(message)}</li>"
            for message in receipt.producer_bagit_validation.messages
        )
        or "<li>No validator messages were reported.</li>"
    )

    rendered = (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>Name Atlas verified result</title>"
        "<style>"
        ":root{color-scheme:dark;--bg:#111418;--panel:#1c2127;"
        "--text:#f6f7f9;--muted:#abb3bf;--line:#39404a;--ok:#3dcc91}"
        "*{box-sizing:border-box}body{margin:0;background:var(--bg);"
        "color:var(--text);font:16px/1.5 system-ui,sans-serif}"
        "main{max-width:960px;margin:auto;padding:40px 24px 64px}"
        "h1{font-size:clamp(2rem,6vw,3.5rem);line-height:1.05;margin:.2em 0}"
        "h2{margin-top:0}.eyebrow{color:var(--ok);font-weight:700;"
        "letter-spacing:.08em;text-transform:uppercase}.lead{font-size:1.2rem}"
        "section,details{background:var(--panel);border:1px solid var(--line);"
        "border-radius:10px;margin:18px 0;padding:20px}summary{cursor:pointer;"
        "font-weight:700}table{border-collapse:collapse;width:100%;margin-top:16px}"
        "th,td{border-bottom:1px solid var(--line);padding:10px;text-align:left;"
        "vertical-align:top}code{overflow-wrap:anywhere;color:#d8e8ff}"
        ".facts li{margin:.45rem 0}.ok{color:var(--ok);font-weight:700}"
        ".muted{color:var(--muted)}@media(max-width:600px){main{padding:24px 14px}"
        "table{font-size:.85rem}th,td{padding:8px 4px}}"
        "</style></head><body><main>"
        '<p class="eyebrow">Reversible Name Atlas</p>'
        "<h1>Verified result</h1>"
        '<p class="lead">The separate folder was created from one complete plan, '
        "checked mechanically, and packaged with this portable proof.</p>"
        '<section><h2>What was proved</h2><ul class="facts">'
        f'<li><span class="ok">{receipt.source_file_count}</span> original '
        "files are present exactly once in the result.</li>"
        f'<li><span class="ok">{receipt.path_change_count}</span> file paths '
        "changed.</li>"
        f'<li><span class="ok">{receipt.rewritten_link_count}</span> supported '
        "Markdown links were updated and still resolve to the same files.</li>"
        "<li>The original folder remained unchanged during the verified "
        "transaction.</li>"
        "<li>Producer proof and BagIt validation passed.</li>"
        "</ul></section>"
        "<section><h2>How the plan was created</h2>"
        f"<p><strong>{_escape(origin_label)}</strong></p>"
        f"<p>{_escape(origin_detail)}</p></section>"
        f"{clarification}"
        "<details><summary>See changes</summary>"
        "<table><thead><tr><th>Original path</th><th>Result path</th>"
        f"<th>Change</th></tr></thead><tbody>{change_rows}</tbody></table>"
        "</details>"
        "<details><summary>Technical proof</summary>"
        "<p><strong>Receipt fingerprint:</strong> <code>"
        f"{_escape(envelope.receipt_fingerprint)}</code></p>"
        "<p><strong>Source commitment:</strong> <code>"
        f"{_escape(receipt.source_commitment)}</code></p>"
        "<p><strong>Staged-data commitment:</strong> <code>"
        f"{_escape(receipt.staged_data_commitment)}</code></p>"
        "<p><strong>Package contract:</strong> "
        f"{_escape(receipt.package_contract_id)}</p>"
        f"<p><strong>Naming profile:</strong> {_escape(receipt.profile_id)}</p>"
        f"<ul>{checks}</ul><h3>BagIt validator</h3><ul>{bagit_messages}</ul>"
        "<h3>Check this result again</h3>"
        "<p><code>uv run name-atlas verify-receipt RESULT_BAG</code></p>"
        "<h3>Recreate the original layout</h3>"
        "<p><code>uv run name-atlas restore-receipt RESULT_BAG "
        "RESTORE_DESTINATION</code></p>"
        "</details>"
        f"<details><summary>Claim boundaries</summary><ul>{claims}</ul></details>"
        '<p class="muted">This page contains no scripts, network resources, or '
        "sender-local absolute paths.</p>"
        "</main></body></html>\n"
    )
    return rendered.encode("utf-8")


def contains_sender_local_path(value: object) -> bool:
    """Detect absolute sender-local paths and file URIs in portable values."""

    if isinstance(value, BaseModel):
        return contains_sender_local_path(value.model_dump(mode="json"))
    if isinstance(value, Mapping):
        return any(contains_sender_local_path(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(contains_sender_local_path(item) for item in value)
    if not isinstance(value, str):
        return False
    candidate = value.strip()
    if not candidate:
        return False
    return (
        _FILE_URI.search(candidate) is not None
        or _POSIX_ABSOLUTE_FRAGMENT.search(candidate) is not None
        or _WINDOWS_ABSOLUTE_FRAGMENT.search(candidate) is not None
        or PurePosixPath(candidate).is_absolute()
        or PureWindowsPath(candidate).is_absolute()
    )


def _require_portable_authorities(
    inventory: FolderInventory,
    accepted_plan: FolderAcceptedPlan,
    reference_graph: FolderReferenceGraph,
) -> None:
    if accepted_plan.source_commitment != inventory.source_commitment:
        raise FolderReceiptBuilderError(
            "Accepted plan and source inventory commitments differ."
        )
    if reference_graph.source_commitment != inventory.source_commitment:
        raise FolderReceiptBuilderError(
            "Reference graph and source inventory commitments differ."
        )
    source_by_id = {item.file_id: item for item in inventory.files}
    mapping_by_id = {item.file_id: item for item in accepted_plan.file_mappings}
    if set(source_by_id) != set(mapping_by_id):
        raise FolderReceiptBuilderError(
            "Accepted plan does not account for every inventory file."
        )
    for file_id, source in source_by_id.items():
        mapping = mapping_by_id[file_id]
        if (
            mapping.original_path != source.relative_path
            or mapping.protected != source.protected
        ):
            raise FolderReceiptBuilderError(
                "Accepted plan changes a source identity or protection flag."
            )
    inventory_empty = tuple(item.relative_path for item in inventory.empty_directories)
    if accepted_plan.empty_directories != inventory_empty:
        raise FolderReceiptBuilderError(
            "Accepted plan does not preserve the exact empty-directory set."
        )
    for reference in reference_graph.references:
        source = source_by_id.get(reference.source_file_id)
        target = source_by_id.get(reference.target_file_id)
        if (
            source is None
            or target is None
            or reference.source_path != source.relative_path
            or reference.target_path != target.relative_path
        ):
            raise FolderReceiptBuilderError(
                "Reference graph does not bind to exact inventory identities."
            )


def _require_evidence_bindings(
    *,
    job_id: str,
    inventory: FolderInventory,
    user_request: FolderUserRequestArtifact,
    evidence_ledger: FolderEvidenceLedger,
    accepted_plan: FolderAcceptedPlan,
    reference_graph: FolderReferenceGraph,
) -> None:
    expected_plan_fingerprint = canonical_sha256(accepted_plan)
    if evidence_ledger.job_id != job_id:
        raise FolderReceiptBuilderError("Evidence ledger job ID does not match.")
    if user_request.request_fingerprint != request_fingerprint(user_request.request):
        raise FolderReceiptBuilderError("User-request artifact is not exact.")
    if not (
        user_request.request_fingerprint
        == evidence_ledger.request_fingerprint
        == accepted_plan.request_fingerprint
    ):
        raise FolderReceiptBuilderError(
            "Request fingerprints differ across the receipt authorities."
        )
    if evidence_ledger.source_commitment != inventory.source_commitment:
        raise FolderReceiptBuilderError(
            "Evidence ledger source commitment does not match the inventory."
        )
    if not (evidence_ledger.evidence_fingerprint == accepted_plan.evidence_fingerprint):
        raise FolderReceiptBuilderError(
            "Accepted plan is not bound to the committed planner evidence."
        )
    if evidence_ledger.accepted_plan_fingerprint != expected_plan_fingerprint:
        raise FolderReceiptBuilderError(
            "Evidence ledger is not bound to the accepted plan."
        )
    if evidence_ledger.request_scope != accepted_plan.request_scope:
        raise FolderReceiptBuilderError(
            "Evidence ledger and accepted plan request scopes differ."
        )
    _require_submitted_plan_compilation(
        inventory=inventory,
        user_request=user_request,
        evidence_ledger=evidence_ledger,
        accepted_plan=accepted_plan,
        reference_graph=reference_graph,
    )
    _require_provider_truthfulness(evidence_ledger)


def _require_submitted_plan_compilation(
    *,
    inventory: FolderInventory,
    user_request: FolderUserRequestArtifact,
    evidence_ledger: FolderEvidenceLedger,
    accepted_plan: FolderAcceptedPlan,
    reference_graph: FolderReferenceGraph,
) -> None:
    """Reproduce every committed plan outcome from evidence known at that turn."""

    submissions = tuple(
        (turn.response_turn, call)
        for turn in evidence_ledger.observable_turns
        for call in turn.tool_calls
        if isinstance(call, SubmitPlanCall)
    )
    if len(submissions) != evidence_ledger.plan_submission_count:
        raise FolderReceiptBuilderError(
            "Observable plan submissions do not match the evidence ledger."
        )
    failures = {
        failure.submission_number: failure
        for failure in evidence_ledger.compiler_failures
    }
    for submission_number, (response_turn, call) in enumerate(submissions, start=1):
        turn = evidence_ledger.observable_turns[response_turn - 1]
        try:
            turn_input = FolderPlannerTurnInput.model_validate_json(
                canonical_json_bytes(turn.input_payload),
                strict=True,
            )
        except ValueError as exc:
            raise FolderReceiptBuilderError(
                "Observable plan submission has an invalid bound input."
            ) from exc
        expected_records = tuple(
            record
            for record in evidence_ledger.evidence_records
            if record.response_turn < response_turn
        )
        expected_prior_turns = tuple(
            planner_history_item(item)
            for item in evidence_ledger.observable_turns[: response_turn - 1]
        )
        expected_failures = tuple(
            failure
            for failure in evidence_ledger.compiler_failures
            if failure.submission_number < submission_number
        )
        if not (
            turn_input.job_id == evidence_ledger.job_id
            and turn_input.response_turn == response_turn
            and turn_input.provider_kind == evidence_ledger.provider_kind
            and turn_input.request == user_request.request
            and turn_input.source_commitment == inventory.source_commitment
            and turn_input.evidence_ledger.initial_evidence
            == evidence_ledger.initial_evidence
            and turn_input.evidence_ledger.initial_evidence_bytes
            == evidence_ledger.initial_evidence_bytes
            and turn_input.evidence_ledger.records == expected_records
            and turn_input.prior_turns == expected_prior_turns
            and turn_input.compiler_failures == expected_failures
        ):
            raise FolderReceiptBuilderError(
                "Observable plan submission input differs from committed history."
            )
        known_evidence_ids = {"initial_inventory"} | {
            record.fingerprint for record in turn_input.evidence_ledger.records
        }
        try:
            compiled = compile_plan(
                inventory,
                user_request.request,
                call.plan,
                known_evidence_ids=known_evidence_ids,
                evidence_fingerprint=(turn_input.evidence_ledger.evidence_fingerprint),
                reference_graph=reference_graph,
            )
        except PlanCompilationError as exc:
            failure = failures.get(submission_number)
            if failure is None or failure.code != exc.code:
                raise FolderReceiptBuilderError(
                    "Committed plan rejection does not match deterministic "
                    f"compilation at submission {submission_number}."
                ) from exc
            continue
        if submission_number != len(submissions):
            raise FolderReceiptBuilderError(
                "A non-final committed plan submission compiles successfully."
            )
        if submission_number in failures:
            raise FolderReceiptBuilderError(
                "The final committed plan both compiles and records a rejection."
            )
        if compiled != accepted_plan:
            raise FolderReceiptBuilderError(
                "Accepted plan is not the compiled final observable submission."
            )

    if set(failures) != set(range(1, len(submissions))):
        raise FolderReceiptBuilderError(
            "Compiler-failure sequence does not match prior plan submissions."
        )


def _require_provider_truthfulness(evidence_ledger: FolderEvidenceLedger) -> None:
    returned = tuple(
        dict.fromkeys(
            turn.returned_model
            for turn in evidence_ledger.observable_turns
            if turn.returned_model is not None
        )
    )
    if evidence_ledger.returned_model_ids != returned:
        raise FolderReceiptBuilderError(
            "Receipt provider model IDs differ from observable planner turns."
        )
    if any(
        turn.provider_kind != evidence_ledger.provider_kind
        for turn in evidence_ledger.observable_turns
    ):
        raise FolderReceiptBuilderError(
            "Receipt planner turns contain mixed provider origins."
        )
    if evidence_ledger.provider_kind == "live":
        if evidence_ledger.store_false is not True or not returned:
            raise FolderReceiptBuilderError(
                "Live planner evidence requires store=false and returned model IDs."
            )
    elif evidence_ledger.provider_kind == "recorded_replay":
        if evidence_ledger.store_false is not None or not returned:
            raise FolderReceiptBuilderError(
                "Recorded replay evidence requires preserved model IDs and no "
                "claim about a new API store setting."
            )
    elif evidence_ledger.store_false is not None or returned:
        raise FolderReceiptBuilderError(
            "Deterministic evidence cannot claim provider model or API facts."
        )


def _require_change_bindings(
    *,
    inventory: FolderInventory,
    accepted_plan: FolderAcceptedPlan,
    reference_graph: FolderReferenceGraph,
    path_rows: tuple[FolderPathMapRow, ...],
    change_ledger: FolderChangeLedger,
) -> None:
    if not (
        change_ledger.source_commitment == inventory.source_commitment
        and change_ledger.request_fingerprint == accepted_plan.request_fingerprint
        and change_ledger.evidence_fingerprint == accepted_plan.evidence_fingerprint
        and change_ledger.accepted_plan_fingerprint == canonical_sha256(accepted_plan)
        and change_ledger.reference_graph_fingerprint
        == canonical_sha256(reference_graph)
    ):
        raise FolderReceiptBuilderError(
            "Change ledger does not bind to the source, plan, and reference graph."
        )
    if len(path_rows) != len(change_ledger.entries):
        raise FolderReceiptBuilderError(
            "Path rows and change-ledger entry counts differ."
        )
    for row, entry in zip(path_rows, change_ledger.entries, strict=True):
        comparable = entry.model_dump(
            mode="python",
            include=set(_FORWARD_MAP_FIELDS),
        )
        if row.model_dump(mode="python") != comparable:
            raise FolderReceiptBuilderError(
                "Path rows and change-ledger file facts differ."
            )
    rewritten_ids = tuple(
        sorted(
            reference.reference_id
            for reference in reference_graph.references
            if reference.verification_status == "rewritten"
        )
    )
    ledger_rewritten_ids = tuple(
        sorted(
            reference_id
            for entry in change_ledger.entries
            for reference_id in entry.rewritten_reference_ids
        )
    )
    if rewritten_ids != ledger_rewritten_ids:
        raise FolderReceiptBuilderError(
            "Change ledger does not account for every rewritten reference."
        )


def _require_report_bindings(
    *,
    inventory: FolderInventory,
    accepted_plan: FolderAcceptedPlan,
    change_ledger: FolderChangeLedger,
    verification_report: FolderVerificationReport,
    staged_data_commitment: str,
    producer_bagit_validation: PackageValidationResult,
) -> None:
    if not producer_bagit_validation.valid:
        raise FolderReceiptBuilderError(
            "A finalized receipt requires successful producer BagIt validation."
        )
    expected = {
        "source_commitment": inventory.source_commitment,
        "request_fingerprint": accepted_plan.request_fingerprint,
        "accepted_plan_fingerprint": canonical_sha256(accepted_plan),
        "result_folder_name": accepted_plan.result_folder_name,
        "staged_data_commitment": staged_data_commitment,
        "file_count": change_ledger.file_count,
        "path_change_count": change_ledger.path_change_count,
        "protected_file_count": change_ledger.protected_file_count,
        "empty_directory_count": len(inventory.empty_directories),
        "supported_link_count": change_ledger.supported_link_count,
        "rewritten_link_count": change_ledger.rewritten_link_count,
        "rewritten_markdown_file_count": (change_ledger.rewritten_markdown_file_count),
    }
    for field_name, value in expected.items():
        if getattr(verification_report, field_name) != value:
            raise FolderReceiptBuilderError(
                f"Verification report {field_name} differs from committed facts."
            )
    check_ids = tuple(check.check_id for check in verification_report.checks)
    if len(check_ids) != len(set(check_ids)):
        raise FolderReceiptBuilderError("Verification report check IDs must be unique.")
    missing = sorted(_REQUIRED_REPORT_CHECKS - set(check_ids))
    if missing:
        raise FolderReceiptBuilderError(
            f"Verification report omits required checks: {missing!r}."
        )


def _require_staged_data_bindings(
    *,
    path_rows: tuple[FolderPathMapRow, ...],
    staged_data_members: tuple[FolderStagedDataMember, ...],
    staged_data_commitment: str,
) -> None:
    if compute_folder_staged_data_commitment(staged_data_members) != (
        staged_data_commitment
    ):
        raise FolderReceiptBuilderError(
            "Staged-data commitment does not match the complete member list."
        )
    expected = tuple(
        (row.result_path, row.result_size, row.result_sha256)
        for row in sorted(path_rows, key=lambda item: item.result_path)
    )
    observed = tuple(
        (member.path, member.size, member.sha256) for member in staged_data_members
    )
    if observed != expected:
        raise FolderReceiptBuilderError(
            "Staged-data members do not equal the complete accepted path map."
        )


def _require_artifact_commitments(
    *,
    inventory: FolderInventory,
    user_request: FolderUserRequestArtifact,
    evidence_ledger: FolderEvidenceLedger,
    accepted_plan: FolderAcceptedPlan,
    reference_graph: FolderReferenceGraph,
    path_rows: tuple[FolderPathMapRow, ...],
    change_ledger: FolderChangeLedger,
    verification_report: FolderVerificationReport,
    artifact_commitments: tuple[FolderArtifactCommitment, ...],
) -> None:
    paths = tuple(item.path for item in artifact_commitments)
    if paths != tuple(sorted(paths)) or len(paths) != len(set(paths)):
        raise FolderReceiptBuilderError(
            "Artifact commitments must be uniquely path-sorted."
        )
    if set(paths) & _EXCLUDED_RECEIPT_COMMITMENTS:
        raise FolderReceiptBuilderError(
            "Receipt commitments contain a circular or post-finalization artifact."
        )
    by_path = {item.path: item for item in artifact_commitments}
    expected_bytes = {
        ACCEPTED_PLAN_PATH: canonical_portable_json_bytes(accepted_plan),
        CHANGE_LEDGER_PATH: canonical_portable_json_bytes(change_ledger),
        EVIDENCE_LEDGER_PATH: canonical_portable_json_bytes(evidence_ledger),
        FORWARD_PATH_MAP_PATH: render_forward_path_map_csv(path_rows),
        REFERENCE_GRAPH_PATH: canonical_portable_json_bytes(reference_graph),
        REVERSE_PATH_MAP_PATH: render_reverse_path_map_csv(path_rows),
        SOURCE_SNAPSHOT_PATH: canonical_portable_json_bytes(inventory),
        USER_REQUEST_PATH: canonical_portable_json_bytes(user_request),
        VERIFICATION_REPORT_PATH: canonical_portable_json_bytes(verification_report),
    }
    for path, data in expected_bytes.items():
        _require_exact_commitment(by_path.get(path), path, data)

    source_by_id = {item.file_id: item for item in inventory.files}
    expected_original_paths = {
        entry.original_content_path
        for entry in change_ledger.entries
        if entry.original_content_path is not None
    }
    committed_original_paths = {
        path for path in paths if path.startswith("name-atlas/original-content/")
    }
    if committed_original_paths != expected_original_paths:
        raise FolderReceiptBuilderError(
            "Original-content commitments do not match rewritten Markdown files."
        )
    for entry in change_ledger.entries:
        if entry.original_content_path is None:
            continue
        source = source_by_id[entry.file_id]
        commitment = by_path[entry.original_content_path]
        if commitment.size != source.size or commitment.sha256 != source.sha256:
            raise FolderReceiptBuilderError(
                "Original-content commitment does not match the source Markdown "
                f"bytes for {source.relative_path!r}."
            )


def _require_exact_commitment(
    commitment: FolderArtifactCommitment | None,
    path: str,
    data: bytes,
) -> None:
    if commitment is None:
        raise FolderReceiptBuilderError(
            f"Receipt omits authoritative artifact {path!r}."
        )
    if (
        commitment.size != len(data)
        or commitment.sha256 != hashlib.sha256(data).hexdigest()
    ):
        raise FolderReceiptBuilderError(
            f"Raw artifact commitment does not match exact bytes for {path!r}."
        )


def _require_receipt_view_bindings(
    envelope: FolderReceiptEnvelope,
    change_ledger: FolderChangeLedger,
    verification_report: FolderVerificationReport,
) -> None:
    receipt = envelope.receipt
    expected = {
        "source_commitment": change_ledger.source_commitment,
        "request_fingerprint": change_ledger.request_fingerprint,
        "evidence_fingerprint": change_ledger.evidence_fingerprint,
        "accepted_plan_fingerprint": change_ledger.accepted_plan_fingerprint,
        "reference_graph_fingerprint": change_ledger.reference_graph_fingerprint,
        "map_row_count": change_ledger.file_count,
        "path_change_count": change_ledger.path_change_count,
        "supported_link_count": change_ledger.supported_link_count,
        "rewritten_link_count": change_ledger.rewritten_link_count,
        "rewritten_markdown_file_count": (change_ledger.rewritten_markdown_file_count),
    }
    for field_name, value in expected.items():
        if getattr(receipt, field_name) != value:
            raise FolderReceiptBuilderError(
                "Receipt and change ledger do not describe one finalized "
                f"transaction: {field_name}."
            )
    report_expected = {
        "source_commitment": receipt.source_commitment,
        "request_fingerprint": receipt.request_fingerprint,
        "accepted_plan_fingerprint": receipt.accepted_plan_fingerprint,
        "staged_data_commitment": receipt.staged_data_commitment,
        "file_count": receipt.map_row_count,
        "path_change_count": receipt.path_change_count,
        "supported_link_count": receipt.supported_link_count,
        "rewritten_link_count": receipt.rewritten_link_count,
        "rewritten_markdown_file_count": receipt.rewritten_markdown_file_count,
    }
    for field_name, value in report_expected.items():
        if getattr(verification_report, field_name) != value:
            raise FolderReceiptBuilderError(
                "Receipt and verification report do not describe one finalized "
                f"transaction: {field_name}."
            )
    by_path = {item.path: item for item in receipt.artifact_commitments}
    _require_exact_commitment(
        by_path.get(CHANGE_LEDGER_PATH),
        CHANGE_LEDGER_PATH,
        canonical_portable_json_bytes(change_ledger),
    )
    _require_exact_commitment(
        by_path.get(VERIFICATION_REPORT_PATH),
        VERIFICATION_REPORT_PATH,
        canonical_portable_json_bytes(verification_report),
    )


def _require_sorted_path_rows(
    rows: Sequence[FolderPathMapRow],
) -> tuple[FolderPathMapRow, ...]:
    complete_rows = tuple(rows)
    paths = tuple(row.original_path for row in complete_rows)
    file_ids = tuple(row.file_id for row in complete_rows)
    result_paths = tuple(row.result_path for row in complete_rows)
    if not complete_rows:
        raise FolderReceiptBuilderError("Path map must contain at least one row.")
    if paths != tuple(sorted(paths)) or len(paths) != len(set(paths)):
        raise FolderReceiptBuilderError(
            "Path rows must be uniquely source-path sorted."
        )
    if len(file_ids) != len(set(file_ids)) or len(result_paths) != len(
        set(result_paths)
    ):
        raise FolderReceiptBuilderError(
            "Path rows must contain unique file IDs and result paths."
        )
    return complete_rows


def _provider_origin_copy(core: FolderReceiptCore) -> tuple[str, str]:
    if core.provider_kind == "live":
        models = ", ".join(core.returned_model_ids)
        return (
            "Live GPT-5.6 planning run",
            "The Responses API returned model identifier(s) "
            f"{models}. The request used store=false; standard OpenAI API "
            "data-retention policies may still apply.",
        )
    if core.provider_kind == "recorded_replay":
        models = ", ".join(core.returned_model_ids)
        return (
            "Recorded GPT-5.6 planning run",
            "This result reproduces a committed observable planning record "
            f"whose original provider returned {models}. Viewing or verifying "
            "it makes no new API call and requires no API key.",
        )
    return (
        "Deterministic local planning run",
        "This result used a deterministic planner test double and does not "
        "claim a live or recorded GPT-5.6 provider response.",
    )


def _require_receipt_provider_truthfulness(core: FolderReceiptCore) -> None:
    if core.provider_kind == "live":
        if core.store_false is not True or not core.returned_model_ids:
            raise FolderReceiptBuilderError(
                "Live receipt evidence requires store=false and returned model IDs."
            )
        return
    if core.provider_kind == "recorded_replay":
        if core.store_false is not None or not core.returned_model_ids:
            raise FolderReceiptBuilderError(
                "Recorded receipt evidence requires preserved model IDs without "
                "claiming a new API store setting."
            )
        return
    if core.store_false is not None or core.returned_model_ids:
        raise FolderReceiptBuilderError(
            "Deterministic receipt evidence cannot claim provider API facts."
        )


def _entry_status(entry: FolderChangeEntry) -> str:
    labels: list[str] = []
    if entry.protected:
        labels.append("Protected and unchanged")
    elif entry.path_changed:
        labels.append("Path changed")
    else:
        labels.append("Path unchanged")
    if entry.markdown_rewritten:
        labels.append(f"{len(entry.rewritten_reference_ids)} supported link(s) updated")
    return _escape("; ".join(labels))


def _escape(value: object) -> str:
    return html.escape(str(value), quote=True)
