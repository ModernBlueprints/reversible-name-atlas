import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  AcceptanceBindingPayload,
  FolderPlanPreviewV1,
  FolderPlanRevisionDeltaV1,
  ReviewStatus,
  RevisionPayload,
} from "./contracts";
import { assertStatus } from "./contracts";
import { ReviewIsland } from "./review-island";

const A = "a".repeat(64);
const B = "b".repeat(64);
const C = "c".repeat(64);
const D = "d".repeat(64);
const E = "e".repeat(64);
const JOB_ID = "1".repeat(32);

const preview: FolderPlanPreviewV1 = {
  schema_version: "folder-plan-preview.v1",
  job_id: JOB_ID,
  expected_job_revision: 3,
  proposal_revision: 0,
  proposal_basis: "fresh_gpt_plan",
  source_commitment: A,
  imported_change_file_fingerprint: null,
  match_report_fingerprint: null,
  immediate_parent_candidate_fingerprint: null,
  current_tree_members: [
    { member_id: A, member_kind: "regular_file", relative_path: ".env.local", directory_prefixes: [], protected: true },
    { member_id: D, member_kind: "empty_directory", relative_path: "archive/empty", directory_prefixes: ["archive"], protected: true },
    { member_id: C, member_kind: "regular_file", relative_path: "assets/logo.png", directory_prefixes: ["assets"], protected: false },
    { member_id: B, member_kind: "regular_file", relative_path: "notes/brief.md", directory_prefixes: ["notes"], protected: false },
  ],
  proposed_tree_members: [
    { member_id: A, member_kind: "regular_file", relative_path: ".env.local", directory_prefixes: [], protected: true },
    { member_id: C, member_kind: "regular_file", relative_path: "Delivery/brand/logo.png", directory_prefixes: ["Delivery", "Delivery/brand"], protected: false },
    { member_id: B, member_kind: "regular_file", relative_path: "Delivery/final-brief.md", directory_prefixes: ["Delivery"], protected: false },
    { member_id: D, member_kind: "empty_directory", relative_path: "archive/empty", directory_prefixes: ["archive"], protected: true },
  ],
  member_changes: [
    {
      member_id: A,
      member_kind: "regular_file",
      current_relative_path: ".env.local",
      proposed_relative_path: ".env.local",
      change_classification: "protected",
      protected: true,
      authority_source: "protected",
      rationale: "Keep this protected file at its exact source path.",
      link_updated: false,
      supported_link_effect_ids: [],
    },
    {
      member_id: D,
      member_kind: "empty_directory",
      current_relative_path: "archive/empty",
      proposed_relative_path: "archive/empty",
      change_classification: "empty_directory",
      protected: true,
      authority_source: "protected",
      rationale: "Keep this explicit empty directory unchanged.",
      link_updated: false,
      supported_link_effect_ids: [],
    },
    {
      member_id: C,
      member_kind: "regular_file",
      current_relative_path: "assets/logo.png",
      proposed_relative_path: "Delivery/brand/logo.png",
      change_classification: "moved",
      protected: false,
      authority_source: "gpt_plan",
      rationale: "Move the identity asset into delivery.",
      link_updated: false,
      supported_link_effect_ids: [],
    },
    {
      member_id: B,
      member_kind: "regular_file",
      current_relative_path: "notes/brief.md",
      proposed_relative_path: "Delivery/final-brief.md",
      change_classification: "moved_and_renamed",
      protected: false,
      authority_source: "gpt_plan",
      rationale: "Put the final brief in Delivery.",
      link_updated: true,
      supported_link_effect_ids: [A],
    },
  ],
  supported_link_effects: [
    {
      reference_id: A,
      source_member_id: B,
      target_member_id: C,
      current_source_path: "notes/brief.md",
      current_target_path: "assets/logo.png",
      proposed_source_path: "Delivery/final-brief.md",
      proposed_target_path: "Delivery/brand/logo.png",
      original_destination: "../assets/logo.png",
      proposed_destination: "brand/logo.png",
      status: "rewritten",
    },
  ],
  collision_findings: [],
  blocker_findings: [],
  counts: {
    file_count: 3,
    empty_directory_count: 1,
    changed_path_count: 2,
    renamed_count: 1,
    moved_count: 2,
    link_count: 1,
    link_updated_count: 1,
    protected_count: 1,
    blocker_count: 0,
  },
  compiled_candidate_fingerprint: B,
  preview_fingerprint: C,
};

const status: ReviewStatus = {
  job_id: JOB_ID,
  lifecycle: "reviewing",
  job_revision: 3,
  proposal_revision: 0,
  candidate_fingerprint: B,
  preview_fingerprint: C,
  output_parent: "/tmp/foldweave-output",
  result_folder_name: "northstar-organized",
  revision_available: true,
  revision_attempts_remaining: 2,
  revision_failure: null,
  latest_proposal_delta: null,
  done_url: null,
};

