declare module "*.css";

interface OpenAIWidgetRuntime {
  toolOutput?: unknown;
  callTool?: (
    name: string,
    argumentsValue: Record<string, unknown>,
  ) => Promise<unknown>;
  sendFollowUpMessage?: (request: {
    prompt: string;
    scrollToBottom?: boolean;
  }) => Promise<void> | void;
}

interface Window {
  openai?: OpenAIWidgetRuntime;
}
