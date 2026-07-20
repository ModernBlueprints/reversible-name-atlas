import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig(({ command, mode }) => {
  const isChatGptWidget = mode === "chatgpt-widget";
  const assetPrefix = isChatGptWidget ? "foldweave-chatgpt-widget" : "review";

  return {
    define:
      command === "build"
        ? { "process.env.NODE_ENV": JSON.stringify("production") }
        : undefined,
    plugins: [react()],
    build: {
      assetsInlineLimit: 1_000_000,
      cssCodeSplit: false,
      emptyOutDir: true,
      lib: {
        entry: isChatGptWidget ? "src/chatgpt-widget.tsx" : "src/main.tsx",
        formats: ["es"],
        fileName: () => `${assetPrefix}.js`,
      },
      outDir: isChatGptWidget
        ? "../src/name_atlas/assets/chatgpt-widget"
        : "../src/name_atlas/static/review",
      rollupOptions: {
        output: {
          codeSplitting: false,
          assetFileNames: (assetInfo) =>
            assetInfo.names.some((name) => name.endsWith(".css"))
              ? `${assetPrefix}.css`
              : "[name][extname]",
        },
      },
    },
    test: {
      environment: "jsdom",
      setupFiles: ["./src/test-setup.ts"],
      css: false,
    },
  };
});
