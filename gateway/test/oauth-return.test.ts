import { describe, expect, it } from "vitest";

import { authorizationCompletionResponse } from "../src/oauth-return";

const CODE = "authorization_code_secret_test_value";
const STATE = "oauth_state_secret_test_value";
const CALLBACK_PATH = "/connector/oauth/-xrCKl1aOw6M";
const CHATGPT_CALLBACK =
  `https://chatgpt.com${CALLBACK_PATH}?code=${CODE}&state=${STATE}`;

function visibleText(html: string): string {
  return html
    .replace(/<style\b[^>]*>[\s\S]*?<\/style>/giu, " ")
    .replace(/<[^>]+>/gu, " ")
    .replace(/\s+/gu, " ")
    .trim();
}

describe("OAuth authorization return", () => {
  it("uses a user-initiated bridge only for the exact ChatGPT callback", async () => {
    const response = authorizationCompletionResponse(CHATGPT_CALLBACK);

    expect(response.status).toBe(200);
    expect(response.headers.get("location")).toBeNull();
    expect(response.headers.get("content-type")).toBe("text/html; charset=utf-8");
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(response.headers.get("referrer-policy")).toBe("no-referrer");
    expect(response.headers.get("set-cookie")).toBe(
      "__Host-fw_oauth_csrf=; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=0",
    );
    const csp = response.headers.get("content-security-policy") ?? "";
    expect(csp).toContain("default-src 'none'");
    expect(csp).toContain("form-action 'none'");
    expect(csp).toContain(
      `navigate-to https://chatgpt.com${CALLBACK_PATH}`,
    );
    expect(csp).not.toContain(CODE);
    expect(csp).not.toContain(STATE);

    const body = await response.text();
    expect(body).toContain(
      `href="https://chatgpt.com${CALLBACK_PATH}?code=${CODE}&amp;state=${STATE}"`,
    );
    expect(body).not.toContain("http-equiv=\"refresh\"");
    expect(body).not.toContain("<script");
    const text = visibleText(body);
    expect(text).toContain("Return to ChatGPT");
    expect(text).not.toContain(CODE);
    expect(text).not.toContain(STATE);
  });

  it.each([
    `https://chatgpt.com.evil.example${CALLBACK_PATH}?code=${CODE}&state=${STATE}`,
    `https://evil.example${CALLBACK_PATH}?code=${CODE}&state=${STATE}`,
    `http://chatgpt.com${CALLBACK_PATH}?code=${CODE}&state=${STATE}`,
    `https://chatgpt.com/connector/oauth.evil/-xrCKl1aOw6M?code=${CODE}&state=${STATE}`,
    `https://chatgpt.com${CALLBACK_PATH}/extra?code=${CODE}&state=${STATE}`,
    `https://chatgpt.com${CALLBACK_PATH}?code=${CODE}`,
    `https://chatgpt.com${CALLBACK_PATH}?code=${CODE}&state=${STATE}&next=evil`,
    `https://chatgpt.com${CALLBACK_PATH}?code=${CODE}&code=duplicate&state=${STATE}`,
  ])("keeps a lookalike or malformed callback on the ordinary redirect path: %s", (url) => {
    const response = authorizationCompletionResponse(url);

    expect(response.status).toBe(302);
    expect(response.headers.get("location")).toBe(url);
    expect(response.headers.get("content-type")).toBeNull();
  });

  it("keeps ordinary OAuth clients on the existing redirect behavior", () => {
    const redirectTo =
      "https://client.example/oauth/callback?code=ordinary_code&state=ordinary_state";
    const response = authorizationCompletionResponse(redirectTo);

    expect(response.status).toBe(302);
    expect(response.headers.get("location")).toBe(redirectTo);
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(response.headers.get("referrer-policy")).toBe("no-referrer");
    expect(response.headers.get("set-cookie")).toBe(
      "__Host-fw_oauth_csrf=; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=0",
    );
  });

  it("does not bridge the published legacy ChatGPT redirect", () => {
    const redirectTo =
      `https://chatgpt.com/connector_platform_oauth_redirect?code=${CODE}&state=${STATE}`;
    const response = authorizationCompletionResponse(redirectTo);

    expect(response.status).toBe(302);
    expect(response.headers.get("location")).toBe(redirectTo);
  });
});
