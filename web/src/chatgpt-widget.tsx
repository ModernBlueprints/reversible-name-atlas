import "@blueprintjs/core/lib/css/blueprint.css";
import "./chatgpt-widget.css";

import {
  Button,
  Callout,
  Card,
  NonIdealState,
  Spinner,
  Tag,
} from "@blueprintjs/core";
import {
  StrictMode,
  type ReactElement,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { createRoot } from "react-dom/client";

import {
  type ChatGptHostBridge,
  type HostInterruption,
  McpAppsHostBridge,
  extractStructuredContent,
  foldweaveStructuredSchema,
} from "./chatgpt-bridge";
import {
  type FoldweaveChatGptReviewV1,
  assertNoSensitiveBoundaryData,
  parseHostedJobStatus,
  parseHostedReviewEnvelope,
  parseHostedVerificationResult,
} from "./chatgpt-contracts";
import type {
  AcceptanceBindingPayload,
  KeepProposalPayload,
  RevisionPayload,
} from "./contracts";
import { ReviewIsland } from "./review-island";

type PendingAction = "accept" | "revision" | "keep" | null;
type ApplyOutcome = "applied" | "unchanged" | "older" | "rejected";

const DEFAULT_HOST_RECOVERY_MS = 60_000;

export interface FoldweaveChatGptWidgetProps {
  bridge: ChatGptHostBridge;
  hostRecoveryMs?: number;
}

export function FoldweaveChatGptWidget({
  bridge,
  hostRecoveryMs = DEFAULT_HOST_RECOVERY_MS,
}: FoldweaveChatGptWidgetProps): ReactElement {
  const [snapshot, setSnapshot] = useState<FoldweaveChatGptReviewV1 | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pendingAction, setPendingAction] = useState<PendingAction>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [requiresRefresh, setRequiresRefresh] = useState(false);
  const [reconcileRequest, setReconcileRequest] = useState(0);
  const [verificationNotice, setVerificationNotice] = useState<string | null>(null);
  const snapshotRef = useRef<FoldweaveChatGptReviewV1 | null>(null);
  const pendingActionRef = useRef<PendingAction>(null);
  const recoveryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const refreshingRef = useRef(false);
  const reconciledRequestRef = useRef(0);

  const clearRecoveryTimer = useCallback((): void => {
    if (recoveryTimerRef.current !== null) {
      clearTimeout(recoveryTimerRef.current);
      recoveryTimerRef.current = null;
    }
  }, []);

  const clearPendingAction = useCallback((): void => {
    clearRecoveryTimer();
    pendingActionRef.current = null;
    setPendingAction(null);
  }, [clearRecoveryTimer]);

  const beginPendingAction = useCallback(
    (action: Exclude<PendingAction, null>): void => {
      clearRecoveryTimer();
      pendingActionRef.current = action;
      setPendingAction(action);
      recoveryTimerRef.current = setTimeout(() => {
        if (pendingActionRef.current !== action) {
          return;
        }
        pendingActionRef.current = null;
        setPendingAction(null);
        setRequiresRefresh(true);
        setError(
          action === "revision"
            ? "ChatGPT did not return a revised preview in time. Refresh to reconcile the durable job before continuing."
            : "Foldweave did not return the completed action in time. Refresh to reconcile the durable job before continuing.",
        );
        setReconcileRequest((current) => current + 1);
      }, hostRecoveryMs);
    },
    [clearRecoveryTimer, hostRecoveryMs],
  );

  const applyStructuredContent = useCallback(
    (value: unknown, reconcileSameVersion = false): ApplyOutcome => {
      try {
        const next = parseHostedReviewEnvelope(value);
        const current = snapshotRef.current;
        if (current && current.status.job_id !== next.status.job_id) {
          throw new Error("Foldweave blocked a different job from replacing this review.");
        }
        if (current && next.state_version < current.state_version) {
          return "older";
        }
        if (
          current &&
          next.state_version === current.state_version &&
          (next.preview.preview_fingerprint !== current.preview.preview_fingerprint ||
            next.status.lifecycle !== current.status.lifecycle ||
            next.status.authorization_context_fingerprint !==
              current.status.authorization_context_fingerprint)
        ) {
          throw new Error("Foldweave blocked conflicting data for the same review version.");
        }
        if (current && next.state_version === current.state_version) {
          if (reconcileSameVersion) {
            setError(null);
            setRequiresRefresh(false);
            clearPendingAction();
          }
          return "unchanged";
        }
        snapshotRef.current = next;
        setSnapshot(next);
        setError(null);
        setRequiresRefresh(false);
        clearPendingAction();
        return "applied";
      } catch (caught) {
        setError(publicError(caught));
        setRequiresRefresh(snapshotRef.current !== null);
        clearPendingAction();
        return "rejected";
      }
    },
    [clearPendingAction],
  );

  useEffect(() => {
    const unsubscribeResults = bridge.subscribeToolResults((value) => {
      const structuredContent = extractStructuredContent(value);
      if (
        foldweaveStructuredSchema(structuredContent) ===
        "foldweave-chatgpt-review.v1"
      ) {
        applyStructuredContent(structuredContent);
      }
    });
    const unsubscribeInterruptions = bridge.subscribeInterruptions(
      (interruption: HostInterruption) => {
        clearPendingAction();
        const hasSnapshot = snapshotRef.current !== null;
        setRequiresRefresh(hasSnapshot);
        setError(interruptionMessage(interruption, hasSnapshot));
        if (hasSnapshot && interruption === "tool_cancelled") {
          setReconcileRequest((current) => current + 1);
        }
      },
    );
    const initial = bridge.getInitialStructuredContent();
    if (initial !== undefined && initial !== null) {
      const structuredContent = extractStructuredContent(initial);
      if (
        foldweaveStructuredSchema(structuredContent) ===
        "foldweave-chatgpt-review.v1"
      ) {
        applyStructuredContent(structuredContent);
      }
    }
    void bridge.connect().catch((caught: unknown) => {
      if (snapshotRef.current === null) {
        setError(publicError(caught));
      }
    });
    return () => {
      unsubscribeResults();
      unsubscribeInterruptions();
    };
  }, [applyStructuredContent, bridge, clearPendingAction]);

  useEffect(() => clearRecoveryTimer, [clearRecoveryTimer]);

  const applyToolResponse = useCallback(
    (response: unknown, reconcileSameVersion = false): ApplyOutcome | "missing" => {
      const structuredContent = extractStructuredContent(response);
      if (structuredContent === undefined) {
        return "missing";
      }
      return applyStructuredContent(structuredContent, reconcileSameVersion);
    },
    [applyStructuredContent],
  );

  const callBoundTool = useCallback(
    async (name: string, argumentsValue: Record<string, unknown>): Promise<unknown> => {
      assertNoSensitiveBoundaryData(argumentsValue);
      try {
        return await bridge.callTool(name, argumentsValue);
      } catch (caught) {
        throw new Error(publicError(caught));
      }
    },
    [bridge],
  );

  const reconcileDurableJob = useCallback(async (): Promise<void> => {
    const current = snapshotRef.current;
    if (current === null || refreshingRef.current) {
      return;
    }
    refreshingRef.current = true;
    setRefreshing(true);
    setVerificationNotice(null);
    try {
      const statusResponse = await callBoundTool("job_status", {
        job_id: current.status.job_id,
        channel: "chatgpt_hosted",
      });
      const statusContent = extractStructuredContent(statusResponse);
      if (statusContent === undefined) {
        throw new Error("Foldweave did not return its durable hosted status.");
      }
      const durableStatus = parseHostedJobStatus(
        statusContent,
        current.status.job_id,
      );
      if (durableStatus.source_commitment !== current.preview.source_commitment) {
        throw new Error("Foldweave blocked a durable status for a different source.");
      }
      if (durableStatus.lifecycle === "stale") {
        throw new Error(
          "Foldweave marked this job stale because its source or Change File changed. Start a fresh job.",
        );
      }
      if (durableStatus.lifecycle === "blocked") {
        throw new Error("Foldweave marked this hosted job blocked. Start a fresh job.");
      }
      if (
        !durableStatus.has_preview ||
        durableStatus.preview_fingerprint === null ||
        durableStatus.candidate_fingerprint === null ||
        (durableStatus.lifecycle !== "reviewing" &&
          durableStatus.lifecycle !== "revision_failed" &&
          durableStatus.lifecycle !== "executing" &&
          durableStatus.lifecycle !== "verified")
      ) {
        throw new Error(
          "Foldweave is still waiting for a complete reviewable proposal. Refresh again after ChatGPT finishes.",
        );
      }
      const previewResponse = await callBoundTool("get_plan_preview", {
        job_id: durableStatus.job_id,
        expected_revision: durableStatus.job_revision,
        preview_fingerprint: durableStatus.preview_fingerprint,
        channel: "chatgpt_hosted",
      });
      const outcome = applyToolResponse(previewResponse, true);
      if (outcome !== "applied" && outcome !== "unchanged") {
        throw new Error("Foldweave did not return a complete reconciled preview.");
      }
    } catch (caught) {
      clearPendingAction();
      setRequiresRefresh(true);
      setError(publicError(caught));
    } finally {
      refreshingRef.current = false;
      setRefreshing(false);
    }
  }, [applyToolResponse, callBoundTool, clearPendingAction]);

  useEffect(() => {
    if (reconcileRequest === reconciledRequestRef.current) {
      return;
    }
    reconciledRequestRef.current = reconcileRequest;
    void reconcileDurableJob();
  }, [reconcileDurableJob, reconcileRequest]);

  const acceptPlan = useCallback(
    async (binding: AcceptanceBindingPayload): Promise<void> => {
      if (!snapshot) {
        throw new Error("Foldweave has no exact preview to accept.");
      }
      beginPendingAction("accept");
      const response = await callBoundTool("accept_plan_and_create_copy", {
        ...exactPreviewBinding(snapshot),
        ...binding,
        channel: "chatgpt_hosted",
      }).catch((caught: unknown) => {
        clearPendingAction();
        setRequiresRefresh(true);
        throw caught;
      });
      const outcome = applyToolResponse(response);
      if (outcome !== "applied") {
        clearPendingAction();
        setRequiresRefresh(true);
        throw new Error(
          "Foldweave did not return the completed exact acceptance. Refresh before continuing.",
        );
      }
    },
    [
      applyToolResponse,
      beginPendingAction,
      callBoundTool,
      clearPendingAction,
      snapshot,
    ],
  );

  const revisePlan = useCallback(
    async (payload: RevisionPayload): Promise<void> => {
      if (!snapshot) {
        throw new Error("Foldweave has no exact preview to revise.");
      }
      if (payload.instruction.length > 2_000) {
        throw new Error("Foldweave revision instructions are limited to 2,000 characters.");
      }
      assertNoSensitiveBoundaryData(payload);
      const prompt = createRevisionPrompt(snapshot, payload);
      assertNoSensitiveBoundaryData(prompt);
      beginPendingAction("revision");
      try {
        await bridge.sendFollowUpMessage(prompt);
      } catch (caught) {
        clearPendingAction();
        throw new Error(publicError(caught));
      }
    },
    [beginPendingAction, bridge, clearPendingAction, snapshot],
  );

  const keepPrevious = useCallback(
    async (payload: KeepProposalPayload): Promise<void> => {
      if (!snapshot) {
        throw new Error("Foldweave has no previous proposal to keep.");
      }
      beginPendingAction("keep");
      const response = await callBoundTool("keep_previous_proposal", {
        ...exactPreviewBinding(snapshot),
        ...payload,
        channel: "chatgpt_hosted",
      }).catch((caught: unknown) => {
        clearPendingAction();
        setRequiresRefresh(true);
        throw caught;
      });
      if (applyToolResponse(response) !== "applied") {
        clearPendingAction();
        setRequiresRefresh(true);
        throw new Error("Foldweave did not return the preserved proposal.");
      }
    },
    [
      applyToolResponse,
      beginPendingAction,
      callBoundTool,
      clearPendingAction,
      snapshot,
    ],
  );

  const refresh = useCallback(async (): Promise<void> => {
    await reconcileDurableJob();
  }, [reconcileDurableJob]);

  const verifyResult = useCallback(async (): Promise<void> => {
    if (!snapshot || snapshot.status.lifecycle !== "verified") {
      return;
    }
    if (refreshingRef.current) {
      return;
    }
    refreshingRef.current = true;
    setRefreshing(true);
    setVerificationNotice(null);
    setError(null);
    try {
      const response = await callBoundTool("verify_result", {
        job_id: snapshot.status.job_id,
        organized_tree_commitment: snapshot.result!.organized_tree_commitment,
        channel: "chatgpt_hosted",
      });
      const structuredContent = extractStructuredContent(response);
      if (structuredContent === undefined) {
        throw new Error("Foldweave did not return independent verification evidence.");
      }
      parseHostedVerificationResult(
        structuredContent,
        snapshot.status.job_id,
        snapshot.result!.organized_tree_commitment,
      );
      setVerificationNotice("Independent verification passed again.");
    } catch (caught) {
      setError(publicError(caught));
    } finally {
      refreshingRef.current = false;
      setRefreshing(false);
    }
  }, [callBoundTool, snapshot]);

  const hostStatus = useMemo(() => {
    if (pendingAction === "revision") {
      return "ChatGPT is revising the exact proposal with the host model.";
    }
    if (pendingAction === "accept") {
      return "The paired Foldweave app is creating and verifying the separate copy.";
    }
    if (pendingAction === "keep") {
      return "Foldweave is restoring the previous valid proposal.";
    }
    return null;
  }, [pendingAction]);

  if (!snapshot) {
    if (error) {
      return (
        <WidgetShell>
          <NonIdealState icon="error" title="Review unavailable" description={error} />
        </WidgetShell>
      );
    }
    return (
      <WidgetShell>
        <div className="fw-chatgpt-loading">
          <Spinner size={24} />
          <span>Waiting for the complete Foldweave preview…</span>
        </div>
      </WidgetShell>
    );
  }

  return (
    <WidgetShell>
      <header className="fw-chatgpt-header">
        <div>
          <span className="fw-eyebrow">CHATGPT-HOSTED PLANNING</span>
          <h1>Review the weave</h1>
          <p>The host model proposes. Your paired Foldweave app checks and executes.</p>
        </div>
        <div className="fw-chatgpt-header-actions">
          <Tag intent="success">No direct API key used</Tag>
          <Button
            disabled={refreshing}
            loading={refreshing}
            onClick={() => void refresh()}
            small
          >
            Refresh
          </Button>
        </div>
      </header>
      {error && (
        <Callout intent="danger" role="alert">
          {error}
          {requiresRefresh && " Review actions remain locked until Refresh reconciles the durable job."}
        </Callout>
      )}
      {hostStatus && <Callout intent="primary" role="status">{hostStatus}</Callout>}
      {snapshot.status.lifecycle === "executing" ? (
        <Card className="fw-chatgpt-terminal-state">
          <Spinner size={28} />
          <h2>Creating the separate copy</h2>
          <p>The exact accepted preview is executing in the paired local app.</p>
        </Card>
      ) : snapshot.status.lifecycle === "verified" ? (
        <VerifiedResult
          notice={verificationNotice}
          onVerify={() => void verifyResult()}
          refreshing={refreshing}
          snapshot={snapshot}
        />
      ) : (
        <ReviewIsland
          acceptanceScopeFingerprint={
            snapshot.status.authorization_context_fingerprint
          }
          acceptPlan={acceptPlan}
          actionsDisabled={pendingAction !== null || requiresRefresh}
          journey={snapshot.journey}
          keepPrevious={keepPrevious}
          preview={snapshot.preview}
          revisePlan={revisePlan}
          status={snapshot.status}
        />
      )}
    </WidgetShell>
  );
}

