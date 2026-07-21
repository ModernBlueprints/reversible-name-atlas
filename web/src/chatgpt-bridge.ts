export interface ChatGptHostBridge {
  connect(): Promise<void>;
  getInitialStructuredContent(): unknown;
  getWidgetState(): unknown;
  setWidgetState(state: Record<string, unknown>): Promise<void>;
  subscribeToolResults(listener: (structuredContent: unknown) => void): () => void;
  subscribeInterruptions(listener: (interruption: HostInterruption) => void): () => void;
  callTool(name: string, argumentsValue: Record<string, unknown>): Promise<unknown>;
  updateModelContext(
    content: string,
    structuredContent: Record<string, unknown>,
  ): Promise<void>;
  sendFollowUpMessage(prompt: string): Promise<void>;
}

export type HostInterruption = "tool_cancelled" | "resource_teardown";

interface PendingRequest {
  resolve: (value: unknown) => void;
  reject: (reason: Error) => void;
  timeoutId: ReturnType<typeof setTimeout>;
}

interface JsonRpcRecord {
  jsonrpc: "2.0";
  id?: number;
  method?: string;
  params?: unknown;
  result?: unknown;
  error?: unknown;
}

const PROTOCOL_VERSION = "2026-01-26";
const DEFAULT_TIMEOUT_MS = 20_000;
const MAX_TOOL_RESULT_WRAPPERS = 3;
const FOLDWEAVE_STRUCTURED_SCHEMAS = new Set([
  "foldweave-chatgpt-review.v1",
  "foldweave-change-file-result.v1",
  "foldweave-hosted-job-status.v1",
  "foldweave-hosted-revision-recovery.v1",
  "foldweave-reconstruction-result.v1",
  "foldweave-verification-result.v1",
]);

export class McpAppsHostBridge implements ChatGptHostBridge {
  private readonly pendingRequests = new Map<number, PendingRequest>();
  private readonly subscribers = new Set<(structuredContent: unknown) => void>();
  private readonly interruptionSubscribers = new Set<
    (interruption: HostInterruption) => void
  >();
  private nextRequestId = 0;
  private initialized: Promise<void> | null = null;
  private standardBridgeReady = false;
  private listening = false;

  constructor(
    private readonly hostWindow: Window = window,
    private readonly requestTimeoutMs: number = DEFAULT_TIMEOUT_MS,
  ) {}

  async connect(): Promise<void> {
    this.startListening();
    try {
      await this.initialize();
      this.standardBridgeReady = true;
    } catch (error) {
      this.standardBridgeReady = false;
      if (this.hostWindow.openai !== undefined) {
        return;
      }
      throw error;
    }
  }

  getInitialStructuredContent(): unknown {
    return this.hostWindow.openai?.toolOutput;
  }

  getWidgetState(): unknown {
    return this.hostWindow.openai?.widgetState;
  }

  async setWidgetState(state: Record<string, unknown>): Promise<void> {
    const setter = this.hostWindow.openai?.setWidgetState;
    if (typeof setter !== "function") {
      throw new Error("The ChatGPT host does not support durable widget state.");
    }
    await setter(state);
  }

  subscribeToolResults(listener: (structuredContent: unknown) => void): () => void {
    this.startListening();
    this.subscribers.add(listener);
    return () => this.subscribers.delete(listener);
  }

  subscribeInterruptions(
    listener: (interruption: HostInterruption) => void,
  ): () => void {
    this.startListening();
    this.interruptionSubscribers.add(listener);
    return () => this.interruptionSubscribers.delete(listener);
  }

  dispose(): void {
    if (this.listening) {
      this.hostWindow.removeEventListener("message", this.handleMessage);
      this.hostWindow.removeEventListener("openai:set_globals", this.handleSetGlobals);
      this.listening = false;
    }
    for (const [id, pending] of this.pendingRequests) {
      clearTimeout(pending.timeoutId);
      pending.reject(new Error("The Foldweave widget bridge was closed."));
      this.pendingRequests.delete(id);
    }
    this.subscribers.clear();
    this.interruptionSubscribers.clear();
  }

