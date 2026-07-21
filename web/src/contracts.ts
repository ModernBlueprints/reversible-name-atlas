export type Journey = "organize" | "apply";
export type ViewSide = "current" | "proposed";

export type MemberKind = "regular_file" | "empty_directory";

export type ChangeClassification =
  | "unchanged"
  | "renamed"
  | "moved"
  | "moved_and_renamed"
  | "protected"
  | "empty_directory";

export interface FolderPlanTreeMember {
  member_id: string;
  member_kind: MemberKind;
  relative_path: string;
  directory_prefixes: string[];
  protected: boolean;
}

export interface FolderPlanLinkEffect {
  reference_id: string;
  source_member_id: string;
  target_member_id: string;
  current_source_path: string;
  current_target_path: string;
  proposed_source_path: string;
  proposed_target_path: string;
  original_destination: string;
  proposed_destination: string;
  status: "unchanged" | "rewritten";
}

export interface FolderPlanMemberChange {
  member_id: string;
  member_kind: MemberKind;
  current_relative_path: string;
  proposed_relative_path: string;
  change_classification: ChangeClassification;
  protected: boolean;
  authority_source: "gpt_plan" | "change_file" | "protected";
  rationale: string;
  link_updated: boolean;
  supported_link_effect_ids: string[];
}

export interface FolderPlanFinding {
  finding_id: string;
  severity: "collision" | "blocker";
  detail: string;
  member_ids: string[];
}

export interface FolderPlanPreviewCounts {
  file_count: number;
  empty_directory_count: number;
  changed_path_count: number;
  renamed_count: number;
  moved_count: number;
  link_count: number;
  link_updated_count: number;
  protected_count: number;
  blocker_count: number;
}

export interface FolderPlanPreviewV1 {
  schema_version: "folder-plan-preview.v1";
  job_id: string;
  expected_job_revision: number;
  proposal_revision: number;
  proposal_basis:
    | "fresh_gpt_plan"
    | "imported_change_file"
    | "gpt_derivative";
  source_commitment: string;
  imported_change_file_fingerprint: string | null;
  match_report_fingerprint: string | null;
  immediate_parent_candidate_fingerprint: string | null;
  current_tree_members: FolderPlanTreeMember[];
  proposed_tree_members: FolderPlanTreeMember[];
  member_changes: FolderPlanMemberChange[];
  supported_link_effects: FolderPlanLinkEffect[];
  collision_findings: FolderPlanFinding[];
  blocker_findings: FolderPlanFinding[];
  counts: FolderPlanPreviewCounts;
  compiled_candidate_fingerprint: string;
  preview_fingerprint: string;
}

export interface FolderPlanRevisionDeltaEntryV1 {
  member_id: string;
  previous_path: string;
  current_path: string;
}

export interface FolderPlanRevisionDeltaV1 {
  schema_version: "folder-plan-revision-delta.v1";
  job_id: string;
  proposal_revision_before: number;
  proposal_revision_after: number;
  base_candidate_fingerprint: string;
  base_preview_fingerprint: string;
  current_candidate_fingerprint: string;
  current_preview_fingerprint: string;
  previous_result_folder_name: string;
  current_result_folder_name: string;
  entries: FolderPlanRevisionDeltaEntryV1[];
  delta_fingerprint: string;
}

export interface ReviewDisplayStatus {
  job_id: string;
  lifecycle: string;
  job_revision: number;
  proposal_revision: number;
  candidate_fingerprint: string;
  preview_fingerprint: string;
  revision_available: boolean;
  revision_attempts_remaining: number;
  revision_failure: string | null;
  latest_proposal_delta: FolderPlanRevisionDeltaV1 | null;
  action_lock_reason?: string | null;
}

export interface ReviewStatus extends ReviewDisplayStatus {
  done_url: string | null;
  output_parent: string;
  result_folder_name: string;
}

export interface AcceptanceBindingPayload {
  candidate_fingerprint: string;
  expected_revision: number;
  idempotency_key: string;
  preview_fingerprint: string;
}

export interface AcceptancePayload extends AcceptanceBindingPayload {
  output_parent: string;
  result_folder_name: string;
}

export interface RevisionPayload {
  candidate_fingerprint: string;
  expected_revision: number;
  idempotency_key: string;
  instruction: string;
  preview_fingerprint: string;
}

export interface KeepProposalPayload {
  candidate_fingerprint: string;
  expected_revision: number;
  idempotency_key: string;
  preview_fingerprint: string;
}

const SHA256 = /^[a-f0-9]{64}$/;
const JOB_ID = /^[a-f0-9]{32}$/;