function revisionDelta(
  overrides: Partial<FolderPlanRevisionDeltaV1> = {},
): FolderPlanRevisionDeltaV1 {
  return {
    schema_version: "folder-plan-revision-delta.v1",
    job_id: JOB_ID,
    proposal_revision_before: 0,
    proposal_revision_after: 1,
    base_candidate_fingerprint: D,
    base_preview_fingerprint: E,
    current_candidate_fingerprint: B,
    current_preview_fingerprint: C,
    previous_result_folder_name: "northstar-organized",
    current_result_folder_name: "northstar-organized",
    entries: [
      {
        member_id: C,
        previous_path: "Delivery/draft/logo.png",
        current_path: "Delivery/brand/logo.png",
      },
    ],
    delta_fingerprint: A,
    ...overrides,
  };
}

function renderReview(journey: "organize" | "apply" = "organize") {
  const acceptPlan = vi.fn(async () => undefined);
  const revisePlan = vi.fn(async () => undefined);
  const keepPrevious = vi.fn(async () => undefined);
  render(
    <ReviewIsland
      acceptPlan={acceptPlan}
      idempotencyKeyFactory={() => "test-idempotency-key"}
      journey={journey}
      keepPrevious={keepPrevious}
      preview={preview}
      revisePlan={revisePlan}
      status={status}
    />,
  );
  return { acceptPlan, keepPrevious, revisePlan };
}

function makeReceiverPreview(): FolderPlanPreviewV1 {
  return {
    ...preview,
    proposal_basis: "imported_change_file",
    imported_change_file_fingerprint: D,
    match_report_fingerprint: E,
    member_changes: preview.member_changes.map((change) => ({
      ...change,
      authority_source: change.protected ? "protected" : "change_file",
    })),
  };
}

function makeMaximumShapePreview(): FolderPlanPreviewV1 {
  const currentTreeMembers: FolderPlanPreviewV1["current_tree_members"] = [];
  const proposedTreeMembers: FolderPlanPreviewV1["proposed_tree_members"] = [];
  const memberChanges: FolderPlanPreviewV1["member_changes"] = [];

  for (let index = 0; index < 500; index += 1) {
    const memberId = hexId(index + 1);
    const protectedMember = index % 100 === 0;
    const currentPath = `source/bucket-${String(index % 10).padStart(2, "0")}/file-${String(index).padStart(4, "0")}.md`;
    const proposedPath = protectedMember
      ? currentPath
      : `organized/bucket-${String(index % 10).padStart(2, "0")}/item-${String(index).padStart(4, "0")}.md`;
    currentTreeMembers.push({
      member_id: memberId,
      member_kind: "regular_file",
      relative_path: currentPath,
      directory_prefixes: directoryPrefixes(currentPath),
      protected: protectedMember,
    });
    proposedTreeMembers.push({
      member_id: memberId,
      member_kind: "regular_file",
      relative_path: proposedPath,
      directory_prefixes: directoryPrefixes(proposedPath),
      protected: protectedMember,
    });
    memberChanges.push({
      member_id: memberId,
      member_kind: "regular_file",
      current_relative_path: currentPath,
      proposed_relative_path: proposedPath,
      change_classification: protectedMember ? "protected" : "moved_and_renamed",
      protected: protectedMember,
      authority_source: protectedMember ? "protected" : "gpt_plan",
      rationale: protectedMember
        ? "Keep the protected member fixed."
        : "Move this member into the organized tree.",
      link_updated: false,
      supported_link_effect_ids: [],
    });
  }

  for (let index = 0; index < 1_000; index += 1) {
    const memberId = hexId(index + 501);
    const path = `empty/group-${String(index).padStart(4, "0")}`;
    const member = {
      member_id: memberId,
      member_kind: "empty_directory" as const,
      relative_path: path,
      directory_prefixes: ["empty"],
      protected: false,
    };
    currentTreeMembers.push(member);
    proposedTreeMembers.push(member);
    memberChanges.push({
      member_id: memberId,
      member_kind: "empty_directory",
      current_relative_path: path,
      proposed_relative_path: path,
      change_classification: "empty_directory",
      protected: false,
      authority_source: "gpt_plan",
      rationale: "Preserve this explicit empty directory.",
      link_updated: false,
      supported_link_effect_ids: [],
    });
  }

  return {
    ...preview,
    current_tree_members: currentTreeMembers,
    proposed_tree_members: proposedTreeMembers,
    member_changes: memberChanges,
    supported_link_effects: [],
    counts: {
      file_count: 500,
      empty_directory_count: 1_000,
      changed_path_count: 495,
      renamed_count: 495,
      moved_count: 495,
      link_count: 0,
      link_updated_count: 0,
      protected_count: 5,
      blocker_count: 0,
    },
  };
}

function hexId(value: number): string {
  return value.toString(16).padStart(64, "0");
}

