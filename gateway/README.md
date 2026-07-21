# Foldweave public gateway

This directory contains the deployable Cloudflare Worker transport for the
ChatGPT-hosted Foldweave path. It does not contain the deterministic Foldweave
engine and does not call the OpenAI Responses API. ChatGPT supplies model
inference; the paired local companion supplies bounded deterministic tools.

The gateway is intentionally a transport and authorization boundary:

- `@cloudflare/workers-oauth-provider` implements OAuth authorization-code
  grants, one-hour access tokens, rotating 30-day refresh grants, Client ID
  Metadata Documents (CIMD), and Dynamic Client Registration (DCR) fallback.
- PKCE is restricted to `S256`; the implicit grant, plain PKCE, and RFC 8693
  token-exchange grant are disabled.
- `OAUTH_KV` is reserved for the OAuth provider's hashed authorization data.
- `PairingDirectory` retains only expiring pairing-code hashes, attempt counts,
  hashed source buckets, and opaque session identifiers.
- One `DeviceSession` Durable Object retains one device's public key, scopes,
  expiry/revocation state, monotonic sequence, and bounded digest-only replay
  correlation. The local `FolderRefactorJobV3` remains the sole product job and
  idempotency authority.
- The companion opens an outbound WebSocket. The Durable Object is the
  Hibernation-capable WebSocket server; no public inbound listener is opened on
  the user's Mac.
- MCP request and response bodies exist only in active Worker/Durable Object
  memory while being relayed. They are not written to KV or Durable Object
  storage.

The current public deployment is:

- origin: <https://foldweave-gateway.skybert-ghostline.workers.dev>;
- deployment: `d14d051d-8920-44ea-b336-f3bbea2f6936`;
- Worker version: `9ac88da8-9f85-4685-8a07-073d44b909b9`;
- widget asset cache: `review-v35`;
- JavaScript SHA-256:
  `3ac8e6c83350e1d88145d50470a90cb3b2763386aee816986139e611f3ac4bea`;
- CSS SHA-256:
  `666df057a85df92cfdd57228ef9fc1a8ece31cd65807720695d14dbd867ca173`;
  and
- local qualification: 50/50 gateway tests, strict TypeScript, and the Wrangler
  production dry-run bundle.

The earlier `ERR_BLOCKED_BY_CLIENT` occurred in the Codex in-app Browser policy
layer. The user-authorized Google Chrome route completed ChatGPT connector
OAuth, device pairing, outbound companion WSS, opaque local selection, consumer
origin and receiver-derivative transactions, reconnect, duplicate/refusal
checks, verification, and reconstruction. `CONSUMER_PAIRING_VERIFIED` and
narrow technical `PUBLICATION_READY` for review submission are achieved.
Foldweave has not been submitted for ChatGPT review, approved, published, or
publicly listed.

## Public endpoints

| Endpoint | Purpose |
|---|---|
| `GET /healthz` | Non-sensitive binding/readiness check |
| `GET/POST /authorize` | Pairing-code OAuth consent surface |
| `POST /oauth/token` | Authorization-code and refresh-token exchange managed by the provider |
| `POST /oauth/register` | DCR fallback managed by the provider |
| `/.well-known/oauth-authorization-server` | Authorization-server metadata |
| `/.well-known/oauth-protected-resource` | MCP protected-resource metadata |
| `POST /pairing/register` | Self-signed public-device registration and one-time-code creation |
| `POST /pairing/approve?session=...` | Device-signed local confirmation |
| `POST /pairing/status?session=...` | Device-signed authoritative OAuth, connection, expiry, and revocation status |
| `POST /pairing/revoke?session=...` | Device-signed revocation and OAuth-grant revocation |
| `GET /companion?session=...` | Outbound companion WebSocket upgrade |
| `/mcp` | OAuth-protected Streamable HTTP MCP relay |

No endpoint accepts an absolute local path, provider credential, shell command,
or project payload as gateway configuration. Local selections are represented
by short-lived opaque handles created by the companion.

## Pairing and relay invariants

1. The companion creates an Ed25519 key in macOS Keychain and self-signs a
   public registration envelope.
2. The gateway returns one ten-character Crockford Base32 code. Only its
   SHA-256 digest is persisted; it expires after ten minutes.
3. The local companion signs an explicit approval before the code can be used.
4. A code is consumed atomically, locks after five failed submissions, and is
   also protected by a twenty-attempt-per-source-bucket, fifteen-minute limit.
5. OAuth props bind the exact opaque device and session. Revocation makes an
   otherwise unexpired token unusable and revokes provider grants.
6. Every companion message binds a request ID, millisecond issue/expiry times,
   nonce, monotonic sequence, canonical body digest, and Ed25519 signature.
7. Identical concurrent relay retries share one pending response. Conflicting
   reuse of the same request ID blocks. Completed identical retries may be
   forwarded again, but the local job/idempotency authority returns the same
   durable job or result without another provider call or output.
8. Pairing-code expiry and authorized-device expiry are distinct. Successful
   OAuth authorization transactionally extends the device session to the
   30-day grant boundary; the per-job capability boundary remains 30 minutes.

## Local verification

Requires Node.js 22 or newer.

Run:

    npm ci --ignore-scripts
    npm run check

`npm run check` performs strict TypeScript checking, Worker-runtime Vitest
tests, and a Wrangler dry-run bundle. The current suite passes 50/50 tests and
includes:

- current OAuth metadata and unauthenticated MCP challenge behavior;
- code expiry/consumption, five-failure lockout, and source-bucket limiting;
- self-signed registration, local approval, and replay rejection;
- a fixed envelope signed by the Python companion implementation;
- ten-minute pairing versus 30-day authorized-session fake-time behavior; and
- identical concurrent retry coalescing versus conflicting reuse.

## Deployment and qualification boundary

No Cloudflare account mutation or deployment is performed by this package's
tests or dry-run build. `wrangler.jsonc` contains the provisioned production and
preview `OAUTH_KV` namespace bindings plus the SQLite Durable Object bindings;
those binding identifiers are configuration, not bearer credentials. A real
`wrangler deploy` is a separate external mutation and must use the authorized
Cloudflare account.

The public Worker above is deployed and responds on its stable `workers.dev`
hostname. End-to-end Chrome qualification covered ChatGPT connector OAuth,
pairing, the paired outbound companion, origin and receiver-derivative
transactions, disconnect/reconnect, duplicate handling, refusal paths,
verification, and reconstruction. This establishes `CONSUMER_PAIRING_VERIFIED`
and narrow technical `PUBLICATION_READY`; it does not establish review
submission, approval, publication, or public listing.

The ChatGPT widget's standard `ui/message` revision request was acknowledged
and displayed but did not automatically trigger the host's revision tool call.
One explicit same-conversation continuation was required and verified for each
consumer revision. This limitation remains part of the qualified transport
profile rather than being relabelled as automatic component-authored
continuation.