function VerifiedResult({
  snapshot,
  refreshing,
  notice,
  onVerify,
}: {
  snapshot: FoldweaveChatGptReviewV1;
  refreshing: boolean;
  notice: string | null;
  onVerify: () => void;
}): ReactElement {
  const result = snapshot.result!;
  return (
    <Card className="fw-chatgpt-terminal-state is-verified">
      <Tag intent="success" large>Verified</Tag>
      <h2>Your new folder is ready</h2>
      <p>
        {result.complete_file_count} files accounted for; {result.changed_path_count} paths
        changed. The selected source remained unchanged.
      </p>
      {notice && <Callout intent="success" role="status">{notice}</Callout>}
      <Button disabled={refreshing} loading={refreshing} onClick={onVerify}>
        Verify again
      </Button>
    </Card>
  );
}

function WidgetShell({ children }: { children: React.ReactNode }): ReactElement {
  return <main className="fw-chatgpt-widget bp6-dark">{children}</main>;
}

function exactPreviewBinding(
  snapshot: FoldweaveChatGptReviewV1,
): Record<string, unknown> {
  return {
    job_id: snapshot.status.job_id,
    proposal_revision: snapshot.preview.proposal_revision,
    source_commitment: snapshot.preview.source_commitment,
    imported_change_file_fingerprint:
      snapshot.preview.imported_change_file_fingerprint,
    match_report_fingerprint: snapshot.preview.match_report_fingerprint,
    authorization_context_fingerprint:
      snapshot.status.authorization_context_fingerprint,
  };
}