function directoryPrefixes(path: string): string[] {
  const parts = path.split("/").slice(0, -1);
  return parts.map((_, index) => parts.slice(0, index + 1).join("/"));
}

afterEach(cleanup);

describe("Foldweave review island", () => {
  it("accepts only a fail-closed stale local status", () => {
    const staleStatus: ReviewStatus = {
      ...status,
      lifecycle: "stale",
      revision_available: false,
      revision_attempts_remaining: 0,
      action_lock_reason: "Selected source differs from the immutable review snapshot.",
    };

    expect(() => assertStatus(staleStatus, JOB_ID)).not.toThrow();
    expect(() =>
      assertStatus(
        { ...staleStatus, revision_available: true },
        JOB_ID,
      ),
    ).toThrow("incomplete stale review status");
  });

  it("requires a strict durable delta exactly when a revised proposal is visible", () => {
    const revisedStatus: ReviewStatus = {
      ...status,
      proposal_revision: 1,
      latest_proposal_delta: revisionDelta(),
    };

    expect(() => assertStatus(revisedStatus, JOB_ID)).not.toThrow();
    expect(() =>
      assertStatus(
        { ...revisedStatus, latest_proposal_delta: null },
        JOB_ID,
      ),
    ).toThrow("omitted the durable delta");
    expect(() =>
      assertStatus(
        {
          ...revisedStatus,
          latest_proposal_delta: revisionDelta({
            current_preview_fingerprint: D,
          }),
        },
        JOB_ID,
      ),
    ).toThrow("does not match the durable review");
    expect(() =>
      assertStatus(
        {
          ...revisedStatus,
          latest_proposal_delta: {
            ...revisionDelta(),
            entries: [
              {
                member_id: C,
                previous_path: "../outside.md",
                current_path: "Delivery/brand/logo.png",
              },
            ],
          },
        },
        JOB_ID,
      ),
    ).toThrow("invalid proposal-delta entry");
    expect(() => assertStatus(status, JOB_ID)).not.toThrow();
    expect(() =>
      assertStatus(
        { ...status, latest_proposal_delta: revisionDelta() },
        JOB_ID,
      ),
    ).toThrow("does not match the durable review");
    expect(() =>
      assertStatus(
        { ...revisedStatus, result_folder_name: "another-result" },
        JOB_ID,
      ),
    ).toThrow("does not match the durable review");
  });

  it("shows all members by default with a compact trust summary", () => {
    renderReview();

    expect(screen.getByRole("checkbox", { name: "Changed only" })).not.toBeChecked();
    expect(screen.getByText("4 items")).toBeInTheDocument();
    const summary = screen.getByRole("region", { name: "Plan trust summary" });
    expect(summary).toHaveTextContent("3 · 1 protected · 1 empty");
    expect(summary).toHaveTextContent("2 paths");
    expect(summary).toHaveTextContent("1 · 1 updated");
    expect(summary).toHaveTextContent("Unchanged");
    expect(summary).toHaveTextContent("Not created");
    expect(summary).toHaveTextContent(
      "Files: 3; 1 protected; 1 empty directories.",
    );
    expect(summary.querySelector(".fw-status-accessible")).toHaveTextContent(
      "Output: not created.",
    );
  });

  it("toggles the complete origin labels without losing selected member details", async () => {
    const user = userEvent.setup();
    renderReview();

    expect(screen.getByRole("button", { name: "Proposed structure" })).toHaveAttribute("aria-pressed", "true");
    await user.click(screen.getByRole("treeitem", { name: /final-brief\.md/ }));
    expect(screen.getByText("Delivery/final-brief.md", { selector: "h2" })).toBeInTheDocument();
    expect(screen.getByText("Planning proposal, checked by deterministic code")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Original structure" }));
    expect(screen.getByRole("button", { name: "Original structure" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("notes/brief.md", { selector: "h2" })).toBeInTheDocument();
  });

  it("uses receiver-specific current and shared proposal labels", () => {
    const receiverPreview: FolderPlanPreviewV1 = {
      ...makeReceiverPreview(),
      supported_link_effects: [
        ...preview.supported_link_effects,
        {
          reference_id: E,
          source_member_id: B,
          target_member_id: A,
          current_source_path: "notes/brief.md",
          current_target_path: ".env.local",
          proposed_source_path: "Delivery/final-brief.md",
          proposed_target_path: ".env.local",
          original_destination: "../.env.local",
          proposed_destination: "../.env.local",
          status: "unchanged",
        },
      ],
      counts: { ...preview.counts, link_count: 2 },
    };
    render(
      <ReviewIsland
        acceptPlan={vi.fn(async () => undefined)}
        journey="apply"
        keepPrevious={vi.fn(async () => undefined)}
        preview={receiverPreview}
        revisePlan={vi.fn(async () => undefined)}
        status={status}
      />,
    );
    expect(screen.getByRole("button", { name: "Your current folder" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Shared proposal" })).toHaveAttribute("aria-pressed", "true");
    const evidence = screen.getByText("Receiver match").closest("details")!;
    expect(within(evidence).getByText("Verified")).toBeInTheDocument();
    expect(within(evidence).getByText(D)).toBeInTheDocument();
    expect(within(evidence).getByText(E)).toBeInTheDocument();
    expect(within(evidence).getByText(A)).toBeInTheDocument();
    expect(within(evidence).getByText("0 GPT calls so far")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Plan trust summary" })).toHaveTextContent(
      "2 · 1 updated",
    );
  });

  it("renders every deterministic finding and explains why acceptance is disabled", () => {
    const findingsPreview: FolderPlanPreviewV1 = {
      ...makeReceiverPreview(),
      collision_findings: [
        {
          finding_id: "target_path_collision",
          severity: "collision",
          detail: "Two members would use Delivery/final-brief.md.",
          member_ids: [B, C],
        },
      ],
      blocker_findings: [
        {
          finding_id: "receiver_ambiguous_duplicate_group",
          severity: "blocker",
          detail: "Two equivalent members cannot be rebound uniquely.",
          member_ids: [B, C],
        },
        {
          finding_id: "receiver_relationship_changed",
          severity: "blocker",
          detail: "A supported Markdown relationship differs.",
          member_ids: [B],
        },
      ],
      counts: { ...preview.counts, blocker_count: 2 },
    };
    render(
      <ReviewIsland
        acceptPlan={vi.fn(async () => undefined)}
        journey="apply"
        keepPrevious={vi.fn(async () => undefined)}
        preview={findingsPreview}
        revisePlan={vi.fn(async () => undefined)}
        status={status}
      />,
    );

    const findings = screen.getByRole("region", { name: "Acceptance is unavailable" });
    expect(within(findings).getByText("target_path_collision")).toBeInTheDocument();
    expect(within(findings).getByText("receiver_ambiguous_duplicate_group")).toBeInTheDocument();
    expect(within(findings).getByText("receiver_relationship_changed")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Resolve the items below.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Accept this structure and create copy" }),
    ).toBeDisabled();
  });

  it("locks stale review actions and no longer asserts that the source is unchanged", () => {
    const durableDelta = revisionDelta();
    render(
      <ReviewIsland
        acceptPlan={vi.fn(async () => undefined)}
        journey="apply"
        keepPrevious={vi.fn(async () => undefined)}
        preview={{ ...makeReceiverPreview(), proposal_revision: 1 }}
        revisePlan={vi.fn(async () => undefined)}
        status={{
          ...status,
          lifecycle: "stale",
          proposal_revision: 1,
          revision_available: false,
          revision_attempts_remaining: 0,
          latest_proposal_delta: durableDelta,
        }}
      />,
    );

    expect(screen.getByRole("region", { name: "Plan trust summary" })).toHaveTextContent(
      "Changed",
    );
    expect(screen.getByRole("alert")).toHaveTextContent("This review is stale.");
    const delta = screen.getByRole("status");
    expect(within(delta).getByText("1 path changed.")).toBeInTheDocument();
    expect(within(delta).getByText("Delivery/draft/logo.png")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Accept this structure and create copy" }),
    ).toBeDisabled();
    expect(screen.getByRole("button", { name: "Send changes" })).toBeDisabled();
  });

  it("does not invent zero-call evidence for a model-derived receiver proposal", () => {
    render(
      <ReviewIsland
        acceptPlan={vi.fn(async () => undefined)}
        journey="apply"
        keepPrevious={vi.fn(async () => undefined)}
        preview={{
          ...preview,
          proposal_basis: "gpt_derivative",
          imported_change_file_fingerprint: D,
          immediate_parent_candidate_fingerprint: E,
        }}
        revisePlan={vi.fn(async () => undefined)}
        status={status}
      />,
    );

    expect(screen.getByRole("button", { name: "Revised proposal" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.queryByRole("button", { name: "Shared proposal" })).not.toBeInTheDocument();
    expect(screen.queryByText("0 GPT calls so far")).not.toBeInTheDocument();
    const evidence = screen
      .getByText("Parent proposal", { selector: "summary" })
      .closest("details")!;
    expect(within(evidence).getByText("GPT used for this derivative proposal")).toBeInTheDocument();
    expect(within(evidence).getByText(E)).toBeInTheDocument();
  });

  it("shows supported-link impact for both the source and target member", async () => {
    const user = userEvent.setup();
    renderReview();

    await user.click(screen.getByRole("treeitem", { name: /logo\.png/ }));
    expect(screen.getByText("../assets/logo.png")).toBeInTheDocument();
    expect(screen.getByText("brand/logo.png")).toBeInTheDocument();
  });

  it("supports filters and keyboard traversal with visible focus targets", async () => {
    const user = userEvent.setup();
    renderReview();

    await user.click(screen.getByRole("checkbox", { name: "Protected" }));
    expect(screen.getByRole("treeitem", { name: ".env.local, Protected" })).toBeInTheDocument();

    await user.click(screen.getByRole("checkbox", { name: "Protected" }));
    await user.click(screen.getByRole("checkbox", { name: "Changed only" }));
    const tree = screen.getByRole("tree");
    const items = within(tree).getAllByRole("treeitem");
    expect(items[0]).toHaveAttribute("tabindex", "0");
    items.slice(1).forEach((item) => expect(item).toHaveAttribute("tabindex", "-1"));
    items[0].focus();
    fireEvent.keyDown(items[0], { key: "ArrowDown" });
    expect(items[1]).toHaveFocus();
    expect(items[1]).toHaveAttribute("tabindex", "0");
    expect(items[0]).toHaveAttribute("tabindex", "-1");
    fireEvent.keyDown(items[1], { key: "End" });
    expect(items.at(-1)).toHaveFocus();
    fireEvent.keyDown(items.at(-1)!, { key: "Home" });
    expect(items[0]).toHaveFocus();
  });

  it("uses standard left and right tree navigation for parents and children", async () => {
    renderReview();
    const finalBrief = screen.getByRole("treeitem", { name: /final-brief\.md/ });
    finalBrief.focus();

    fireEvent.keyDown(finalBrief, { key: "ArrowLeft" });
    const delivery = screen.getByRole("treeitem", { name: "Delivery" });
    expect(delivery).toHaveFocus();

    fireEvent.keyDown(delivery, { key: "ArrowLeft" });
    expect(delivery).toHaveAttribute("aria-expanded", "false");
    fireEvent.keyDown(delivery, { key: "ArrowRight" });
    expect(delivery).toHaveAttribute("aria-expanded", "true");
    fireEvent.keyDown(delivery, { key: "ArrowRight" });
    expect(screen.getByRole("treeitem", { name: "brand" })).toHaveFocus();
  });

  it("submits the exact fingerprint-bound acceptance only after the explicit click", async () => {
    const user = userEvent.setup();
    const { acceptPlan } = renderReview();
    expect(acceptPlan).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Accept this structure and create copy" }));
    expect(acceptPlan).toHaveBeenCalledTimes(1);
    expect(acceptPlan).toHaveBeenCalledWith({
      candidate_fingerprint: B,
      expected_revision: 3,
      idempotency_key: "test-idempotency-key",
      preview_fingerprint: C,
    });
  });

  it("reuses the exact acceptance key after an uncertain response", async () => {
    const user = userEvent.setup();
    const acceptPlan = vi
      .fn<(payload: AcceptanceBindingPayload) => Promise<void>>()
      .mockRejectedValueOnce(new Error("The response was not observed."))
      .mockResolvedValueOnce(undefined);
    const idempotencyKeyFactory = vi
      .fn<() => string>()
      .mockReturnValueOnce("stable-accept-key")
      .mockReturnValueOnce("must-not-be-used");
    render(
      <ReviewIsland
        acceptPlan={acceptPlan}
        idempotencyKeyFactory={idempotencyKeyFactory}
        journey="organize"
        keepPrevious={vi.fn(async () => undefined)}
        preview={preview}
        revisePlan={vi.fn(async () => undefined)}
        status={status}
      />,
    );

    const accept = screen.getByRole("button", {
      name: "Accept this structure and create copy",
    });
    await user.click(accept);
    expect(await screen.findByText("The response was not observed.")).toBeInTheDocument();
    await user.click(accept);

    expect(acceptPlan).toHaveBeenCalledTimes(2);
    expect(acceptPlan.mock.calls[0]?.[0].idempotency_key).toBe("stable-accept-key");
    expect(acceptPlan.mock.calls[1]?.[0].idempotency_key).toBe("stable-accept-key");
    expect(idempotencyKeyFactory).toHaveBeenCalledTimes(1);
  });

  it("submits one exact bounded revision after nonblank user input", async () => {
    const user = userEvent.setup();
    const { acceptPlan, revisePlan } = renderReview();
    const input = screen.getByLabelText("Change this proposal");
    expect(screen.getByRole("button", { name: "Send changes" })).toBeDisabled();
    await user.type(input, "Keep the notes together");
    await user.click(screen.getByRole("button", { name: "Send changes" }));
    expect(revisePlan).toHaveBeenCalledWith({
      candidate_fingerprint: B,
      expected_revision: 3,
      idempotency_key: "test-idempotency-key",
      instruction: "Keep the notes together",
      preview_fingerprint: C,
    });
    expect(acceptPlan).not.toHaveBeenCalled();
  });

  it("reuses the exact revision key when the same delivered message is retried", async () => {
    const user = userEvent.setup();
    const revisePlan = vi.fn<(payload: RevisionPayload) => Promise<void>>(
      async () => undefined,
    );
    const idempotencyKeyFactory = vi
      .fn<() => string>()
      .mockReturnValueOnce("stable-revision-key")
      .mockReturnValueOnce("must-not-be-used");
    render(
      <ReviewIsland
        acceptPlan={vi.fn(async () => undefined)}
        idempotencyKeyFactory={idempotencyKeyFactory}
        journey="organize"
        keepPrevious={vi.fn(async () => undefined)}
        preview={preview}
        revisePlan={revisePlan}
        status={status}
      />,
    );

    const input = screen.getByLabelText("Change this proposal");
    const send = screen.getByRole("button", { name: "Send changes" });
    await user.type(input, "Keep the notes together");
    await user.click(send);
    expect(input).toHaveValue("Keep the notes together");
    await user.click(send);

    expect(revisePlan).toHaveBeenCalledTimes(2);
    expect(revisePlan.mock.calls[0]?.[0].idempotency_key).toBe(
      "stable-revision-key",
    );
    expect(revisePlan.mock.calls[1]?.[0].idempotency_key).toBe(
      "stable-revision-key",
    );
    expect(idempotencyKeyFactory).toHaveBeenCalledTimes(1);
  });

  it("preserves a failed proposal and exposes retry or keep actions", async () => {
    const user = userEvent.setup();
    const keepPrevious = vi.fn(async () => undefined);
    render(
      <ReviewIsland
        acceptPlan={vi.fn(async () => undefined)}
        idempotencyKeyFactory={() => "keep-idempotency-key"}
        journey="organize"
        keepPrevious={keepPrevious}
        preview={preview}
        revisePlan={vi.fn(async () => undefined)}
        status={{
          ...status,
          revision_attempts_remaining: 1,
          revision_failure: "Two files would use the same target path.",
        }}
      />,
    );

    expect(
      screen.getByText("Two files would use the same target path."),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Accept this structure and create copy" }),
    ).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Keep previous proposal" }));
    expect(keepPrevious).toHaveBeenCalledWith({
      candidate_fingerprint: B,
      expected_revision: 3,
      idempotency_key: "keep-idempotency-key",
      preview_fingerprint: C,
    });
  });

  it("preserves tree scroll and shows every changed mapping across preview replacement", async () => {
    const user = userEvent.setup();
    const rendered = render(
      <ReviewIsland
        acceptPlan={vi.fn(async () => undefined)}
        journey="organize"
        keepPrevious={vi.fn(async () => undefined)}
        preview={preview}
        revisePlan={vi.fn(async () => undefined)}
        status={status}
      />,
    );
    const tree = screen.getByRole("tree");
    tree.scrollTop = 173;
    fireEvent.scroll(tree);
    await user.click(screen.getByRole("button", { name: "Original structure" }));
    expect(screen.getByRole("tree").scrollTop).toBe(173);

    const revisedPreview: FolderPlanPreviewV1 = {
      ...preview,
      expected_job_revision: 4,
      proposal_revision: 1,
      proposed_tree_members: preview.proposed_tree_members.map((member) =>
        member.member_id === C
          ? {
              ...member,
              relative_path: "Delivery/final/logo.png",
              directory_prefixes: ["Delivery", "Delivery/final"],
            }
          : member,
      ),
      member_changes: preview.member_changes.map((change) =>
        change.member_id === C
          ? { ...change, proposed_relative_path: "Delivery/final/logo.png" }
          : change,
      ),
      compiled_candidate_fingerprint: D,
      preview_fingerprint: E,
    };
    rendered.rerender(
      <ReviewIsland
        acceptPlan={vi.fn(async () => undefined)}
        journey="organize"
        keepPrevious={vi.fn(async () => undefined)}
        preview={revisedPreview}
        revisePlan={vi.fn(async () => undefined)}
        status={{
          ...status,
          job_revision: 4,
          proposal_revision: 1,
          candidate_fingerprint: D,
          preview_fingerprint: E,
          latest_proposal_delta: revisionDelta({
            base_candidate_fingerprint: B,
            base_preview_fingerprint: C,
            current_candidate_fingerprint: D,
            current_preview_fingerprint: E,
            entries: [
              {
                member_id: C,
                previous_path: "Delivery/brand/logo.png",
                current_path: "Delivery/final/logo.png",
              },
            ],
          }),
        }}
      />,
    );

    expect(screen.getByRole("tree").scrollTop).toBe(173);
    const delta = await screen.findByRole("status");
    expect(within(delta).getByText("1 path changed.")).toBeInTheDocument();
    expect(within(delta).getByText("Delivery/brand/logo.png")).toBeInTheDocument();
    expect(within(delta).getByText("Delivery/final/logo.png")).toBeInTheDocument();
  });

  it("renders the latest persisted revision delta on the first mount after restart", () => {
    render(
      <ReviewIsland
        acceptPlan={vi.fn(async () => undefined)}
        journey="organize"
        keepPrevious={vi.fn(async () => undefined)}
        preview={{ ...preview, proposal_revision: 2 }}
        revisePlan={vi.fn(async () => undefined)}
        status={{
          ...status,
          proposal_revision: 2,
          revision_available: false,
          revision_attempts_remaining: 0,
          latest_proposal_delta: revisionDelta({
            proposal_revision_before: 1,
            proposal_revision_after: 2,
            entries: [
              {
                member_id: C,
                previous_path: "Delivery/intermediate/logo.png",
                current_path: "Delivery/brand/logo.png",
              },
            ],
          }),
        }}
      />,
    );

    const delta = screen.getByRole("status");
    expect(within(delta).getByText("1 path changed.")).toBeInTheDocument();
    expect(
      within(delta).getByText("Delivery/intermediate/logo.png"),
    ).toBeInTheDocument();
    expect(within(delta).queryByText("Delivery/draft/logo.png")).not.toBeInTheDocument();
  });

  it("renders a result-folder-only revision without inventing a path change", () => {
    render(
      <ReviewIsland
        acceptPlan={vi.fn(async () => undefined)}
        journey="organize"
        keepPrevious={vi.fn(async () => undefined)}
        preview={{ ...preview, proposal_revision: 1 }}
        revisePlan={vi.fn(async () => undefined)}
        status={{
          ...status,
          proposal_revision: 1,
          latest_proposal_delta: revisionDelta({
            previous_result_folder_name: "northstar-draft",
            current_result_folder_name: "northstar-final",
            entries: [],
          }),
        }}
      />,
    );

    const delta = screen.getByRole("status");
    expect(within(delta).getByText("0 paths changed.")).toBeInTheDocument();
    expect(within(delta).getByText("Result folder")).toBeInTheDocument();
    expect(within(delta).getByText("northstar-draft")).toBeInTheDocument();
    expect(within(delta).getByText("northstar-final")).toBeInTheDocument();
  });

  it("keeps the bounded 500-file and 1000-directory review usable", async () => {
    const user = userEvent.setup();
    const maximumPreview = makeMaximumShapePreview();
    render(
      <ReviewIsland
        acceptPlan={vi.fn(async () => undefined)}
        journey="organize"
        keepPrevious={vi.fn(async () => undefined)}
        preview={maximumPreview}
        revisePlan={vi.fn(async () => undefined)}
        status={status}
      />,
    );

    expect(screen.getByRole("checkbox", { name: "Changed only" })).toBeChecked();
    expect(screen.getByText("495 items")).toBeInTheDocument();
    const summary = screen.getByRole("region", { name: "Plan trust summary" });
    expect(summary).toHaveTextContent("500 · 5 protected · 1000 empty");
    expect(summary).toHaveTextContent("495 paths");

    const search = screen.getByRole("textbox", {
      name: "Search current and proposed paths",
    });
    await user.type(search, "item-0499");
    expect(screen.getByText("1 items")).toBeInTheDocument();
    const matchingItem = screen.getByRole("treeitem", { name: /item-0499\.md/ });
    await user.click(matchingItem);
    expect(screen.getByText(/organized\/bucket-09\/item-0499\.md/, { selector: "h2" })).toBeInTheDocument();

    const tree = screen.getByRole("tree");
    tree.scrollTop = 211;
    fireEvent.scroll(tree);
    await user.click(screen.getByRole("button", { name: "Original structure" }));
    expect(screen.getByRole("tree").scrollTop).toBe(211);
    const treeItems = within(screen.getByRole("tree")).getAllByRole("treeitem");
    treeItems[0]!.focus();
    fireEvent.keyDown(treeItems[0]!, { key: "ArrowDown" });
    expect(treeItems[1]).toHaveFocus();

    await user.clear(search);
    await user.click(screen.getByRole("checkbox", { name: "Empty directory" }));
    expect(screen.getByText("1000 items")).toBeInTheDocument();

    const reviewCss = readFileSync(
      resolve(process.cwd(), "src/review.css"),
      "utf8",
    );
    expect(reviewCss).not.toMatch(/overflow-x:\s*clip/);
    expect(reviewCss).toMatch(/\.fw-review\s*\{[^}]*min-width:\s*0;[^}]*width:\s*100%;/s);
    expect(reviewCss).toMatch(/@media \(max-width:\s*520px\)[\s\S]*\.fw-toolbar \.bp6-button-group\s*\{[^}]*grid-template-columns:\s*repeat\(2, minmax\(0, 1fr\)\);/s);
    expect(reviewCss).toMatch(/@media \(max-width:\s*520px\)[\s\S]*\.fw-filter-row\s*\{[^}]*grid-template-columns:\s*minmax\(0, 1fr\);/s);
    expect(reviewCss).toMatch(
      /@media \(max-width:\s*520px\)[\s\S]*\.fw-status-items\s*\{[^}]*display:\s*none;[^}]*\}[\s\S]*\.fw-status-compact\s*\{[^}]*display:\s*grid;[^}]*grid-template-columns:\s*repeat\(4, minmax\(0, 1fr\)\);[^}]*max-width:\s*100%;[^}]*min-width:\s*0;[^}]*width:\s*100%;/s,
    );
    expect(reviewCss).toMatch(
      /\.fw-main-grid\s*\{[^}]*max-width:\s*100%;[^}]*min-width:\s*0;[^}]*overflow:\s*hidden;[^}]*width:\s*calc\(100% - 16px\);/s,
    );
    expect(reviewCss).toMatch(/\.fw-tree\s*\{[^}]*max-width:\s*100%;[^}]*overflow:\s*auto;/s);
    expect(reviewCss).toMatch(/\.fw-tree-row\s*\{[^}]*min-width:\s*0;/s);
    expect(reviewCss).not.toMatch(/(?:linear|radial)-gradient\s*\(/);
    expect(reviewCss).not.toMatch(/(?:text-shadow|filter:\s*drop-shadow)/);
    expect(reviewCss).not.toMatch(/--fw-(?:cyan|violet|navy)/);
    expect(reviewCss).toMatch(
      /body\.folder-app \.folder-main:has\(\.foldweave-review-shell\)\s*\{[^}]*padding:\s*0;/s,
    );
    expect(reviewCss).toMatch(
      /\.foldweave-review-shell\s*\{[^}]*height:\s*calc\(100dvh - 2\.75rem\);[^}]*min-height:\s*0;[^}]*overflow:\s*hidden;/s,
    );
    expect(reviewCss).toMatch(
      /\.fw-review-window\s*\{[^}]*display:\s*flex;[^}]*height:\s*100%;[^}]*min-height:\s*0;[^}]*overflow:\s*hidden;/s,
    );
    expect(reviewCss).toMatch(
      /\.fw-main-grid\s*\{[^}]*flex:\s*1 1 0;[^}]*gap:\s*0;[^}]*min-height:\s*0;[^}]*overflow:\s*hidden;/s,
    );
    expect(reviewCss).toMatch(/\.fw-detail-panel\s*\{[^}]*border-left:\s*1px solid/s);
    expect(reviewCss).not.toMatch(/\.fw-(?:toolbar|status-bar|panel-heading|detail-heading|decision-panel)\s*\{[^}]*border-(?:top|bottom):\s*1px solid/s);
    expect(reviewCss).not.toMatch(/\.fw-details > div\s*\{[^}]*border-bottom:/s);
    expect(reviewCss).toMatch(
      /font-family:\s*-apple-system,\s*BlinkMacSystemFont,\s*system-ui,\s*"Helvetica Neue"/,
    );
    const reviewSource = readFileSync(
      resolve(process.cwd(), "src/review-island.tsx"),
      "utf8",
    );
    const widgetSource = readFileSync(
      resolve(process.cwd(), "src/chatgpt-widget.tsx"),
      "utf8",
    );
    const widgetCss = readFileSync(
      resolve(process.cwd(), "src/chatgpt-widget.css"),
      "utf8",
    );
    expect(`${reviewSource}\n${widgetSource}`).not.toContain("bp6-dark");
    expect(reviewSource).not.toMatch(/<(?:Card|Tag)\b/);
    expect(reviewSource).not.toContain(">Unchanged</span>");
    expect(widgetSource).not.toMatch(/<(?:Card|Tag)\b/);
    expect(widgetCss).not.toMatch(/(?:linear|radial)-gradient\s*\(/);
    expect(widgetCss).not.toMatch(/\.fw-chatgpt-(?:header|progress)\s*\{[^}]*border-bottom:/s);
    expect(widgetCss).not.toMatch(/\.fw-chatgpt-header\s*\{[^}]*border-radius:/s);
    expect(widgetCss).toMatch(
      /\.fw-chatgpt-widget\s*\{[^}]*display:\s*flex;[^}]*height:\s*100dvh;[^}]*overflow:\s*hidden;/s,
    );
    const widgetHeaders = readFileSync(
      resolve(process.cwd(), "chatgpt-widget-assets/_headers"),
      "utf8",
    );
    expect(widgetHeaders).toMatch(
      /\/foldweave-chatgpt-widget\.js[\s\S]*Access-Control-Allow-Origin:\s*\*[\s\S]*Cross-Origin-Resource-Policy:\s*cross-origin/,
    );
    expect(widgetHeaders).toMatch(
      /\/foldweave-chatgpt-widget\.css[\s\S]*Access-Control-Allow-Origin:\s*\*[\s\S]*Cross-Origin-Resource-Policy:\s*cross-origin/,
    );
    const shellCss = readFileSync(
      resolve(process.cwd(), "../src/name_atlas/static/folder.css"),
      "utf8",
    );
    expect(shellCss).not.toMatch(/\.folder-mode\s*\{/);
    expect(shellCss).toMatch(/@media \(max-width:\s*39rem\)[\s\S]*\.folder-header-actions\s*\{[^}]*margin-left:\s*auto;/s);
  });
});
