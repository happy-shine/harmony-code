/**
 * SSE streaming proxy for ``POST /api/threads/{tid}/messages``.
 *
 * Next.js's built-in ``rewrites()`` buffers chunked responses through the
 * dev server, collapsing the stream into a single burst at ``end``. That
 * defeats token-by-token streaming for the chat UI. An explicit App
 * Router route handler gives us control: we forward the request to the
 * gateway with Node's streaming fetch and return ``response.body``
 * verbatim, preserving the SSE frame cadence.
 *
 * This route is co-located with the Next config's catch-all rewrite but
 * takes precedence (route handlers run ``afterFiles``-before-rewrites by
 * default for same-origin paths). Every other ``/api/*`` path still goes
 * through the rewrite; only the SSE endpoint needs this bypass.
 */

import { NextRequest } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const fetchCache = "force-no-store";

function gatewayBase(): string {
  // Mirrors next.config.js: server-side env, no rewrite-loop concern
  // because we're forwarding with an explicit fetch.
  const url = process.env.DEER_FLOW_INTERNAL_GATEWAY_BASE_URL?.trim();
  return url && url.length > 0 ? url.replace(/\/+$/, "") : "http://127.0.0.1:8000";
}

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ tid: string }> },
) {
  const { tid } = await params;
  const body = await req.arrayBuffer();

  // Forward Cookie so the gateway sees the caller's ``harmony_session``.
  // Strip hop-by-hop headers; copy the rest verbatim so Accept, Content-Type,
  // and any future app headers pass through.
  const headers = new Headers(req.headers);
  headers.delete("host");
  headers.delete("content-length");
  headers.delete("connection");

  const upstream = await fetch(`${gatewayBase()}/api/threads/${tid}/messages`, {
    method: "POST",
    headers,
    body,
    // Undici respects this hint to not buffer the response body.
    // @ts-expect-error — Next's fetch supports this Node-only option.
    duplex: "half",
    cache: "no-store",
    redirect: "manual",
  });

  if (!upstream.body) {
    return new Response("upstream has no body", { status: 502 });
  }

  // Re-emit bytes through an explicit ReadableStream so the Next dev
  // server can't coalesce multiple upstream chunks into one flush. Each
  // read from the upstream reader is enqueued into the downstream
  // controller immediately; the browser sees the same cadence the
  // backend used. Returning ``upstream.body`` verbatim works in
  // production but Turbopack's dev proxy buffers it into one chunk.
  const reader = upstream.body.getReader();
  const out = new ReadableStream<Uint8Array>({
    async pull(controller) {
      const { done, value } = await reader.read();
      if (done) {
        controller.close();
        return;
      }
      controller.enqueue(value);
    },
    cancel(reason) {
      void reader.cancel(reason);
    },
  });

  // Explicitly set SSE-friendly headers so no intermediary decides to
  // buffer — ``X-Accel-Buffering: no`` is the nginx-ism;
  // ``Cache-Control: no-transform`` keeps CDNs from re-encoding the
  // stream; ``Content-Type: text/event-stream`` is the authoritative type.
  const respHeaders = new Headers();
  respHeaders.set("Content-Type", upstream.headers.get("content-type") ?? "text/event-stream");
  respHeaders.set("Cache-Control", "no-cache, no-transform");
  respHeaders.set("Connection", "keep-alive");
  respHeaders.set("X-Accel-Buffering", "no");

  return new Response(out, {
    status: upstream.status,
    headers: respHeaders,
  });
}
