import { OAuthProvider } from "@cloudflare/workers-oauth-provider";

import {
  ACCESS_TOKEN_TTL_SECONDS,
  CLIENT_REGISTRATION_TTL_SECONDS,
  REFRESH_TOKEN_TTL_SECONDS,
  SUPPORTED_SCOPES,
} from "./constants";
import { DeviceSession } from "./device-session";
import type { Env } from "./env";
import {
  defaultHandler,
  McpApiHandler,
  validateClientRegistration,
} from "./gateway";
import { PairingDirectory } from "./pairing-directory";
import { PUBLIC_MCP_PATH } from "./public-discovery";

export { DeviceSession, PairingDirectory };

const oauthProvider = new OAuthProvider<Env>({
  accessTokenTTL: ACCESS_TOKEN_TTL_SECONDS,
  allowImplicitFlow: false,
  allowPlainPKCE: false,
  allowTokenExchangeGrant: false,
  apiHandler: McpApiHandler,
  apiRoute: PUBLIC_MCP_PATH,
  authorizeEndpoint: "/authorize",
  clientIdMetadataDocumentEnabled: true,
  clientRegistrationCallback: validateClientRegistration,
  clientRegistrationEndpoint: "/oauth/register",
  clientRegistrationTTL: CLIENT_REGISTRATION_TTL_SECONDS,
  defaultHandler,
  disallowPublicClientRegistration: false,
  refreshTokenTTL: REFRESH_TOKEN_TTL_SECONDS,
  resourceMetadata: {
    bearer_methods_supported: ["header"],
    resource_name: "Foldweave",
    scopes_supported: [...SUPPORTED_SCOPES],
  },
  scopesSupported: [...SUPPORTED_SCOPES],
  tokenEndpoint: "/oauth/token",
});

export default {
  async fetch(
    request: Request,
    env: Env,
    ctx: ExecutionContext,
  ): Promise<Response> {
    const url = new URL(request.url);
    const hasBearerToken = request.headers
      .get("authorization")
      ?.startsWith("Bearer ");
    if (
      url.pathname === PUBLIC_MCP_PATH &&
      request.method === "POST" &&
      !hasBearerToken
    ) {
      return defaultHandler.fetch!(
        request as Parameters<NonNullable<typeof defaultHandler.fetch>>[0],
        env,
        ctx,
      );
    }
    return oauthProvider.fetch(
      request as Parameters<typeof oauthProvider.fetch>[0],
      env,
      ctx,
    );
  },
} satisfies ExportedHandler<Env>;
