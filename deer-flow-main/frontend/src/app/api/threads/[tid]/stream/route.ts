/**
 * SSE streaming proxy for ``GET /api/threads/{tid}/stream``.
 *
 * Mirror of the POST /messages proxy in
 * ``../messages/route.ts`` — same buffering-bypass dance, but for the
 * reconnect endpoint that re-attaches to an *existing* runner without
 * starting a new turn. See that file for the full rationale on
 * ``Content-Encoding: identity`` + the manual ReadableStream pump.
 *
 * The frontend hits this endpoint when the original POST stream drops
 * mid-run (network blip, idle proxy timeout, page navigation), passing
 * its last seen SSE id via ``Last-Event-ID`` for cursor-based resume.
 */

import type { NextRequest } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const fetchCache = "force-no-store";

function gatewayBase(): string {
  const url = process.env.DEER_FLOW_INTERNAL_GATEWAY_BASE_URL?.trim();
  return url && url.length > 0 ? url.replace(/\/+$/, "") : "http://127.0.0.1:8000";
}

export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ tid: string }> },
) {
  const { tid } = await params;

  const headers = new Headers(req.headers);
  headers.delete("host");
  headers.delete("content-length");
  headers.delete("connection");

  const upstream = await fetch(`${gatewayBase()}/api/threads/${tid}/stream`, {
    method: "GET",
    headers,
    // @ts-expect-error — Next's fetch supports this Node-only option.
    duplex: "half",
    cache: "no-store",
    redirect: "manual",
  });

  // 404 (no_active_run) and other non-2xx pass through verbatim — the
  // frontend reads them to decide whether to give up or retry. We only
  // need the streaming-bypass dance on the 200 path.
  if (upstream.status !== 200 || !upstream.body) {
    const text = await upstream.text().catch(() => "");
    return new Response(text, {
      status: upstream.status,
      headers: { "Content-Type": upstream.headers.get("content-type") ?? "text/plain" },
    });
  }

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

  const respHeaders = new Headers();
  respHeaders.set(
    "Content-Type",
    upstream.headers.get("content-type") ?? "text/event-stream",
  );
  respHeaders.set("Cache-Control", "no-cache, no-transform");
  respHeaders.set("Connection", "keep-alive");
  respHeaders.set("X-Accel-Buffering", "no");
  respHeaders.set("Content-Encoding", "identity");

  return new Response(out, {
    status: upstream.status,
    headers: respHeaders,
  });
}