  async callTool(
    name: string,
    argumentsValue: Record<string, unknown>,
  ): Promise<unknown> {
    const transport = await this.selectTransport("callTool");
    if (transport === "compatibility") {
      return this.hostWindow.openai!.callTool!(name, argumentsValue);
    }
    return this.request("tools/call", { name, arguments: argumentsValue });
  }

  async updateModelContext(
    content: string,
    structuredContent: Record<string, unknown>,
  ): Promise<void> {
    if (!this.standardBridgeReady) {
      try {
        await this.initialize();
        this.standardBridgeReady = true;
      } catch (error) {
        this.standardBridgeReady = false;
        // Widget-local persistence is not model-visible context. Report the
        // unsupported operation truthfully; the caller may continue with a
        // self-contained follow-up after its durable reservation succeeds.
        throw error;
      }
    }
    await this.request("ui/update-model-context", {
      content: [{ type: "text", text: content }],
      structuredContent,
    });
  }

  async sendFollowUpMessage(prompt: string): Promise<void> {
    if (!this.standardBridgeReady) {
      try {
        await this.initialize();
        this.standardBridgeReady = true;
      } catch (error) {
        this.standardBridgeReady = false;
        const compatibilityRuntime = this.hostWindow.openai;
        if (typeof compatibilityRuntime?.sendFollowUpMessage === "function") {
          await compatibilityRuntime.sendFollowUpMessage({
            prompt,
            scrollToBottom: true,
          });
          return;
        }
        throw error;
      }
    }
    // ui/message is an MCP Apps request. Await the host response so the widget
    // never reports a follow-up as accepted after a silently discarded
    // notification. Do not retry through the compatibility extension after a
    // request error or timeout because delivery is then ambiguous.
    const result = await this.request("ui/message", {
      role: "user",
      content: [{ type: "text", text: prompt }],
    });
    if (!isRecord(result)) {
      throw new Error("The ChatGPT host returned a malformed follow-up response.");
    }
    if (result.isError === true) {
      throw new Error("The ChatGPT host rejected the follow-up message.");
    }
  }

  private async selectTransport(
    requiredCompatibilityMethod: "callTool" | "sendFollowUpMessage",
  ): Promise<"mcp-apps" | "compatibility"> {
    if (this.standardBridgeReady) {
      return "mcp-apps";
    }
    try {
      await this.initialize();
      this.standardBridgeReady = true;
      return "mcp-apps";
    } catch (error) {
      this.standardBridgeReady = false;
      if (typeof this.hostWindow.openai?.[requiredCompatibilityMethod] === "function") {
        return "compatibility";
      }
      throw error;
    }
  }

  private initialize(): Promise<void> {
    this.startListening();
    if (this.initialized === null) {
      const attempt = this.request("ui/initialize", {
        appInfo: { name: "foldweave-review-widget", version: "0.1.0" },
        appCapabilities: {},
        protocolVersion: PROTOCOL_VERSION,
      }).then(() => {
        this.notify("ui/notifications/initialized", {});
      });
      this.initialized = attempt;
      void attempt.catch(() => {
        if (this.initialized === attempt) {
          this.initialized = null;
        }
      });
    }
    return this.initialized;
  }

  private startListening(): void {
    if (this.listening) {
      return;
    }
    this.listening = true;
    this.hostWindow.addEventListener("message", this.handleMessage, {
      passive: true,
    });
    this.hostWindow.addEventListener("openai:set_globals", this.handleSetGlobals, {
      passive: true,
    });
  }

