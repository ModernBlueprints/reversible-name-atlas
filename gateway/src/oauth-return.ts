import { securityHeaders } from "./http";

const CHATGPT_OAUTH_ORIGIN = "https://chatgpt.com";
const CHATGPT_CALLBACK_PATH = /^\/connector\/oauth\/[A-Za-z0-9_-]+$/u;
const MAX_CALLBACK_URL_LENGTH = 4096;
const MAX_AUTHORIZATION_CODE_LENGTH = 2048;
const MAX_STATE_LENGTH = 1024;
const CSRF_COOKIE_CLEAR =
  "__Host-fw_oauth_csrf=; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=0";

function hasSingleBoundedParameter(
  params: URLSearchParams,
  name: string,
  maximumLength: number,
): boolean {
  const values = params.getAll(name);
  return (
    values.length === 1 &&
    values[0] !== undefined &&
    values[0].length > 0 &&
    values[0].length <= maximumLength
  );
}

function isExactChatGptCallback(url: URL): boolean {
  if (
    url.origin !== CHATGPT_OAUTH_ORIGIN ||
    url.username !== "" ||
    url.password !== "" ||
    url.hash !== "" ||
    !CHATGPT_CALLBACK_PATH.test(url.pathname)
  ) {
    return false;
  }
  const keys: string[] = [];
  url.searchParams.forEach((_value, key) => keys.push(key));
  keys.sort();
  return (
    keys.length === 2 &&
    keys[0] === "code" &&
    keys[1] === "state" &&
    hasSingleBoundedParameter(
      url.searchParams,
      "code",
      MAX_AUTHORIZATION_CODE_LENGTH,
    ) &&
    hasSingleBoundedParameter(url.searchParams, "state", MAX_STATE_LENGTH)
  );
}

function htmlEscape(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function redirectResponse(redirectTo: string): Response {
  const headers = securityHeaders(new Headers({ location: redirectTo }));
  headers.append("set-cookie", CSRF_COOKIE_CLEAR);
  return new Response(null, { headers, status: 302 });
}

function chatGptReturnPage(redirectTo: string, callback: URL): Response {
  const body = `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Return to ChatGPT</title>
  <style>
    :root{color-scheme:light dark;font-family:-apple-system,BlinkMacSystemFont,system-ui,"Helvetica Neue",sans-serif;background:#f5f5f7;color:#1d1d1f}
    *{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;padding:24px;background:#f5f5f7;-webkit-font-smoothing:antialiased}
    main{width:min(420px,100%);padding:28px;border-radius:14px;background:#fff}h1{font-size:1.4rem;font-weight:650;letter-spacing:-.02em;margin:0 0 7px}
    p{margin:0;color:#5f5f64;line-height:1.45}.actions{display:flex;justify-content:flex-end;margin-top:22px}a{display:inline-flex;min-height:38px;align-items:center;border-radius:8px;background:#0066cc;color:#fff;padding:8px 18px;text-decoration:none;font-weight:600}
    a:hover{background:#005eb8}a:focus-visible{outline:3px solid #005eb8;outline-offset:2px}
    @media(prefers-color-scheme:dark){:root,body{background:#1c1c1e;color:#f5f5f7}main{background:#2c2c2e}p{color:#c7c7cc}a{background:#0066cc}a:hover{background:#005eb8}a:focus-visible{outline-color:#64b5ff}}
    @media(max-width:520px){body{padding:12px}main{padding:22px 18px}a{min-height:44px}}
  </style>
</head>
<body>
  <main>
    <h1>Return to ChatGPT</h1>
    <p>Continue there to finish connecting Foldweave.</p>
    <div class="actions"><a href="${htmlEscape(redirectTo)}" rel="noreferrer">Return to ChatGPT</a></div>
  </main>
</body>
</html>`;
  const navigationTarget = `${callback.origin}${callback.pathname}`;
  const headers = securityHeaders(
    new Headers({ "content-type": "text/html; charset=utf-8" }),
  );
  headers.set(
    "content-security-policy",
    "default-src 'none'; base-uri 'none'; frame-ancestors 'none'; " +
      "form-action 'none'; script-src 'none'; object-src 'none'; " +
      "connect-src 'none'; img-src 'none'; font-src 'none'; media-src 'none'; " +
      "worker-src 'none'; style-src 'unsafe-inline'; " +
      `navigate-to ${navigationTarget}`,
  );
  headers.set("permissions-policy", "camera=(), geolocation=(), microphone=(), payment=(), usb=()");
  headers.set("x-robots-tag", "noindex, nofollow, noarchive");
  headers.append("set-cookie", CSRF_COOKIE_CLEAR);
  return new Response(body, { headers, status: 200 });
}

export function authorizationCompletionResponse(redirectTo: string): Response {
  if (redirectTo.length > MAX_CALLBACK_URL_LENGTH) {
    return redirectResponse(redirectTo);
  }
  try {
    const callback = new URL(redirectTo);
    if (isExactChatGptCallback(callback)) {
      return chatGptReturnPage(redirectTo, callback);
    }
  } catch {
    // The OAuth provider owns redirect validation. Preserve its ordinary response here.
  }
  return redirectResponse(redirectTo);
}
