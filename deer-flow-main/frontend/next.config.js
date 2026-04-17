/**
 * Run `build` or `dev` with `SKIP_ENV_VALIDATION` to skip env validation. This is especially useful
 * for Docker builds.
 */
import "./src/env.js";

function getInternalServiceURL(envKey, fallbackURL) {
  const configured = process.env[envKey]?.trim();
  return configured && configured.length > 0
    ? configured.replace(/\/+$/, "")
    : fallbackURL;
}
import nextra from "nextra";

const withNextra = nextra({});

/** @type {import("next").NextConfig} */
const config = {
  i18n: {
    locales: ["en", "zh"],
    defaultLocale: "en",
  },
  devIndicators: false,
  // Disable built-in gzip so ``/api/threads/{tid}/messages`` SSE streams
  // arrive with real per-token cadence instead of being buffered inside
  // the compressor. gzip has to accumulate bytes before producing an
  // output frame, so a series of 30-byte ``content_block_delta`` events
  // emitted 400ms apart would otherwise land at the client in one burst
  // at stream close — the user sees a dead pause then the whole reply
  // pops in. Static assets ship from the Turbopack dev server with
  // their own transfer encoding, so turning this off has no measurable
  // impact on page-load perf.
  compress: false,
  async rewrites() {
    // Single catch-all: forward every /api/* request to the harmony-code
    // gateway. The old per-endpoint rewrites (langgraph / agents / threads)
    // are dead — LangGraph is gone (M5), and a catch-all covers the M3+
    // routers (auth, skills, mcp, workspace, uploads, threads, ...).
    //
    // Any Next.js local routes under src/app/api/** take precedence over
    // rewrites by default (afterFiles), so if we ever want to intercept a
    // path in-process, drop a route.ts there. For the MVP, everything
    // /api/* goes straight to the Python backend.
    const gatewayURL = getInternalServiceURL(
      "DEER_FLOW_INTERNAL_GATEWAY_BASE_URL",
      "http://127.0.0.1:8000",
    );

    // When NEXT_PUBLIC_BACKEND_BASE_URL is set, the client hits the backend
    // directly (no same-origin rewrite needed), so skip registering the
    // rewrite to avoid a redundant hop.
    if (process.env.NEXT_PUBLIC_BACKEND_BASE_URL) {
      return [];
    }

    return [
      {
        source: "/api/:path*",
        destination: `${gatewayURL}/api/:path*`,
      },
    ];
  },
};

export default withNextra(config);