  private readonly handleMessage = (event: MessageEvent<unknown>): void => {
    if (event.source !== this.hostWindow.parent || !isJsonRpcRecord(event.data)) {
      return;
    }
    const message = event.data;
    if (message.method === "ui/resource-teardown" && typeof message.id === "number") {
      this.emitInterruption("resource_teardown");
      this.hostWindow.parent.postMessage(
        { jsonrpc: "2.0", id: message.id, result: {} },
        "*",
      );
      return;
    }
    if (message.method === "ui/notifications/tool-cancelled") {
      this.emitInterruption("tool_cancelled");
      return;
    }
    if (message.method === "ui/notifications/tool-result") {
      const structuredContent = extractStructuredContent(message.params);
      if (structuredContent !== undefined) {
        this.emitStructuredContent(structuredContent);
      }
      return;
    }
    if (message.method === undefined && typeof message.id === "number") {
      const pending = this.pendingRequests.get(message.id);
      if (!pending) {
        return;
      }
      clearTimeout(pending.timeoutId);
      this.pendingRequests.delete(message.id);
      const hasError = message.error !== undefined;
      const hasResult = message.result !== undefined;
      if (hasError === hasResult) {
        pending.reject(new Error("The ChatGPT host returned a malformed response."));
      } else if (hasError) {
        pending.reject(new Error("The ChatGPT host rejected the widget request."));
      } else {
        pending.resolve(message.result);
      }
      return;
    }
  };

  private readonly handleSetGlobals = (event: Event): void => {
    const detail = (event as CustomEvent<unknown>).detail;
    if (!isRecord(detail) || !isRecord(detail.globals)) {
      return;
    }
    const structuredContent = detail.globals.toolOutput;
    if (structuredContent !== undefined && structuredContent !== null) {
      this.emitStructuredContent(structuredContent);
    }
  };

  private emitStructuredContent(structuredContent: unknown): void {
    for (const subscriber of this.subscribers) {
      subscriber(structuredContent);
    }
  }

  private emitInterruption(interruption: HostInterruption): void {
    for (const subscriber of this.interruptionSubscribers) {
      subscriber(interruption);
    }
  }

  private request(method: string, params: unknown): Promise<unknown> {
    const id = ++this.nextRequestId;
    return new Promise((resolve, reject) => {
      const timeoutId = setTimeout(() => {
        this.pendingRequests.delete(id);
        reject(new Error("The ChatGPT host did not answer the widget request in time."));
      }, this.requestTimeoutMs);
      this.pendingRequests.set(id, { resolve, reject, timeoutId });
      this.hostWindow.parent.postMessage({ jsonrpc: "2.0", id, method, params }, "*");
    });
  }

  private notify(method: string, params: unknown): void {
    this.hostWindow.parent.postMessage({ jsonrpc: "2.0", method, params }, "*");
  }
}

export function extractStructuredContent(value: unknown): unknown {
  let candidate = value;
  for (let wrapperDepth = 0; wrapperDepth <= MAX_TOOL_RESULT_WRAPPERS; wrapperDepth += 1) {
    if (!isRecord(candidate)) {
      return undefined;
    }
    if (candidate.isError === true || candidate.is_error === true) {
      return undefined;
    }
    if (
      typeof candidate.schema_version === "string" &&
      FOLDWEAVE_STRUCTURED_SCHEMAS.has(candidate.schema_version)
    ) {
      return candidate;
    }
    if (wrapperDepth === MAX_TOOL_RESULT_WRAPPERS) {
      return undefined;
    }
    const wrapper = candidate;
    const wrapperKeys = ["structuredContent", "structured_content", "result"].filter(
      (key) => key in wrapper,
    );
    if (wrapperKeys.length !== 1) {
      return undefined;
    }
    candidate = wrapper[wrapperKeys[0]!];
  }
  return undefined;
}

export function foldweaveStructuredSchema(value: unknown): string | null {
  const structuredContent = extractStructuredContent(value);
  if (
    !isRecord(structuredContent) ||
    typeof structuredContent.schema_version !== "string"
  ) {
    return null;
  }
  return structuredContent.schema_version;
}

function isJsonRpcRecord(value: unknown): value is JsonRpcRecord {
  return isRecord(value) && value.jsonrpc === "2.0";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
