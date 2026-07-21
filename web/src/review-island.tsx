import {
  Button,
  ButtonGroup,
  Checkbox,
  Icon,
  InputGroup,
  Spinner,
} from "@blueprintjs/core";
import {
  type CSSProperties,
  type KeyboardEvent,
  type ReactElement,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import type {
  AcceptanceBindingPayload,
  ChangeClassification,
  FolderPlanMemberChange,
  FolderPlanPreviewV1,
  FolderPlanRevisionDeltaV1,
  FolderPlanTreeMember,
  Journey,
  KeepProposalPayload,
  RevisionPayload,
  ReviewDisplayStatus,
  ViewSide,
} from "./contracts";

type FilterKey =
  | "moved"
  | "renamed"
  | "link_updated"
  | "protected"
  | "unchanged"
  | "empty_directory";

interface TreeNode {
  key: string;
  label: string;
  path: string;
  depth: number;
  kind: "directory" | "file";
  memberId: string | null;
  explicitEmptyDirectory: boolean;
  children: TreeNode[];
}

interface PendingMutationKey {
  requestFingerprint: string;
  idempotencyKey: string;
}

export interface ReviewIslandProps {
  preview: FolderPlanPreviewV1;
  status: ReviewDisplayStatus;
  journey: Journey;
  acceptPlan: (payload: AcceptanceBindingPayload) => Promise<void>;
  revisePlan: (payload: RevisionPayload) => Promise<void>;
  keepPrevious: (payload: KeepProposalPayload) => Promise<void>;
  acceptanceScopeFingerprint?: string;
  actionsDisabled?: boolean;
  idempotencyKeyFactory?: () => string;
}

const FILTERS: ReadonlyArray<{ key: FilterKey; label: string }> = [
  { key: "moved", label: "Moved" },
  { key: "renamed", label: "Renamed" },
  { key: "link_updated", label: "Link updated" },
  { key: "protected", label: "Protected" },
  { key: "unchanged", label: "Unchanged" },
  { key: "empty_directory", label: "Empty directory" },
];

const CLASS_LABELS: Record<ChangeClassification, string> = {
  unchanged: "Unchanged",
  renamed: "Renamed",
  moved: "Moved",
  moved_and_renamed: "Moved + renamed",
  protected: "Protected",
  empty_directory: "Empty directory",
};

const LARGE_TREE_CHANGED_ONLY_THRESHOLD = 200;

export function ReviewIsland({
  preview,
  status,
  journey,
  acceptPlan,
  revisePlan,
  keepPrevious,
  acceptanceScopeFingerprint = "",
  actionsDisabled = false,
  idempotencyKeyFactory = createIdempotencyKey,
}: ReviewIslandProps): ReactElement {
  const [side, setSide] = useState<ViewSide>("proposed");
  const [changedOnly, setChangedOnly] = useState(
    () => preview.member_changes.length >= LARGE_TREE_CHANGED_ONLY_THRESHOLD,
  );
  const [activeFilters, setActiveFilters] = useState<Set<FilterKey>>(new Set());
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(
    preview.member_changes.find(isChanged)?.member_id ??
      preview.member_changes[0]?.member_id ??
      null,
  );
  const [expanded, setExpanded] = useState<Set<string>>(() =>
    initialExpandedPaths(preview),
  );
  const [activeTreeKey, setActiveTreeKey] = useState<string | null>(null);
  const [revisionInstruction, setRevisionInstruction] = useState("");
  const [accepting, setAccepting] = useState(false);
  const [acceptanceError, setAcceptanceError] = useState<string | null>(null);
  const [revisionBusy, setRevisionBusy] = useState(false);
  const [revisionError, setRevisionError] = useState<string | null>(null);
  const acceptanceMutation = useRef<PendingMutationKey | null>(null);
  const revisionMutation = useRef<PendingMutationKey | null>(null);
  const keepMutation = useRef<PendingMutationKey | null>(null);
  const revisionInput = useRef<HTMLTextAreaElement | null>(null);
  const treeItemRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const treeScrollElement = useRef<HTMLDivElement | null>(null);
  const rememberedScrollTop = useRef(0);
  const pendingScrollTop = useRef<number | null>(null);
  const renderedPreviewFingerprint = useRef(preview.preview_fingerprint);

  const changeById = useMemo(
    () => new Map(preview.member_changes.map((change) => [change.member_id, change])),
    [preview.member_changes],
  );
  const members = side === "current" ? preview.current_tree_members : preview.proposed_tree_members;
  const visibleMembers = useMemo(
    () =>
      members.filter((member) => {
        const change = changeById.get(member.member_id);
        if (!change) {
          return false;
        }
        const normalizedQuery = query.trim().toLowerCase();
        const matchesQuery =
          normalizedQuery.length === 0 ||
          change.current_relative_path.toLowerCase().includes(normalizedQuery) ||
          change.proposed_relative_path.toLowerCase().includes(normalizedQuery);
        if (!matchesQuery) {
          return false;
        }
        if (activeFilters.size > 0) {
          return [...activeFilters].some((filter) => matchesFilter(change, filter));
        }
        return !changedOnly || isChanged(change);
      }),
    [activeFilters, changeById, changedOnly, members, query],
  );
  const tree = useMemo(() => buildTree(visibleMembers), [visibleMembers]);
  const flatTree = useMemo(() => flattenVisibleTree(tree, expanded), [expanded, tree]);
  const selected = selectedId === null ? undefined : changeById.get(selectedId);
  const selectedLinks = useMemo(
    () =>
      selected === undefined
        ? []
        : preview.supported_link_effects.filter(
            (effect) =>
              effect.source_member_id === selected.member_id ||
              effect.target_member_id === selected.member_id,
          ),
    [preview.supported_link_effects, selected],
  );

  useEffect(() => {
    if (selectedId !== null && visibleMembers.some((item) => item.member_id === selectedId)) {
      return;
    }
    setSelectedId(visibleMembers[0]?.member_id ?? null);
  }, [selectedId, visibleMembers]);

  useEffect(() => {
    if (flatTree.some((node) => node.key === activeTreeKey)) {
      return;
    }
    const selectedNode = flatTree.find((node) => node.memberId === selectedId);
    setActiveTreeKey(selectedNode?.key ?? flatTree[0]?.key ?? null);
  }, [activeTreeKey, flatTree, selectedId]);

  useEffect(() => {
    acceptanceMutation.current = null;
    revisionMutation.current = null;
    keepMutation.current = null;
    setRevisionInstruction("");
    setRevisionError(null);
    setAcceptanceError(null);
  }, [preview.preview_fingerprint]);

  useLayoutEffect(() => {
    const treeElement = treeScrollElement.current;
    const previewChanged =
      renderedPreviewFingerprint.current !== preview.preview_fingerprint;
    const scrollTop = pendingScrollTop.current;
    if (treeElement && (previewChanged || scrollTop !== null)) {
      treeElement.scrollTop = scrollTop ?? rememberedScrollTop.current;
    }
    pendingScrollTop.current = null;
    renderedPreviewFingerprint.current = preview.preview_fingerprint;
  }, [preview.preview_fingerprint, side]);

  const toggleFilter = (filter: FilterKey): void => {
    setChangedOnly(false);
    setActiveFilters((current) => {
      const next = new Set(current);
      if (next.has(filter)) {
        next.delete(filter);
      } else {
        next.add(filter);
      }
      return next;
    });
  };

  const handleTreeKey = (
    event: KeyboardEvent<HTMLButtonElement>,
    index: number,
    node: TreeNode,
  ): void => {
    if (
      event.key === "ArrowDown" ||
      event.key === "ArrowUp" ||
      event.key === "Home" ||
      event.key === "End"
    ) {
      event.preventDefault();
      const nextIndex =
        event.key === "Home"
          ? 0
          : event.key === "End"
            ? flatTree.length - 1
            : event.key === "ArrowDown"
              ? index + 1
              : index - 1;
      focusTreeItem(nextIndex);
      return;
    }
    if (node.kind === "directory" && event.key === "ArrowRight") {
      event.preventDefault();
      if (!expanded.has(node.path)) {
        setExpanded((current) => new Set(current).add(node.path));
      } else if (flatTree[index + 1]?.depth === node.depth + 1) {
        focusTreeItem(index + 1);
      }
      return;
    }
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      if (node.kind === "directory" && expanded.has(node.path)) {
        setExpanded((current) => {
          const next = new Set(current);
          next.delete(node.path);
          return next;
        });
        return;
      }
      for (let parentIndex = index - 1; parentIndex >= 0; parentIndex -= 1) {
        if (flatTree[parentIndex]!.depth === node.depth - 1) {
          focusTreeItem(parentIndex);
          return;
        }
      }
    }
  };

  const focusTreeItem = (index: number): void => {
    const next = flatTree[index];
    if (!next) {
      return;
    }
    setActiveTreeKey(next.key);
    if (next.memberId !== null) {
      setSelectedId(next.memberId);
    }
    treeItemRefs.current[index]?.focus();
  };

  const submitAcceptance = async (): Promise<void> => {
    if (
      accepting ||
      revisionBusy ||
      actionsDisabled ||
      !reviewInputsCurrent ||
      status.revision_failure !== null ||
      hasBlockingFindings
    ) {
      return;
    }
    setAccepting(true);
    setAcceptanceError(null);
    const requestFingerprint = JSON.stringify([
      preview.expected_job_revision,
      preview.compiled_candidate_fingerprint,
      preview.preview_fingerprint,
      acceptanceScopeFingerprint,
    ]);
    const idempotencyKey = mutationIdempotencyKey(
      acceptanceMutation,
      requestFingerprint,
      idempotencyKeyFactory,
    );
    try {
      await acceptPlan({
        candidate_fingerprint: preview.compiled_candidate_fingerprint,
        expected_revision: preview.expected_job_revision,
        idempotency_key: idempotencyKey,
        preview_fingerprint: preview.preview_fingerprint,
      });
      acceptanceMutation.current = null;
    } catch (error) {
      setAcceptanceError(error instanceof Error ? error.message : "Acceptance was blocked.");
      setAccepting(false);
    }
  };

  const submitRevision = async (): Promise<void> => {
    const instruction = revisionInstruction.trim();
    if (
      revisionBusy ||
      accepting ||
      actionsDisabled ||
      !reviewInputsCurrent ||
      !status.revision_available ||
      instruction.length === 0
    ) {
      return;
    }
    setRevisionBusy(true);
    setRevisionError(null);
    const requestFingerprint = JSON.stringify([
      preview.expected_job_revision,
      preview.compiled_candidate_fingerprint,
      preview.preview_fingerprint,
      instruction,
    ]);
    const idempotencyKey = mutationIdempotencyKey(
      revisionMutation,
      requestFingerprint,
      idempotencyKeyFactory,
    );
    try {
      await revisePlan({
        candidate_fingerprint: preview.compiled_candidate_fingerprint,
        expected_revision: preview.expected_job_revision,
        idempotency_key: idempotencyKey,
        instruction,
        preview_fingerprint: preview.preview_fingerprint,
      });
    } catch (error) {
      setRevisionError(error instanceof Error ? error.message : "Revision was blocked.");
    } finally {
      setRevisionBusy(false);
    }
  };

  const submitKeepPrevious = async (): Promise<void> => {
    if (
      revisionBusy ||
      actionsDisabled ||
      !reviewInputsCurrent ||
      status.revision_failure === null
    ) {
      return;
    }
    setRevisionBusy(true);
    setRevisionError(null);
    const requestFingerprint = JSON.stringify([
      preview.expected_job_revision,
      preview.compiled_candidate_fingerprint,
      preview.preview_fingerprint,
    ]);
    const idempotencyKey = mutationIdempotencyKey(
      keepMutation,
      requestFingerprint,
      idempotencyKeyFactory,
    );
    try {
      await keepPrevious({
        candidate_fingerprint: preview.compiled_candidate_fingerprint,
        expected_revision: preview.expected_job_revision,
        idempotency_key: idempotencyKey,
        preview_fingerprint: preview.preview_fingerprint,
      });
      keepMutation.current = null;
    } catch (error) {
      setRevisionError(
        error instanceof Error ? error.message : "The prior proposal could not be kept.",
      );
    } finally {
      setRevisionBusy(false);
    }
  };

  const currentLabel = journey === "apply" ? "Your current folder" : "Original structure";
  const proposedLabel =
    journey === "apply"
      ? preview.proposal_basis === "gpt_derivative"
        ? "Revised proposal"
        : "Shared proposal"
      : "Proposed structure";
  const importedReceiverEvidence =
    journey === "apply" &&
    preview.proposal_basis === "imported_change_file" &&
    preview.imported_change_file_fingerprint !== null &&
    preview.match_report_fingerprint !== null;
  const derivativeReceiverEvidence =
    journey === "apply" &&
    preview.proposal_basis === "gpt_derivative" &&
    preview.immediate_parent_candidate_fingerprint !== null;
  const reviewInputsCurrent =
    status.lifecycle === "reviewing" || status.lifecycle === "revision_failed";
  const sourceTrustState = reviewInputsCurrent ? "Unchanged" : "Changed";
  const trustSummary = [
    `Source: ${sourceTrustState}.`,
    `Files: ${preview.counts.file_count}; ${preview.counts.protected_count} protected; ${preview.counts.empty_directory_count} empty directories.`,
    `Changes: ${preview.counts.changed_path_count} paths.`,
    `Links: ${preview.counts.link_count}; ${preview.counts.link_updated_count} updated.`,
    "Output: not created.",
  ].join(" ");
  const collisionCount = preview.collision_findings.length;
  const blockerCount = preview.blocker_findings.length;
  const hasBlockingFindings = collisionCount + blockerCount > 0;

  const switchSide = (nextSide: ViewSide): void => {
    if (nextSide === side) {
      return;
    }
    const scrollTop = treeScrollElement.current?.scrollTop ?? rememberedScrollTop.current;
    rememberedScrollTop.current = scrollTop;
    pendingScrollTop.current = scrollTop;
    setSide(nextSide);
  };

  return (
    <div className="fw-review">
      <section className="fw-review-window" aria-label="Structure review">
        <div className="fw-toolbar">
          <ButtonGroup aria-label="Structure view">
            <Button
              aria-pressed={side === "current"}
              intent={side === "current" ? "primary" : "none"}
              onClick={() => switchSide("current")}
            >
              {currentLabel}
            </Button>
            <Button
              aria-pressed={side === "proposed"}
              intent={side === "proposed" ? "primary" : "none"}
              onClick={() => switchSide("proposed")}
            >
              {proposedLabel}
            </Button>
          </ButtonGroup>
          <div className="fw-toolbar-spacer" />
          <InputGroup
            aria-label="Search current and proposed paths"
            leftIcon="search"
            onChange={(event) => setQuery(event.currentTarget.value)}
            placeholder="Search"
            value={query}
          />
          <Checkbox
            checked={changedOnly}
            label="Changed only"
            onChange={(event) => {
              setChangedOnly(event.currentTarget.checked);
              if (event.currentTarget.checked) {
                setActiveFilters(new Set());
              }
            }}
          />
          <details className="fw-filter-menu">
            <summary>Filters</summary>
            <div className="fw-filter-row" role="group" aria-label="Member filters">
              {FILTERS.map(({ key, label }) => (
                <Checkbox
                  checked={activeFilters.has(key)}
                  key={key}
                  label={label}
                  onChange={() => toggleFilter(key)}
                />
              ))}
            </div>
          </details>
        </div>

        <section className="fw-status-bar" aria-label="Plan trust summary">
          <span className="fw-status-accessible">{trustSummary}</span>
          <div className="fw-status-items" aria-hidden="true">
            <TrustItem
              icon="lock"
              label="Source"
              value={sourceTrustState}
            />
            <TrustItem
              icon="document"
              label="Files"
              value={`${preview.counts.file_count} · ${preview.counts.protected_count} protected · ${preview.counts.empty_directory_count} empty`}
            />
            <TrustItem
              icon="changes"
              label="Changes"
              value={`${preview.counts.changed_path_count} paths`}
            />
            <TrustItem
              icon="link"
              label="Links"
              value={`${preview.counts.link_count} · ${preview.counts.link_updated_count} updated`}
            />
            <TrustItem icon="folder-new" label="Output" value="Not created" />
          </div>
          <div className="fw-status-compact" aria-hidden="true">
            <span><Icon icon="lock" />{sourceTrustState}</span>
            <span><Icon icon="document" />{preview.counts.file_count} files</span>
            <span><Icon icon="changes" />{preview.counts.changed_path_count} changed</span>
            <span><Icon icon="link" />{preview.counts.link_updated_count} updated</span>
          </div>
        </section>

        {(importedReceiverEvidence || derivativeReceiverEvidence) && (
          <details className="fw-receiver-evidence">
            <summary>{importedReceiverEvidence ? "Receiver match" : "Parent proposal"}</summary>
            <dl>
              {preview.imported_change_file_fingerprint !== null && (
                <div>
                  <dt>Change File</dt>
                  <dd className="fw-verified-identity">
                    <Icon icon="tick-circle" aria-hidden="true" />
                    <span>Verified</span>
                    <code>{preview.imported_change_file_fingerprint}</code>
                  </dd>
                </div>
              )}
              <div>
                <dt>Receiver source</dt>
                <dd><code>{preview.source_commitment}</code></dd>
              </div>
              {importedReceiverEvidence && (
                <div>
                  <dt>Match report</dt>
                  <dd><code>{preview.match_report_fingerprint}</code></dd>
                </div>
              )}
              {derivativeReceiverEvidence && (
                <div>
                  <dt>Fingerprint</dt>
                  <dd><code>{preview.immediate_parent_candidate_fingerprint}</code></dd>
                </div>
              )}
              <div>
                <dt>Model use</dt>
                <dd>
                  {importedReceiverEvidence
                    ? "0 GPT calls so far"
                    : "GPT used for this derivative proposal"}
                </dd>
              </div>
            </dl>
          </details>
        )}

        <div className="fw-main-grid">
          <section className="fw-tree-panel">
          <header className="fw-panel-heading">
            <h2>{side === "current" ? currentLabel : proposedLabel}</h2>
            <span className="fw-item-count">{visibleMembers.length} items</span>
          </header>
          <div
            className="fw-tree"
            onScroll={(event) => {
              rememberedScrollTop.current = event.currentTarget.scrollTop;
            }}
            ref={treeScrollElement}
            role="tree"
            aria-label={`${side === "current" ? currentLabel : proposedLabel} folder tree`}
          >
            {flatTree.length === 0 ? (
              <div className="fw-empty-state">No members match these filters.</div>
            ) : (
              flatTree.map((node, index) => {
                const change = node.memberId === null ? undefined : changeById.get(node.memberId);
                const isDirectory = node.kind === "directory";
                const isExpanded = expanded.has(node.path);
                return (
                  <button
                    aria-label={treeItemLabel(node, change)}
                    aria-expanded={isDirectory ? isExpanded : undefined}
                    aria-level={node.depth + 1}
                    aria-selected={node.memberId !== null && selectedId === node.memberId}
                    className={`fw-tree-row ${node.memberId !== null && selectedId === node.memberId ? "is-selected" : ""}`}
                    key={node.key}
                    onClick={() => {
                      setActiveTreeKey(node.key);
                      if (isDirectory) {
                        setExpanded((current) => toggleSetValue(current, node.path));
                      }
                      if (node.memberId !== null) {
                        setSelectedId(node.memberId);
                      }
                    }}
                    onFocus={() => {
                      setActiveTreeKey(node.key);
                      if (node.memberId !== null) {
                        setSelectedId(node.memberId);
                      }
                    }}
                    onKeyDown={(event) => handleTreeKey(event, index, node)}
                    ref={(element) => {
                      treeItemRefs.current[index] = element;
                    }}
                    role="treeitem"
                    style={{ "--fw-depth": node.depth } as CSSProperties}
                    tabIndex={node.key === (activeTreeKey ?? flatTree[0]?.key) ? 0 : -1}
                    type="button"
                  >
                    <span className="fw-disclosure" aria-hidden="true">
                      {isDirectory ? (isExpanded ? "⌄" : "›") : ""}
                    </span>
                    <Icon icon={isDirectory ? "folder-close" : "document"} aria-hidden="true" />
                    <span className="fw-tree-label">{node.label}</span>
                    {change && <StatusSignals change={change} />}
                  </button>
                );
              })
            )}
          </div>
          </section>

          <aside className="fw-detail-panel">
          {selected ? (
            <>
              <div className="fw-detail-heading">
                <h2>{side === "current" ? selected.current_relative_path : selected.proposed_relative_path}</h2>
                {selected.change_classification !== "unchanged" && (
                  <span className="fw-detail-status">
                    {CLASS_LABELS[selected.change_classification]}
                  </span>
                )}
              </div>
              <dl className="fw-details">
                <div><dt>Current</dt><dd>{selected.current_relative_path}</dd></div>
                <div><dt>Proposed</dt><dd>{selected.proposed_relative_path}</dd></div>
                <div><dt>Authority</dt><dd>{authorityLabel(selected)}</dd></div>
                <div><dt>Reason</dt><dd>{selected.rationale}</dd></div>
              </dl>
              <section className="fw-link-section" aria-labelledby="link-effects-title">
                <h3 id="link-effects-title">Links</h3>
                {selectedLinks.length === 0 ? (
                  <p>No supported links.</p>
                ) : (
                  <ul>
                    {selectedLinks.map((effect) => (
                      <li key={effect.reference_id}>
                        <span className={`fw-link-status is-${effect.status}`}>
                          {effect.status === "rewritten" ? "Rewritten" : "Still valid"}
                        </span>
                        <code>{effect.original_destination}</code>
                        <span aria-hidden="true">→</span>
                        <code>{effect.proposed_destination}</code>
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            </>
          ) : (
            <div className="fw-empty-state">Select a member to inspect its exact change.</div>
          )}
          </aside>
        </div>

      {hasBlockingFindings && (
        <section className="fw-findings" aria-labelledby="deterministic-findings-title">
          <div>
            <Icon icon="error" aria-hidden="true" />
            <span>
              <strong id="deterministic-findings-title">Acceptance is unavailable</strong>
              <small>Resolve the items below.</small>
            </span>
          </div>
          <ul>
            {[...preview.collision_findings, ...preview.blocker_findings].map((finding) => (
              <li key={`${finding.severity}:${finding.finding_id}`}>
                <span className="fw-finding-kind">
                  {finding.severity === "collision" ? "Collision" : "Blocker"}
                </span>
                <span>
                  <strong>{finding.detail}</strong>
                  <small><code>{finding.finding_id}</code></small>
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {!reviewInputsCurrent && (
        <section className="fw-stale-review" role="alert">
          <Icon icon="outdated" aria-hidden="true" />
          <span>
            <strong>This review is stale.</strong>
            <small>
              {status.action_lock_reason ??
                "The source or Change File changed. Start again with the current inputs."}
            </small>
          </span>
        </section>
      )}

        <section className="fw-decision-panel" aria-label="Review actions">
        {acceptanceError && <div className="fw-error" role="alert">{acceptanceError}</div>}
        {status.latest_proposal_delta && (
          <section className="fw-revision-delta" aria-labelledby="revision-delta-title" role="status">
            <strong id="revision-delta-title">Changes from previous proposal</strong>
            <p>
              {status.latest_proposal_delta.entries.length}{" "}
              {status.latest_proposal_delta.entries.length === 1 ? "path" : "paths"} changed.
            </p>
            {resultFolderChanged(status.latest_proposal_delta) && (
              <div className="fw-revision-root-delta">
                <span>Result folder</span>
                <code>{status.latest_proposal_delta.previous_result_folder_name}</code>
                <span aria-hidden="true">→</span>
                <code>{status.latest_proposal_delta.current_result_folder_name}</code>
              </div>
            )}
            {status.latest_proposal_delta.entries.length > 0 && (
              <ul>
                {status.latest_proposal_delta.entries.map((item) => (
                  <li data-member-id={item.member_id} key={item.member_id}>
                    <code>{item.previous_path}</code>
                    <span aria-hidden="true">→</span>
                    <code>{item.current_path}</code>
                  </li>
                ))}
              </ul>
            )}
          </section>
        )}
        {status.revision_failure && (
          <div className="fw-error" role="alert">
            <strong>That change could not be applied.</strong>
            <span>{status.revision_failure}</span>
            <div className="fw-revision-actions">
              <Button
                disabled={!status.revision_available || revisionBusy || actionsDisabled}
                onClick={() => revisionInput.current?.focus()}
              >
                Try another change
              </Button>
              <Button
                disabled={revisionBusy || actionsDisabled}
                onClick={() => void submitKeepPrevious()}
              >
                Keep previous proposal
              </Button>
            </div>
          </div>
        )}
        {revisionError && <div className="fw-error" role="alert">{revisionError}</div>}
        <div className="fw-decision-controls">
          <div className="fw-revision-composer">
            <label className="fw-revision-label" htmlFor="foldweave-revision">
              Change this proposal
            </label>
            <textarea
              disabled={actionsDisabled || accepting || revisionBusy || !reviewInputsCurrent}
              id="foldweave-revision"
              onChange={(event) => setRevisionInstruction(event.currentTarget.value)}
              placeholder="Describe a change"
              ref={revisionInput}
              rows={2}
              value={revisionInstruction}
            />
            <span className="fw-revision-count">
              {status.revision_available
                ? `${status.revision_attempts_remaining} left`
                : "No revisions left"}
            </span>
          </div>
          <Button
            className="fw-send-button"
            disabled={
              !status.revision_available ||
              revisionInstruction.trim().length === 0 ||
              revisionBusy ||
              accepting ||
              actionsDisabled ||
              !reviewInputsCurrent
            }
            loading={revisionBusy}
            onClick={() => void submitRevision()}
            rightIcon="send-message"
          >
            Send changes
          </Button>
          <Button
            className="fw-accept-button"
            disabled={
              hasBlockingFindings ||
              accepting ||
              revisionBusy ||
              actionsDisabled ||
              !reviewInputsCurrent ||
              status.revision_failure !== null
            }
            intent="primary"
            loading={accepting}
            onClick={() => void submitAcceptance()}
            rightIcon="arrow-right"
          >
            Accept this structure and create copy
          </Button>
        </div>
        </section>
      </section>
    </div>
  );
}

function mutationIdempotencyKey(
  reference: { current: PendingMutationKey | null },
  requestFingerprint: string,
  factory: () => string,
): string {
  if (reference.current?.requestFingerprint === requestFingerprint) {
    return reference.current.idempotencyKey;
  }
  const idempotencyKey = factory();
  reference.current = { requestFingerprint, idempotencyKey };
  return idempotencyKey;
}

function resultFolderChanged(delta: FolderPlanRevisionDeltaV1): boolean {
  return delta.previous_result_folder_name !== delta.current_result_folder_name;
}

function TrustItem({ icon, label, value }: { icon: React.ComponentProps<typeof Icon>["icon"]; label: string; value: string }): ReactElement {
  return (
    <div className="fw-trust-item">
      <Icon icon={icon} aria-hidden="true" />
      <span><strong>{label}</strong><small>{value}</small></span>
    </div>
  );
}

function StatusSignals({ change }: { change: FolderPlanMemberChange }): ReactElement | null {
  const signals: string[] = [];
  if (change.protected) {
    signals.push("Protected");
  } else if (change.change_classification !== "unchanged") {
    signals.push(CLASS_LABELS[change.change_classification]);
  }
  if (change.link_updated) {
    signals.push("Link updated");
  }
  if (signals.length === 0) {
    return null;
  }
  return (
    <span className="fw-status-signals">
      {signals.join(" · ")}
    </span>
  );
}

function treeItemLabel(
  node: TreeNode,
  change: FolderPlanMemberChange | undefined,
): string {
  if (!change) {
    return node.label;
  }
  const signals = [CLASS_LABELS[change.change_classification]];
  if (change.link_updated) {
    signals.push("supported link updated");
  }
  return `${node.label}, ${signals.join(", ")}`;
}

function buildTree(members: FolderPlanTreeMember[]): TreeNode[] {
  const root: TreeNode = {
    key: "root",
    label: "",
    path: "",
    depth: -1,
    kind: "directory",
    memberId: null,
    explicitEmptyDirectory: false,
    children: [],
  };
  const directories = new Map<string, TreeNode>([["", root]]);

  for (const member of members) {
    const pathParts = member.relative_path.split("/");
    const directoryPartCount = member.member_kind === "empty_directory" ? pathParts.length : pathParts.length - 1;
    let parent = root;
    for (let index = 0; index < directoryPartCount; index += 1) {
      const directoryPath = pathParts.slice(0, index + 1).join("/");
      let directory = directories.get(directoryPath);
      if (!directory) {
        directory = {
          key: `directory:${directoryPath}`,
          label: pathParts[index],
          path: directoryPath,
          depth: index,
          kind: "directory",
          memberId: null,
          explicitEmptyDirectory: false,
          children: [],
        };
        directories.set(directoryPath, directory);
        parent.children.push(directory);
      }
      if (member.member_kind === "empty_directory" && index === pathParts.length - 1) {
        directory.memberId = member.member_id;
        directory.explicitEmptyDirectory = true;
      }
      parent = directory;
    }
    if (member.member_kind === "regular_file") {
      parent.children.push({
        key: `file:${member.member_id}`,
        label: pathParts.at(-1) ?? member.relative_path,
        path: member.relative_path,
        depth: pathParts.length - 1,
        kind: "file",
        memberId: member.member_id,
        explicitEmptyDirectory: false,
        children: [],
      });
    }
  }

  sortTree(root);
  return root.children;
}

function sortTree(node: TreeNode): void {
  node.children.sort((left, right) => {
    if (left.kind !== right.kind) {
      return left.kind === "directory" ? -1 : 1;
    }
    return compareCodePointStrings(left.label, right.label);
  });
  node.children.forEach(sortTree);
}

function compareCodePointStrings(left: string, right: string): number {
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

function flattenVisibleTree(nodes: TreeNode[], expanded: Set<string>): TreeNode[] {
  const output: TreeNode[] = [];
  for (const node of nodes) {
    output.push(node);
    if (node.kind === "directory" && expanded.has(node.path)) {
      output.push(...flattenVisibleTree(node.children, expanded));
    }
  }
  return output;
}

function initialExpandedPaths(preview: FolderPlanPreviewV1): Set<string> {
  const paths = new Set<string>();
  for (const change of preview.member_changes.filter(isChanged)) {
    for (const path of [change.current_relative_path, change.proposed_relative_path]) {
      const parts = path.split("/");
      const count = change.member_kind === "empty_directory" ? parts.length : parts.length - 1;
      for (let index = 1; index <= count; index += 1) {
        paths.add(parts.slice(0, index).join("/"));
      }
    }
  }
  return paths;
}

function matchesFilter(change: FolderPlanMemberChange, filter: FilterKey): boolean {
  switch (filter) {
    case "moved":
      return change.change_classification === "moved" || change.change_classification === "moved_and_renamed";
    case "renamed":
      return change.change_classification === "renamed" || change.change_classification === "moved_and_renamed";
    case "link_updated":
      return change.link_updated;
    case "protected":
      return change.protected;
    case "unchanged":
      return change.change_classification === "unchanged";
    case "empty_directory":
      return change.member_kind === "empty_directory";
  }
}

function isChanged(change: FolderPlanMemberChange): boolean {
  return change.current_relative_path !== change.proposed_relative_path || change.link_updated;
}

function authorityLabel(change: FolderPlanMemberChange): string {
  if (change.authority_source === "change_file") {
    return "Imported Foldweave Change File";
  }
  if (change.authority_source === "gpt_plan") {
    return "Planning proposal, checked by deterministic code";
  }
  return "Protected by deterministic policy";
}

function toggleSetValue(current: Set<string>, value: string): Set<string> {
  const next = new Set(current);
  if (next.has(value)) {
    next.delete(value);
  } else {
    next.add(value);
  }
  return next;
}

function createIdempotencyKey(): string {
  if (typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
}

function formatCount(count: number, singular: string, plural = `${singular}s`): string {
  return `${count} ${count === 1 ? singular : plural}`;
}

export function ReviewLoading(): ReactElement {
  return <div className="fw-loading"><Spinner size={24} /><span>Loading preview…</span></div>;
}