function createRevisionPrompt(
  snapshot: FoldweaveChatGptReviewV1,
  payload: RevisionPayload,
): string {
  return [
    `Revise Foldweave planning job ${snapshot.status.job_id}.`,
    `Use the Foldweave host-planning tools and bind the replacement to job revision ${payload.expected_revision}, candidate ${payload.candidate_fingerprint}, and preview ${payload.preview_fingerprint}.`,
    `Reuse this exact idempotency key: ${payload.idempotency_key}.`,
    "Submit one complete mechanically checked replacement preview; do not execute it and do not call the Foldweave direct Responses API.",
    `The user's requested change is: ${JSON.stringify(payload.instruction)}.`,
  ].join("\n");
}

function interruptionMessage(
  interruption: HostInterruption,
  hasSnapshot: boolean,
): string {
  if (interruption === "resource_teardown") {
    return "The ChatGPT host closed this Foldweave review surface.";
  }
  return hasSnapshot
    ? "The ChatGPT host cancelled the active Foldweave operation."
    : "The ChatGPT host cancelled the Foldweave review before a complete preview arrived.";
}

function publicError(caught: unknown): string {
  const fallback = "Foldweave could not complete the ChatGPT-hosted action.";
  if (!(caught instanceof Error) || caught.message.length === 0) {
    return fallback;
  }
  try {
    assertNoSensitiveBoundaryData(caught.message);
  } catch {
    return fallback;
  }
  return caught.message.startsWith("Foldweave") ||
    caught.message.startsWith("The Foldweave") ||
    caught.message.startsWith("The ChatGPT")
    ? caught.message
    : fallback;
}

const mount = document.getElementById("foldweave-chatgpt-widget-root");
if (mount) {
  createRoot(mount).render(
    <StrictMode>
      <FoldweaveChatGptWidget bridge={new McpAppsHostBridge()} />
    </StrictMode>,
  );
}