export function assertPreview(
  value: unknown,
  expectedJobId: string,
): asserts value is FolderPlanPreviewV1 {
  if (!isRecord(value)) {
    throw new Error("Foldweave returned an invalid preview object.");
  }
  if (
    value.schema_version !== "folder-plan-preview.v1" ||
    value.job_id !== expectedJobId ||
    !matchesPattern(value.job_id, JOB_ID) ||
    (value.proposal_basis !== "fresh_gpt_plan" &&
      value.proposal_basis !== "imported_change_file" &&
      value.proposal_basis !== "gpt_derivative") ||
    !matchesPattern(value.source_commitment, SHA256) ||
    !isNullableFingerprint(value.imported_change_file_fingerprint) ||
    !isNullableFingerprint(value.match_report_fingerprint) ||
    !isNullableFingerprint(value.immediate_parent_candidate_fingerprint) ||
    !matchesPattern(value.compiled_candidate_fingerprint, SHA256) ||
    !matchesPattern(value.preview_fingerprint, SHA256) ||
    !Number.isInteger(value.expected_job_revision) ||
    (value.expected_job_revision as number) < 0 ||
    !Number.isInteger(value.proposal_revision) ||
    (value.proposal_revision as number) < 0 ||
    (value.proposal_revision as number) > 2 ||
    !Array.isArray(value.current_tree_members) ||
    !Array.isArray(value.proposed_tree_members) ||
    !Array.isArray(value.member_changes) ||
    !Array.isArray(value.supported_link_effects) ||
    !Array.isArray(value.collision_findings) ||
    !Array.isArray(value.blocker_findings) ||
    !isRecord(value.counts)
  ) {
    throw new Error("Foldweave returned an incomplete preview contract.");
  }
  if (
    value.proposal_basis === "imported_change_file" &&
    (value.imported_change_file_fingerprint === null ||
      value.match_report_fingerprint === null)
  ) {
    throw new Error("An imported Foldweave proposal requires exact matching evidence.");
  }
  if (
    (value.imported_change_file_fingerprint === null) !==
    (value.match_report_fingerprint === null)
  ) {
    throw new Error(
      "Foldweave Change File identity and matching evidence must remain paired.",
    );
  }
  if (
    value.proposal_basis === "fresh_gpt_plan" &&
    value.imported_change_file_fingerprint !== null
  ) {
    throw new Error("A fresh Foldweave proposal cannot retain imported matching evidence.");
  }
}

export function assertStatus(
  value: unknown,
  expectedJobId: string,
): asserts value is ReviewStatus {
  if (isRecord(value) && value.lifecycle === "stale") {
    if (
      value.job_id !== expectedJobId ||
      !matchesPattern(value.job_id, JOB_ID) ||
      !Number.isInteger(value.job_revision) ||
      !Number.isInteger(value.proposal_revision) ||
      !matchesPattern(value.candidate_fingerprint, SHA256) ||
      !matchesPattern(value.preview_fingerprint, SHA256) ||
      value.revision_available !== false ||
      value.revision_attempts_remaining !== 0 ||
      value.revision_failure !== null ||
      typeof value.action_lock_reason !== "string" ||
      value.action_lock_reason.length === 0
    ) {
      throw new Error("Foldweave returned an incomplete stale review status.");
    }
  } else {
    assertReviewDisplayStatus(value, expectedJobId);
  }
  const localValue = value as ReviewDisplayStatus & Record<string, unknown>;
  assertLatestProposalDelta(localValue.latest_proposal_delta, localValue);
  if (
    typeof localValue.output_parent !== "string" ||
    localValue.output_parent.length === 0 ||
    typeof localValue.result_folder_name !== "string" ||
    localValue.result_folder_name.length === 0
  ) {
    throw new Error("Foldweave returned an incomplete local review status.");
  }
}

export function assertReviewDisplayStatus(
  value: unknown,
  expectedJobId: string,
): asserts value is ReviewDisplayStatus {
  if (!isRecord(value)) {
    throw new Error("Foldweave returned an invalid review status.");
  }
  if (
    value.job_id !== expectedJobId ||
    !matchesPattern(value.job_id, JOB_ID) ||
    value.lifecycle !== "reviewing" ||
    !Number.isInteger(value.job_revision) ||
    !Number.isInteger(value.proposal_revision) ||
    !matchesPattern(value.candidate_fingerprint, SHA256) ||
    !matchesPattern(value.preview_fingerprint, SHA256) ||
    typeof value.revision_available !== "boolean" ||
    typeof value.revision_attempts_remaining !== "number" ||
    !Number.isInteger(value.revision_attempts_remaining) ||
    value.revision_attempts_remaining < 0 ||
    value.revision_attempts_remaining > 2 ||
    (value.revision_failure !== null &&
      typeof value.revision_failure !== "string")
  ) {
    throw new Error("Foldweave returned an incomplete review status.");
  }
  assertLatestProposalDelta(value.latest_proposal_delta, {
    job_id: value.job_id,
    proposal_revision: value.proposal_revision,
    candidate_fingerprint: value.candidate_fingerprint,
    preview_fingerprint: value.preview_fingerprint,
  });
}

interface RevisionDeltaStatusBinding {
  job_id: unknown;
  proposal_revision: unknown;
  candidate_fingerprint: unknown;
  preview_fingerprint: unknown;
  result_folder_name?: unknown;
}

