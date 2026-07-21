import "@blueprintjs/core/lib/css/blueprint.css";
import "./chatgpt-widget.css";

import {
  Button,
  Callout,
  Icon,
  NonIdealState,
  Spinner,
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
  type FoldweaveChangeFileResultV1,
  type FoldweaveChatGptReviewV1,
  type FoldweaveHostedJobStatusV1,
  type FoldweaveHostedRevisionRecoveryV1,
  type HostedRevisionParentBinding,
  type FoldweaveReconstructionResultV1,
  assertNoSensitiveBoundaryData,
  parseHostedChangeFileResult,
  parseHostedJobStatus,
  parseHostedRevisionRecovery,
  parseHostedReconstructionResult,
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
type TerminalAction = "verify" | "change_file" | "reconstruct" | null;
type ApplyOutcome = "applied" | "unchanged" | "older" | "rejected";

interface PendingRevisionContext {
  parentJobId: string;
  parentJobRevision: number;
  parentCandidateFingerprint: string;
  parentPreviewFingerprint: string;
  sourceCommitment: string;
}

interface ReservedRevisionContinuation {
  revisionJobId: string;
  modelContext: {
    content: string;
    structuredContent: Record<string, unknown>;
  };
  prompt: string;
}

const DEFAULT_HOST_RECOVERY_MS = 30_000;
const WIDGET_STATE_SCHEMA_VERSION = "foldweave-widget-state.v1";

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
  const [followUpDispatched, setFollowUpDispatched] = useState(false);
  const [revisionContinuationAvailable, setRevisionContinuationAvailable] =
    useState(false);
  const [continuationCopyNotice, setContinuationCopyNotice] = useState<
    string | null
  >(null);
  const [refreshing, setRefreshing] = useState(false);
  const [requiresRefresh, setRequiresRefresh] = useState(false);
  const [reconcileRequest, setReconcileRequest] = useState(0);
  const [verificationNotice, setVerificationNotice] = useState<string | null>(null);
  const [terminalAction, setTerminalAction] = useState<TerminalAction>(null);
  const [changeFileEvidence, setChangeFileEvidence] =
    useState<FoldweaveChangeFileResultV1 | null>(null);
  const [reconstructionEvidence, setReconstructionEvidence] =
    useState<FoldweaveReconstructionResultV1 | null>(null);
  const snapshotRef = useRef<FoldweaveChatGptReviewV1 | null>(null);
  const activeJobIdRef = useRef<string | null>(null);
  const pendingRevisionContextRef = useRef<PendingRevisionContext | null>(null);
  const reservedRevisionRef = useRef<ReservedRevisionContinuation | null>(null);
  const continuationTextRef = useRef<HTMLTextAreaElement | null>(null);
  const pendingActionRef = useRef<PendingAction>(null);
  const recoveryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const followUpDispatchedRef = useRef(false);
  const revisionStartedRef = useRef(false);
  const preserveRecoveryDiagnosticRef = useRef(false);
  const refreshingRef = useRef(false);
  const reconciledRequestRef = useRef(0);
  const mountRecoveryAttemptedRef = useRef(false);

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

  const clearReservedRevision = useCallback((): void => {
    reservedRevisionRef.current = null;
    setRevisionContinuationAvailable(false);
    setContinuationCopyNotice(null);
  }, []);

  const clearPersistedRevisionBinding = useCallback((): void => {
    void bridge
      .setWidgetState(emptyRevisionWidgetState())
      .catch(() => undefined);
  }, [bridge]);

  const scheduleRecoveryTimer = useCallback(
    (action: Exclude<PendingAction, null>): void => {
      clearRecoveryTimer();
      recoveryTimerRef.current = setTimeout(() => {
        if (pendingActionRef.current !== action) {
          return;
        }
        pendingActionRef.current = null;
        setPendingAction(null);
        setRequiresRefresh(true);
        if (action === "revision") {
          preserveRecoveryDiagnosticRef.current = true;
          setError(
            revisionStartedRef.current
              ? "Foldweave observed a submitted replacement state, but the revised preview did not return in time. Reconciling the durable job."
              : followUpDispatchedRef.current
                ? "The revision is reserved and ChatGPT accepted the follow-up, but Foldweave has not yet observed a submitted replacement. Reconciling the durable job."
                : "The revision is reserved, but its exact context has not completed delivery to ChatGPT. Foldweave is reconciling the durable job.",
          );
        } else {
          setError(
            "Foldweave did not return the completed action in time. Refresh to reconcile the durable job before continuing.",
          );
        }
        setReconcileRequest((current) => current + 1);
      }, hostRecoveryMs);
    },
    [clearRecoveryTimer, hostRecoveryMs],
  );

  const beginPendingAction = useCallback(
    (action: Exclude<PendingAction, null>): void => {
      if (action === "revision") {
        followUpDispatchedRef.current = false;
        revisionStartedRef.current = false;
        setFollowUpDispatched(false);
        setContinuationCopyNotice(null);
        preserveRecoveryDiagnosticRef.current = false;
      }
      setError(null);
      setRequiresRefresh(false);
      pendingActionRef.current = action;
      setPendingAction(action);
      scheduleRecoveryTimer(action);
    },
    [scheduleRecoveryTimer],
  );

  const handleRevisionStatus = useCallback(
    (status: FoldweaveHostedJobStatusV1): void => {
      const current = snapshotRef.current;
      const activeJobId = activeJobIdRef.current;
      const revisionContext = pendingRevisionContextRef.current;
      if (current === null || activeJobId === null || revisionContext === null) {
        throw new Error("Foldweave blocked an unbound hosted revision response.");
      }
      if (
        activeJobId !== current.status.job_id &&
        activeJobId !== status.job_id
      ) {
        throw new Error("Foldweave blocked an unrelated derivative job response.");
      }
      const statusIsValid =
        status.lifecycle === "revising"
          ? status.job_id === current.status.job_id
            ? isBoundSameJobRevisionStatus(current, status, revisionContext)
            : isBoundDerivativeStatus(current, status, revisionContext)
          : isBoundRevisionResultStatus(current, status, revisionContext);
      if (!statusIsValid) {
        throw new Error("Foldweave blocked an invalid hosted revision response.");
      }
      activeJobIdRef.current = status.job_id;
      if (status.lifecycle !== "revising") {
        revisionStartedRef.current = true;
        setFollowUpDispatched(true);
      }
      scheduleRecoveryTimer("revision");
      if (status.lifecycle !== "revising") {
        setReconcileRequest((request) => request + 1);
      }
    },
    [scheduleRecoveryTimer],
  );

  const abandonPendingRevision = useCallback((): void => {
    pendingRevisionContextRef.current = null;
    clearReservedRevision();
    followUpDispatchedRef.current = false;
    revisionStartedRef.current = false;
    setFollowUpDispatched(false);
    const current = snapshotRef.current;
    if (current !== null) {
      activeJobIdRef.current = current.status.job_id;
    }
  }, [clearReservedRevision]);

  const applyStructuredContent = useCallback(
    (value: unknown, reconcileSameVersion = false): ApplyOutcome => {
      try {
        const next = parseHostedReviewEnvelope(value);
        const current = snapshotRef.current;
        const activeJobId = activeJobIdRef.current;
        const sameJob = current?.status.job_id === next.status.job_id;
        if (current && !sameJob) {
          const revisionContext = pendingRevisionContextRef.current;
          if (!isBoundDerivativeReview(current, next, revisionContext)) {
            throw new Error("Foldweave blocked a different job from replacing this review.");
          }
          if (
            activeJobId !== null &&
            activeJobId !== current.status.job_id &&
            activeJobId !== next.status.job_id
          ) {
            throw new Error("Foldweave blocked an unrelated derivative job response.");
          }
        } else if (activeJobId !== null && activeJobId !== next.status.job_id) {
          throw new Error("Foldweave blocked a stale hosted job response.");
        }
        if (current && sameJob && next.state_version < current.state_version) {
          return "older";
        }
        if (
          current &&
          sameJob &&
          next.state_version === current.state_version &&
          (next.preview.preview_fingerprint !== current.preview.preview_fingerprint ||
            next.status.lifecycle !== current.status.lifecycle ||
            next.status.authorization_context_fingerprint !==
              current.status.authorization_context_fingerprint)
        ) {
          throw new Error("Foldweave blocked conflicting data for the same review version.");
        }
        if (current && sameJob && next.state_version === current.state_version) {
          if (reconcileSameVersion) {
            pendingRevisionContextRef.current = null;
            clearReservedRevision();
            if (!preserveRecoveryDiagnosticRef.current) {
              setError(null);
            }
            setRequiresRefresh(false);
            clearPendingAction();
            followUpDispatchedRef.current = false;
            revisionStartedRef.current = false;
            setFollowUpDispatched(false);
            preserveRecoveryDiagnosticRef.current = false;
            clearPersistedRevisionBinding();
          }
          return "unchanged";
        }
        activeJobIdRef.current = next.status.job_id;
        snapshotRef.current = next;
        pendingRevisionContextRef.current = null;
        clearReservedRevision();
        setSnapshot(next);
        setError(null);
        setRequiresRefresh(false);
        setVerificationNotice(null);
        setChangeFileEvidence(null);
        setReconstructionEvidence(null);
        clearPendingAction();
        followUpDispatchedRef.current = false;
        revisionStartedRef.current = false;
        setFollowUpDispatched(false);
        preserveRecoveryDiagnosticRef.current = false;
        if (current !== null) {
          clearPersistedRevisionBinding();
        }
        return "applied";
      } catch (caught) {
        setError(publicError(caught));
        setRequiresRefresh(snapshotRef.current !== null);
        clearPendingAction();
        return "rejected";
      }
    },
    [
      clearPendingAction,
      clearPersistedRevisionBinding,
      clearReservedRevision,
    ],
  );

  useEffect(() => {
    const unsubscribeResults = bridge.subscribeToolResults((value) => {
      const structuredContent = extractStructuredContent(value);
      const schema = foldweaveStructuredSchema(structuredContent);
      if (schema === "foldweave-chatgpt-review.v1") {
        applyStructuredContent(structuredContent);
      } else if (schema === "foldweave-hosted-job-status.v1") {
        try {
          const status = parseHostedJobStatus(structuredContent);
          if (
            pendingActionRef.current !== "revision" &&
            reservedRevisionRef.current === null
          ) {
            return;
          }
          handleRevisionStatus(status);
        } catch (caught) {
          setError(publicError(caught));
          setRequiresRefresh(snapshotRef.current !== null);
          if (reservedRevisionRef.current !== null) {
            preserveRecoveryDiagnosticRef.current = true;
            setRevisionContinuationAvailable(true);
          }
          clearPendingAction();
        }
      }
    });
    const unsubscribeInterruptions = bridge.subscribeInterruptions(
      (interruption: HostInterruption) => {
        const revisionWasReserved = reservedRevisionRef.current !== null;
        if (!revisionWasReserved) {
          abandonPendingRevision();
        } else {
          preserveRecoveryDiagnosticRef.current = true;
          setRevisionContinuationAvailable(true);
        }
        clearPendingAction();
        const hasSnapshot = snapshotRef.current !== null;
        setRequiresRefresh(hasSnapshot);
        setError(interruptionMessage(interruption, hasSnapshot));
        if (hasSnapshot && (interruption === "tool_cancelled" || revisionWasReserved)) {
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
  }, [
    abandonPendingRevision,
    applyStructuredContent,
    bridge,
    clearPendingAction,
    handleRevisionStatus,
    scheduleRecoveryTimer,
  ]);

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

  const callJobBoundTool = useCallback(
    async (
      name: string,
      expectedJobId: string,
      argumentsValue: Record<string, unknown>,
    ): Promise<unknown> => {
      const activeJobId = activeJobIdRef.current;
      if (activeJobId === null || activeJobId !== expectedJobId) {
        throw new Error("Foldweave blocked a stale hosted job action.");
      }
      const boundArguments = bindHostedJobToolArguments(
        activeJobId,
        argumentsValue,
      );
      assertNoSensitiveBoundaryData(boundArguments);
      try {
        return await bridge.callTool(name, boundArguments);
      } catch (caught) {
        throw new Error(publicError(caught));
      }
    },
    [bridge],
  );

  const recoverMountedRevision = useCallback(
    async (binding: PendingRevisionContext): Promise<void> => {
      const current = snapshotRef.current;
      if (current === null || refreshingRef.current) {
        return;
      }
      if (!isCurrentRevisionParent(current, binding)) {
        setRequiresRefresh(true);
        setError(
          "Foldweave blocked saved revision state for another visible review.",
        );
        return;
      }
      refreshingRef.current = true;
      setRefreshing(true);
      setRequiresRefresh(true);
      pendingRevisionContextRef.current = binding;
      try {
        const response = await callJobBoundTool(
          "recover_revision",
          binding.parentJobId,
          recoveryToolArguments(binding),
        );
        const structuredContent = extractStructuredContent(response);
        if (structuredContent === undefined) {
          throw new Error("Foldweave did not return hosted revision recovery data.");
        }
        const recovered = parseHostedRevisionRecovery(
          structuredContent,
          toHostedParentBinding(binding),
        );
        if (recovered.recovery_status === "none") {
          pendingRevisionContextRef.current = null;
          activeJobIdRef.current = current.status.job_id;
          clearReservedRevision();
          clearPersistedRevisionBinding();
          setRequiresRefresh(false);
          setError(null);
          return;
        }
        const status = recovered.status!;
        activeJobIdRef.current = status.job_id;
        if (status.lifecycle === "revising") {
          handleRevisionStatus(status);
          const modelContext = createRevisionModelContext(
            current,
            recovered.revision_instruction!,
            binding.parentCandidateFingerprint,
            binding.parentPreviewFingerprint,
            status,
            recovered.submit_call_id!,
          );
          const prompt = createRevisionPrompt(
            recovered.revision_instruction!,
            status,
            recovered.submit_call_id!,
          );
          assertNoSensitiveBoundaryData(modelContext);
          assertNoSensitiveBoundaryData(prompt);
          reservedRevisionRef.current = {
            revisionJobId: status.job_id,
            modelContext,
            prompt,
          };
          clearPendingAction();
          preserveRecoveryDiagnosticRef.current = true;
          setRevisionContinuationAvailable(true);
          setRequiresRefresh(false);
          setError(
            "The revision is safely reserved. Copy the prepared continuation below, paste it into the ChatGPT composer, and send it.",
          );
          return;
        }
        if (
          !status.has_preview ||
          status.preview_fingerprint === null ||
          status.candidate_fingerprint === null
        ) {
          throw new Error(
            "Foldweave recovered a completed revision without its exact preview.",
          );
        }
        const previewResponse = await callJobBoundTool(
          "get_plan_preview",
          status.job_id,
          {
            expected_revision: status.job_revision,
            preview_fingerprint: status.preview_fingerprint,
          },
        );
        const outcome = applyToolResponse(previewResponse, true);
        if (outcome !== "applied" && outcome !== "unchanged") {
          throw new Error("Foldweave did not return the recovered revision preview.");
        }
      } catch (caught) {
        clearPendingAction();
        preserveRecoveryDiagnosticRef.current = false;
        setRequiresRefresh(true);
        setError(publicError(caught));
      } finally {
        refreshingRef.current = false;
        setRefreshing(false);
      }
    },
    [
      applyToolResponse,
      callJobBoundTool,
      clearPendingAction,
      clearPersistedRevisionBinding,
      clearReservedRevision,
      handleRevisionStatus,
    ],
  );

  useEffect(() => {
    if (snapshot === null || mountRecoveryAttemptedRef.current) {
      return;
    }
    mountRecoveryAttemptedRef.current = true;
    try {
      const binding = parsePendingRevisionWidgetState(bridge.getWidgetState());
      if (binding !== null) {
        void recoverMountedRevision(binding);
      }
    } catch (caught) {
      setRequiresRefresh(true);
      setError(publicError(caught));
    }
  }, [bridge, recoverMountedRevision, snapshot]);

  const reconcileDurableJob = useCallback(async (): Promise<void> => {
    const current = snapshotRef.current;
    const activeJobId = activeJobIdRef.current;
    if (current === null || activeJobId === null || refreshingRef.current) {
      return;
    }
    refreshingRef.current = true;
    setRefreshing(true);
    setVerificationNotice(null);
    try {
      const statusResponse = await callJobBoundTool(
        "job_status",
        activeJobId,
        {},
      );
      const statusContent = extractStructuredContent(statusResponse);
      if (statusContent === undefined) {
        throw new Error("Foldweave did not return its durable hosted status.");
      }
      const durableStatus = parseHostedJobStatus(
        statusContent,
        activeJobId,
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
        durableStatus.lifecycle === "revising" &&
        reservedRevisionRef.current?.revisionJobId === durableStatus.job_id &&
        pendingRevisionContextRef.current !== null
      ) {
        clearPendingAction();
        preserveRecoveryDiagnosticRef.current = true;
        setRequiresRefresh(false);
        setRevisionContinuationAvailable(true);
        setError(
          "The revision is safely reserved, but ChatGPT has not submitted the replacement. Copy the prepared continuation below, paste it into the ChatGPT composer, and send it. Foldweave will not send a second follow-up or reserve another revision.",
        );
        return;
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
      const previewResponse = await callJobBoundTool(
        "get_plan_preview",
        durableStatus.job_id,
        {
          expected_revision: durableStatus.job_revision,
          preview_fingerprint: durableStatus.preview_fingerprint,
        },
      );
      const outcome = applyToolResponse(previewResponse, true);
      if (outcome !== "applied" && outcome !== "unchanged") {
        throw new Error("Foldweave did not return a complete reconciled preview.");
      }
    } catch (caught) {
      clearPendingAction();
      preserveRecoveryDiagnosticRef.current = false;
      setRequiresRefresh(true);
      setError(publicError(caught));
    } finally {
      refreshingRef.current = false;
      setRefreshing(false);
    }
  }, [applyToolResponse, callJobBoundTool, clearPendingAction]);

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
      pendingRevisionContextRef.current = null;
      beginPendingAction("accept");
      const response = await callJobBoundTool(
        "accept_plan_and_create_copy",
        snapshot.status.job_id,
        {
          ...exactPreviewBinding(snapshot),
          ...binding,
        },
      ).catch((caught: unknown) => {
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
      callJobBoundTool,
      clearPendingAction,
      snapshot,
    ],
  );

  const revisePlan = useCallback(
    async (payload: RevisionPayload): Promise<void> => {
      if (pendingActionRef.current !== null) {
        return;
      }
      if (!snapshot) {
        throw new Error("Foldweave has no exact preview to revise.");
      }
      if (payload.instruction.length > 2_000) {
        throw new Error("Foldweave revision instructions are limited to 2,000 characters.");
      }
      assertNoSensitiveBoundaryData(payload);
      pendingRevisionContextRef.current = {
        parentJobId: snapshot.status.job_id,
        parentJobRevision: snapshot.status.job_revision,
        parentCandidateFingerprint: snapshot.preview.compiled_candidate_fingerprint,
        parentPreviewFingerprint: snapshot.preview.preview_fingerprint,
        sourceCommitment: snapshot.preview.source_commitment,
      };
      beginPendingAction("revision");
      let revisionReserved = false;
      try {
        const reservationResponse = await callJobBoundTool(
          "revise_plan",
          snapshot.status.job_id,
          {
            candidate_fingerprint: payload.candidate_fingerprint,
            expected_revision: payload.expected_revision,
            idempotency_key: payload.idempotency_key,
            instruction: payload.instruction,
            preview_fingerprint: payload.preview_fingerprint,
          },
        );
        const reservationContent = extractStructuredContent(reservationResponse);
        if (reservationContent === undefined) {
          throw new Error("Foldweave did not return a durable revision reservation.");
        }
        const reservationStatus = parseHostedJobStatus(reservationContent);
        handleRevisionStatus(reservationStatus);
        revisionReserved = true;
        const submitCallId = revisionSubmitCallId(reservationStatus);
        const modelContext = createRevisionModelContext(
          snapshot,
          payload.instruction,
          payload.candidate_fingerprint,
          payload.preview_fingerprint,
          reservationStatus,
          submitCallId,
        );
        assertNoSensitiveBoundaryData(modelContext);
        const prompt = createRevisionPrompt(
          payload.instruction,
          reservationStatus,
          submitCallId,
        );
        assertNoSensitiveBoundaryData(prompt);
        reservedRevisionRef.current = {
          revisionJobId: reservationStatus.job_id,
          modelContext,
          prompt,
        };
        setRevisionContinuationAvailable(false);
        await bridge.setWidgetState(
          encodePendingRevisionWidgetState(
            pendingRevisionContextRef.current!,
          ),
        );
        await bridge.updateModelContext(
          modelContext.content,
          modelContext.structuredContent,
        );
        await bridge.sendFollowUpMessage(prompt);
        if (pendingActionRef.current === "revision") {
          followUpDispatchedRef.current = true;
          setFollowUpDispatched(true);
        }
      } catch (caught) {
        if (!revisionReserved) {
          abandonPendingRevision();
        } else {
          preserveRecoveryDiagnosticRef.current = true;
          setRequiresRefresh(false);
          setRevisionContinuationAvailable(true);
        }
        clearPendingAction();
        throw new Error(publicError(caught));
      }
    },
    [
      abandonPendingRevision,
      beginPendingAction,
      bridge,
      callJobBoundTool,
      clearPendingAction,
      handleRevisionStatus,
      snapshot,
    ],
  );

  const copyRevisionContinuation = useCallback(async (): Promise<void> => {
    const continuation = reservedRevisionRef.current;
    if (continuation === null) {
      setRequiresRefresh(true);
      setError(
        "Foldweave could not recover the exact reserved continuation. Check revision status before continuing.",
      );
      return;
    }
    try {
      if (navigator.clipboard === undefined) {
        throw new Error("Clipboard unavailable");
      }
      await navigator.clipboard.writeText(continuation.prompt);
      setContinuationCopyNotice(
        "Copied. Paste it into the ChatGPT composer and press Return.",
      );
    } catch {
      continuationTextRef.current?.focus();
      continuationTextRef.current?.select();
      setContinuationCopyNotice(
        "The continuation is selected. Copy it, paste it into the ChatGPT composer, and press Return.",
      );
    }
  }, []);

  const keepPrevious = useCallback(
    async (payload: KeepProposalPayload): Promise<void> => {
      if (!snapshot) {
        throw new Error("Foldweave has no previous proposal to keep.");
      }
      pendingRevisionContextRef.current = null;
      beginPendingAction("keep");
      const response = await callJobBoundTool(
        "keep_previous_proposal",
        snapshot.status.job_id,
        {
          ...exactPreviewBinding(snapshot),
          ...payload,
        },
      ).catch((caught: unknown) => {
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
      callJobBoundTool,
      clearPendingAction,
      snapshot,
    ],
  );

  const refresh = useCallback(async (): Promise<void> => {
    const binding = parsePendingRevisionWidgetState(bridge.getWidgetState());
    if (
      binding !== null &&
      activeJobIdRef.current === binding.parentJobId
    ) {
      await recoverMountedRevision(binding);
      return;
    }
    await reconcileDurableJob();
  }, [bridge, reconcileDurableJob, recoverMountedRevision]);

  const verifyResult = useCallback(async (): Promise<void> => {
    if (!snapshot || snapshot.status.lifecycle !== "verified") {
      return;
    }
    if (refreshingRef.current) {
      return;
    }
    refreshingRef.current = true;
    setRefreshing(true);
    setTerminalAction("verify");
    setVerificationNotice(null);
    setError(null);
    try {
      const response = await callJobBoundTool(
        "verify_result",
        snapshot.status.job_id,
        {
          organized_tree_commitment: snapshot.result!.organized_tree_commitment,
        },
      );
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
      setTerminalAction(null);
    }
  }, [callJobBoundTool, snapshot]);

  const getChangeFile = useCallback(async (): Promise<void> => {
    if (
      !snapshot ||
      snapshot.status.lifecycle !== "verified" ||
      snapshot.result?.change_file_fingerprint === null ||
      snapshot.result?.change_file_fingerprint === undefined ||
      refreshingRef.current
    ) {
      return;
    }
    refreshingRef.current = true;
    setRefreshing(true);
    setTerminalAction("change_file");
    setError(null);
    try {
      const response = await callJobBoundTool(
        "get_change_file",
        snapshot.status.job_id,
        {},
      );
      const structuredContent = extractStructuredContent(response);
      if (structuredContent === undefined) {
        throw new Error("Foldweave did not return the verified Change File identity.");
      }
      const evidence = parseHostedChangeFileResult(
        structuredContent,
        snapshot.status.job_id,
        snapshot.result.change_file_fingerprint,
      );
      setChangeFileEvidence(evidence);
    } catch (caught) {
      setError(publicError(caught));
    } finally {
      refreshingRef.current = false;
      setRefreshing(false);
      setTerminalAction(null);
    }
  }, [callJobBoundTool, snapshot]);

  const recreateOriginal = useCallback(async (): Promise<void> => {
    if (!snapshot || snapshot.status.lifecycle !== "verified" || refreshingRef.current) {
      return;
    }
    refreshingRef.current = true;
    setRefreshing(true);
    setTerminalAction("reconstruct");
    setError(null);
    try {
      const response = await callJobBoundTool(
        "recreate_original",
        snapshot.status.job_id,
        {},
      );
      const structuredContent = extractStructuredContent(response);
      if (structuredContent === undefined) {
        throw new Error("Foldweave did not return verified reconstruction evidence.");
      }
      const evidence = parseHostedReconstructionResult(
        structuredContent,
        snapshot.status.job_id,
        snapshot.preview.source_commitment,
        snapshot.result!.complete_file_count,
      );
      setReconstructionEvidence(evidence);
    } catch (caught) {
      setError(publicError(caught));
    } finally {
      refreshingRef.current = false;
      setRefreshing(false);
      setTerminalAction(null);
    }
  }, [callJobBoundTool, snapshot]);

  const hostStatus = useMemo(() => {
    if (pendingAction === "revision") {
      return followUpDispatched
        ? "Follow-up accepted. Waiting for ChatGPT to return a revised structure…"
        : "Revision reserved. Sending the exact context to ChatGPT…";
    }
    if (pendingAction === "accept") {
      return "Creating and verifying copy…";
    }
    if (pendingAction === "keep") {
      return "Restoring previous proposal…";
    }
    return null;
  }, [followUpDispatched, pendingAction]);

  const manualContinuationPrompt = revisionContinuationAvailable
    ? (reservedRevisionRef.current?.prompt ?? null)
    : null;

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
          <span>Waiting for preview…</span>
        </div>
      </WidgetShell>
    );
  }

  return (
    <WidgetShell>
      <header className="fw-chatgpt-header">
        <div className="fw-chatgpt-title">
          <strong>Review structure</strong>
          <span className="fw-chatgpt-provenance">
            <Icon icon="cloud" aria-hidden="true" />
            <span>ChatGPT</span>
            <span aria-hidden="true">·</span>
            <span>No direct API key used</span>
          </span>
        </div>
        <div className="fw-chatgpt-header-actions">
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
      {revisionContinuationAvailable && manualContinuationPrompt !== null && (
        <Callout intent="warning" title="Finish the reserved revision in ChatGPT">
          <p>
            Foldweave preserved the current preview and its single revision
            reservation. Copy the exact continuation below, paste it into this
            conversation&apos;s ChatGPT composer, and send it. Foldweave will not
            send another follow-up or create another reservation.
          </p>
          <label htmlFor="foldweave-revision-continuation">
            Prepared continuation
          </label>
          <textarea
            className="bp6-input bp6-fill"
            id="foldweave-revision-continuation"
            readOnly
            ref={continuationTextRef}
            rows={5}
            spellCheck={false}
            value={manualContinuationPrompt}
          />
          <div className="fw-chatgpt-header-actions">
            <Button
              disabled={refreshing || pendingAction !== null}
              icon="duplicate"
              onClick={() => void copyRevisionContinuation()}
              small
            >
              Copy continuation
            </Button>
            <Button
              disabled={refreshing || pendingAction !== null}
              icon="refresh"
              loading={refreshing}
              onClick={() => void reconcileDurableJob()}
              small
            >
              Check revision status
            </Button>
          </div>
          {continuationCopyNotice && (
            <p role="status">{continuationCopyNotice}</p>
          )}
        </Callout>
      )}
      {hostStatus && <div className="fw-chatgpt-progress" role="status">{hostStatus}</div>}
      {snapshot.status.lifecycle === "executing" ? (
        <section className="fw-chatgpt-terminal-state">
          <Spinner size={28} />
          <h2>Creating copy</h2>
        </section>
      ) : snapshot.status.lifecycle === "verified" ? (
        <VerifiedResult
          changeFileEvidence={changeFileEvidence}
          notice={verificationNotice}
          onGetChangeFile={() => void getChangeFile()}
          onRecreateOriginal={() => void recreateOriginal()}
          onVerify={() => void verifyResult()}
          reconstructionEvidence={reconstructionEvidence}
          refreshing={refreshing}
          snapshot={snapshot}
          terminalAction={terminalAction}
        />
      ) : (
        <ReviewIsland
          acceptanceScopeFingerprint={
            snapshot.status.authorization_context_fingerprint
          }
          acceptPlan={acceptPlan}
          actionsDisabled={
            pendingAction !== null ||
            requiresRefresh ||
            revisionContinuationAvailable
          }
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
  changeFileEvidence,
  reconstructionEvidence,
  onVerify,
  onGetChangeFile,
  onRecreateOriginal,
  terminalAction,
}: {
  snapshot: FoldweaveChatGptReviewV1;
  refreshing: boolean;
  notice: string | null;
  changeFileEvidence: FoldweaveChangeFileResultV1 | null;
  reconstructionEvidence: FoldweaveReconstructionResultV1 | null;
  onVerify: () => void;
  onGetChangeFile: () => void;
  onRecreateOriginal: () => void;
  terminalAction: TerminalAction;
}): ReactElement {
  const result = snapshot.result!;
  return (
    <section className="fw-chatgpt-terminal-state is-verified">
      <div className="fw-verified-mark">
        <Icon icon="tick-circle" aria-hidden="true" />
        <span>Verified</span>
      </div>
      <h2>Your new folder is ready</h2>
      <p>
        {result.complete_file_count} files accounted for; {result.changed_path_count} paths
        changed. The selected source remained unchanged.
      </p>
      {notice && <div className="fw-terminal-notice" role="status">{notice}</div>}
      {changeFileEvidence && (
        <section className="fw-terminal-evidence" role="status">
          <h3>Foldweave Change File ready</h3>
          <strong>{changeFileEvidence.item.display_name}</strong>
          <details>
            <summary>Proof details</summary>
            <p>Local item <code>{changeFileEvidence.item.handle}</code></p>
            <p>Change File <code>{changeFileEvidence.change_file_fingerprint}</code></p>
            <p>Receipt <code>{changeFileEvidence.originating_receipt_fingerprint}</code></p>
          </details>
        </section>
      )}
      {reconstructionEvidence && (
        <section className="fw-terminal-evidence" role="status">
          <h3>Original layout recreated and verified</h3>
          <strong>{reconstructionEvidence.item.display_name}</strong>
          <p>{reconstructionEvidence.restored_file_count} files · {reconstructionEvidence.restored_empty_directory_count} empty folders</p>
          <details>
            <summary>Proof details</summary>
            <p>Local item <code>{reconstructionEvidence.item.handle}</code></p>
            <p>Receipt <code>{reconstructionEvidence.receipt_fingerprint}</code></p>
          </details>
        </section>
      )}
      <div className="fw-chatgpt-terminal-actions">
        <Button
          disabled={refreshing}
          loading={terminalAction === "verify"}
          onClick={onVerify}
        >
          Verify again
        </Button>
        <Button
          disabled={refreshing || result.change_file_fingerprint === null}
          loading={terminalAction === "change_file"}
          onClick={onGetChangeFile}
        >
          Get Change File
        </Button>
        <Button
          disabled={refreshing}
          loading={terminalAction === "reconstruct"}
          onClick={onRecreateOriginal}
        >
          Recreate original
        </Button>
      </div>
    </section>
  );
}

function WidgetShell({ children }: { children: React.ReactNode }): ReactElement {
  return <main className="fw-chatgpt-widget">{children}</main>;
}

function bindHostedJobToolArguments(
  jobId: string,
  argumentsValue: Record<string, unknown>,
): Record<string, unknown> {
  if ("job_id" in argumentsValue) {
    throw new Error("Foldweave blocked a caller-supplied hosted job identity.");
  }
  return { job_id: jobId, ...argumentsValue };
}

function isCurrentRevisionParent(
  current: FoldweaveChatGptReviewV1,
  revisionContext: PendingRevisionContext | null,
): revisionContext is PendingRevisionContext {
  return (
    revisionContext !== null &&
    current.status.job_id === revisionContext.parentJobId &&
    current.status.job_revision === revisionContext.parentJobRevision &&
    current.preview.compiled_candidate_fingerprint ===
      revisionContext.parentCandidateFingerprint &&
    current.preview.preview_fingerprint === revisionContext.parentPreviewFingerprint &&
    current.preview.source_commitment === revisionContext.sourceCommitment
  );
}

function isBoundSameJobRevisionStatus(
  current: FoldweaveChatGptReviewV1,
  status: FoldweaveHostedJobStatusV1,
  revisionContext: PendingRevisionContext | null,
): boolean {
  return (
    isCurrentRevisionParent(current, revisionContext) &&
    status.job_id === revisionContext.parentJobId &&
    status.lifecycle === "revising" &&
    status.job_revision > current.status.job_revision &&
    status.proposal_revision === current.status.proposal_revision &&
    status.source_commitment === revisionContext.sourceCommitment &&
    status.planning_basis === current.status.planning_basis &&
    status.model_transport === "chatgpt_hosted" &&
    status.execution_origin === current.status.execution_origin &&
    status.has_preview === true &&
    status.candidate_fingerprint === revisionContext.parentCandidateFingerprint &&
    status.preview_fingerprint === revisionContext.parentPreviewFingerprint
  );
}

function isBoundDerivativeStatus(
  current: FoldweaveChatGptReviewV1,
  status: FoldweaveHostedJobStatusV1,
  revisionContext: PendingRevisionContext | null,
): boolean {
  return (
    isCurrentRevisionParent(current, revisionContext) &&
    current.journey === "apply" &&
    status.job_id !== revisionContext.parentJobId &&
    status.lifecycle === "revising" &&
    status.planning_basis === "derivative" &&
    status.model_transport === "chatgpt_hosted" &&
    status.execution_origin === "none" &&
    status.source_commitment === revisionContext.sourceCommitment &&
    status.has_preview === false &&
    status.candidate_fingerprint === null &&
    status.preview_fingerprint === null
  );
}

function isBoundRevisionResultStatus(
  current: FoldweaveChatGptReviewV1,
  status: FoldweaveHostedJobStatusV1,
  revisionContext: PendingRevisionContext | null,
): boolean {
  if (
    !isCurrentRevisionParent(current, revisionContext) ||
    (status.lifecycle !== "reviewing" &&
      status.lifecycle !== "revision_failed") ||
    status.job_revision <= current.status.job_revision ||
    status.source_commitment !== revisionContext.sourceCommitment ||
    status.model_transport !== "chatgpt_hosted" ||
    status.has_preview !== true ||
    status.candidate_fingerprint === null ||
    status.preview_fingerprint === null
  ) {
    return false;
  }
  const sameJob = status.job_id === revisionContext.parentJobId;
  if (
    sameJob
      ? status.planning_basis !== current.status.planning_basis
      : current.journey !== "apply" || status.planning_basis !== "derivative"
  ) {
    return false;
  }
  const executionOriginIsBound = sameJob
    ? status.execution_origin === current.status.execution_origin
    : status.lifecycle === "reviewing"
      ? status.execution_origin === "gpt_revised_from_change_file"
      : status.execution_origin === "none";
  if (!executionOriginIsBound) {
    return false;
  }
  return status.lifecycle === "reviewing"
    ? status.proposal_revision === current.status.proposal_revision + 1
    : status.proposal_revision === current.status.proposal_revision;
}

function isBoundDerivativeReview(
  current: FoldweaveChatGptReviewV1,
  next: FoldweaveChatGptReviewV1,
  revisionContext: PendingRevisionContext | null,
): boolean {
  const completedDerivative =
    next.preview.proposal_basis === "gpt_derivative" &&
    next.preview.immediate_parent_candidate_fingerprint ===
      revisionContext?.parentCandidateFingerprint;
  const parentShapedFailedDerivative =
    (next.status.lifecycle === "revision_failed" ||
      next.status.lifecycle === "reviewing") &&
    next.preview.proposal_basis === "imported_change_file" &&
    next.preview.immediate_parent_candidate_fingerprint === null &&
    next.preview.compiled_candidate_fingerprint ===
      revisionContext?.parentCandidateFingerprint;
  return (
    isCurrentRevisionParent(current, revisionContext) &&
    current.journey === "apply" &&
    next.journey === "apply" &&
    next.status.job_id !== revisionContext.parentJobId &&
    next.status.planning_basis === "derivative" &&
    next.status.model_transport === "chatgpt_hosted" &&
    (completedDerivative
      ? next.status.execution_origin === "gpt_revised_from_change_file"
      : next.status.execution_origin === "none") &&
    (completedDerivative || parentShapedFailedDerivative) &&
    next.preview.source_commitment === revisionContext.sourceCommitment &&
    next.preview.imported_change_file_fingerprint ===
      current.preview.imported_change_file_fingerprint
  );
}

function toHostedParentBinding(
  context: PendingRevisionContext,
): HostedRevisionParentBinding {
  return {
    parent_job_id: context.parentJobId,
    parent_job_revision: context.parentJobRevision,
    parent_candidate_fingerprint: context.parentCandidateFingerprint,
    parent_preview_fingerprint: context.parentPreviewFingerprint,
    source_commitment: context.sourceCommitment,
  };
}

function recoveryToolArguments(
  context: PendingRevisionContext,
): Record<string, unknown> {
  const binding = toHostedParentBinding(context);
  return {
    parent_job_revision: binding.parent_job_revision,
    parent_candidate_fingerprint: binding.parent_candidate_fingerprint,
    parent_preview_fingerprint: binding.parent_preview_fingerprint,
    source_commitment: binding.source_commitment,
  };
}

function encodePendingRevisionWidgetState(
  context: PendingRevisionContext,
): Record<string, unknown> {
  return {
    schema_version: WIDGET_STATE_SCHEMA_VERSION,
    pending_revision: toHostedParentBinding(context),
  };
}

function emptyRevisionWidgetState(): Record<string, unknown> {
  return {
    schema_version: WIDGET_STATE_SCHEMA_VERSION,
    pending_revision: null,
  };
}

function parsePendingRevisionWidgetState(
  value: unknown,
): PendingRevisionContext | null {
  if (value === undefined || value === null) {
    return null;
  }
  assertNoSensitiveBoundaryData(value);
  if (
    typeof value !== "object" ||
    Array.isArray(value) ||
    value === null ||
    !("schema_version" in value) ||
    value.schema_version !== WIDGET_STATE_SCHEMA_VERSION ||
    !("pending_revision" in value)
  ) {
    throw new Error("Foldweave blocked invalid saved revision state.");
  }
  const pending = value.pending_revision;
  if (pending === null) {
    return null;
  }
  if (
    typeof pending !== "object" ||
    Array.isArray(pending) ||
    pending === null ||
    !("parent_job_id" in pending) ||
    typeof pending.parent_job_id !== "string" ||
    !/^[a-f0-9]{32}$/.test(pending.parent_job_id) ||
    !("parent_job_revision" in pending) ||
    !Number.isInteger(pending.parent_job_revision) ||
    (pending.parent_job_revision as number) < 0 ||
    !("parent_candidate_fingerprint" in pending) ||
    typeof pending.parent_candidate_fingerprint !== "string" ||
    !/^[a-f0-9]{64}$/.test(pending.parent_candidate_fingerprint) ||
    !("parent_preview_fingerprint" in pending) ||
    typeof pending.parent_preview_fingerprint !== "string" ||
    !/^[a-f0-9]{64}$/.test(pending.parent_preview_fingerprint) ||
    !("source_commitment" in pending) ||
    typeof pending.source_commitment !== "string" ||
    !/^[a-f0-9]{64}$/.test(pending.source_commitment)
  ) {
    throw new Error("Foldweave blocked incomplete saved revision state.");
  }
  return {
    parentJobId: pending.parent_job_id,
    parentJobRevision: pending.parent_job_revision as number,
    parentCandidateFingerprint: pending.parent_candidate_fingerprint,
    parentPreviewFingerprint: pending.parent_preview_fingerprint,
    sourceCommitment: pending.source_commitment,
  };
}

function exactPreviewBinding(
  snapshot: FoldweaveChatGptReviewV1,
): Record<string, unknown> {
  return {
    proposal_revision: snapshot.preview.proposal_revision,
    source_commitment: snapshot.preview.source_commitment,
    imported_change_file_fingerprint:
      snapshot.preview.imported_change_file_fingerprint,
    match_report_fingerprint: snapshot.preview.match_report_fingerprint,
    authorization_context_fingerprint:
      snapshot.status.authorization_context_fingerprint,
  };
}

function createRevisionModelContext(
  snapshot: FoldweaveChatGptReviewV1,
  instruction: string,
  baseCandidateFingerprint: string,
  basePreviewFingerprint: string,
  reservationStatus: FoldweaveHostedJobStatusV1,
  submitCallId: string,
): { content: string; structuredContent: Record<string, unknown> } {
  return {
    content:
      "The user requested a Foldweave revision. The deterministic engine has " +
      "already reserved the exact revision job. Produce one sparse replacement " +
      "with submit_plan_revision using folder-host-plan-revision.v1. Under " +
      "revision.entries use replacement_target_path; target_path is invalid. " +
      "Return the replacement for review. Do not execute or accept it.",
    structuredContent: {
      schema_version: "foldweave-host-revision-context.v1",
      revision_job_id: reservationStatus.job_id,
      revision_job_revision: reservationStatus.job_revision,
      parent_job_id: snapshot.status.job_id,
      base_candidate_fingerprint: baseCandidateFingerprint,
      base_preview_fingerprint: basePreviewFingerprint,
      submit_call_id: submitCallId,
      instruction,
      permitted_evidence_ids: ["initial_inventory"],
      members: snapshot.preview.member_changes.map((member) => ({
        file_id: member.member_id,
        member_kind: member.member_kind,
        current_relative_path: member.current_relative_path,
        proposed_relative_path: member.proposed_relative_path,
        protected: member.protected,
      })),
      constraints: {
        submit_tool: "submit_plan_revision",
        revision_schema_version: "folder-host-plan-revision.v1",
        base_candidate_fingerprint: baseCandidateFingerprint,
        revision_top_level_fields: [
          "schema_version",
          "base_candidate_fingerprint",
          "replacement_result_folder_name",
          "entries",
        ],
        revision_entry_fields: [
          "file_id",
          "replacement_target_path",
          "rationale",
          "evidence_ids",
        ],
        forbidden_revision_entry_fields: ["target_path"],
        replacement_result_folder_name_default: null,
        entries_sorted_by: "file_id",
        entries_unique_by: "file_id",
        unlisted_members: "preserved_from_base_candidate",
        path_only_evidence_ids: ["initial_inventory"],
        execute: false,
        accept: false,
        direct_api: false,
      },
    },
  };
}

function createRevisionPrompt(
  instruction: string,
  reservationStatus: FoldweaveHostedJobStatusV1,
  submitCallId: string,
): string {
  return [
    `Foldweave revision job ${reservationStatus.job_id} is already reserved; do not call revise_plan again.`,
    `Revise this Foldweave proposal as follows: ${JSON.stringify(instruction)}.`,
    `Call submit_plan_revision once with call_id ${submitCallId}.`,
    "Submit schema folder-host-plan-revision.v1 with the exact base_candidate_fingerprint from the Foldweave model context.",
    "Under revision.entries include only changed members; each entry must contain exactly file_id, replacement_target_path, rationale, and evidence_ids; target_path is invalid.",
    'Set every evidence_ids to ["initial_inventory"] and keep replacement_result_folder_name null unless the user explicitly requested a result-folder rename.',
    "Sort revision.entries by file_id; unlisted members remain unchanged.",
    "The exact member bindings are in the Foldweave model context.",
    "Return the revised structure for review; Foldweave will fetch the durable status and preview.",
    "Do not execute or accept it.",
  ].join(" ");
}

function revisionSubmitCallId(status: FoldweaveHostedJobStatusV1): string {
  return `revision-submit:${status.job_id}:${status.job_revision}`;
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