export function assertLatestProposalDelta(
  value: unknown,
  status: RevisionDeltaStatusBinding,
): asserts value is FolderPlanRevisionDeltaV1 | null {
  if (value === null) {
    if (status.proposal_revision !== 0) {
      throw new Error(
        "Foldweave omitted the durable delta for a revised proposal.",
      );
    }
    return;
  }
  if (!isRecord(value)) {
    throw new Error("Foldweave returned an invalid proposal delta.");
  }
  const expectedKeys = [
    "base_candidate_fingerprint",
    "base_preview_fingerprint",
    "current_candidate_fingerprint",
    "current_preview_fingerprint",
    "current_result_folder_name",
    "delta_fingerprint",
    "entries",
    "job_id",
    "previous_result_folder_name",
    "proposal_revision_after",
    "proposal_revision_before",
    "schema_version",
  ];
  if (
    !sameStrings(Object.keys(value).sort(compareStrings), expectedKeys) ||
    value.schema_version !== "folder-plan-revision-delta.v1" ||
    value.job_id !== status.job_id ||
    !matchesPattern(value.job_id, JOB_ID) ||
    !Number.isInteger(value.proposal_revision_before) ||
    !Number.isInteger(value.proposal_revision_after) ||
    (value.proposal_revision_before as number) < 0 ||
    (value.proposal_revision_after as number) < 1 ||
    (value.proposal_revision_after as number) > 2 ||
    (value.proposal_revision_after as number) !==
      (value.proposal_revision_before as number) + 1 ||
    value.proposal_revision_after !== status.proposal_revision ||
    !matchesPattern(value.base_candidate_fingerprint, SHA256) ||
    !matchesPattern(value.base_preview_fingerprint, SHA256) ||
    value.current_candidate_fingerprint !== status.candidate_fingerprint ||
    !matchesPattern(value.current_candidate_fingerprint, SHA256) ||
    value.current_preview_fingerprint !== status.preview_fingerprint ||
    !matchesPattern(value.current_preview_fingerprint, SHA256) ||
    (status.result_folder_name !== undefined &&
      value.current_result_folder_name !== status.result_folder_name) ||
    value.base_candidate_fingerprint === value.current_candidate_fingerprint ||
    value.base_preview_fingerprint === value.current_preview_fingerprint ||
    !isResultFolderName(value.previous_result_folder_name) ||
    !isResultFolderName(value.current_result_folder_name) ||
    !Array.isArray(value.entries) ||
    value.entries.length > 500 ||
    !matchesPattern(value.delta_fingerprint, SHA256)
  ) {
    throw new Error(
      "Foldweave returned a proposal delta that does not match the durable review.",
    );
  }
  const memberIds = new Set<string>();
  let previousMemberId: string | null = null;
  for (const entry of value.entries) {
    if (
      !isRecord(entry) ||
      !sameStrings(Object.keys(entry).sort(compareStrings), [
        "current_path",
        "member_id",
        "previous_path",
      ]) ||
      !matchesPattern(entry.member_id, SHA256) ||
      memberIds.has(entry.member_id) ||
      (previousMemberId !== null &&
        compareStrings(previousMemberId, entry.member_id) >= 0) ||
      !isSafeRelativePath(entry.previous_path) ||
      !isSafeRelativePath(entry.current_path) ||
      entry.previous_path === entry.current_path
    ) {
      throw new Error("Foldweave returned an invalid proposal-delta entry.");
    }
    memberIds.add(entry.member_id);
    previousMemberId = entry.member_id;
  }
  if (
    value.entries.length === 0 &&
    value.previous_result_folder_name === value.current_result_folder_name
  ) {
    throw new Error("Foldweave returned an empty proposal delta.");
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function matchesPattern(value: unknown, pattern: RegExp): value is string {
  return typeof value === "string" && pattern.test(value);
}

function isNullableFingerprint(value: unknown): value is string | null {
  return value === null || matchesPattern(value, SHA256);
}

function isSafeRelativePath(value: unknown): value is string {
  if (
    typeof value !== "string" ||
    value.length === 0 ||
    value.length > 4_096 ||
    value.includes("\0") ||
    value.includes("\\")
  ) {
    return false;
  }
  return value
    .split("/")
    .every((part) => part.length > 0 && part !== "." && part !== "..");
}

function isResultFolderName(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value.length > 0 &&
    value.length <= 240 &&
    value === value.trim() &&
    value !== "." &&
    value !== ".." &&
    !value.includes("/") &&
    !value.includes("\\") &&
    !value.includes("\0")
  );
}

function sameStrings(left: readonly string[], right: readonly string[]): boolean {
  return (
    left.length === right.length &&
    left.every((value, index) => value === right[index])
  );
}

function compareStrings(left: string, right: string): number {
  const leftCodePoints = Array.from(left, (value) => value.codePointAt(0) ?? 0);
  const rightCodePoints = Array.from(right, (value) => value.codePointAt(0) ?? 0);
  const sharedLength = Math.min(leftCodePoints.length, rightCodePoints.length);
  for (let index = 0; index < sharedLength; index += 1) {
    const difference = leftCodePoints[index]! - rightCodePoints[index]!;
    if (difference !== 0) {
      return difference;
    }
  }
  return leftCodePoints.length - rightCodePoints.length;
}
