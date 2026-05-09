// Copyright (c) 2025 Bytedance Ltd. and/or its affiliates
// SPDX-License-Identifier: Apache-2.0

import { drainSSE } from "@/core/cc-events/parse";
import type { StreamEvent } from "@/core/cc-events/types";

export type StreamYield =
  | { type: "event"; event: StreamEvent; id?: string }
  | { type: "_done" }
  | { type: "_error"; payload: Record<string, unknown> };

/** Start a new run on a thread by POSTing the user message. The returned
 *  generator yields events with their backend-assigned SSE ids so the
 *  caller can record a resume cursor for ``openResumeStream``. */
export async function* openMessageStream(
  threadId: string,
  payload: { content: string; attachments?: string[] },
  signal: AbortSignal,
): AsyncGenerator<StreamYield> {
  const resp = await fetch(`/api/threads/${threadId}/messages`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      // Explicit SSE Accept lets any intermediary route the response
      // verbatim (no buffering / content-negotiation surprises).
      Accept: "text/event-stream",
    },
    cache: "no-store",
    credentials: "include",
    body: JSON.stringify(payload),
    signal,
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  if (!resp.body) throw new Error("no body");

  yield* drainStream(resp.body);
}

/** Re-attach to a thread's *currently active* run without sending a new
 *  message — the reconnect path. ``lastEventId`` is the opaque id last
 *  received from a previous stream (``<run_id>:<event_id>``); the
 *  backend resumes from the next event when the run_id matches, or
 *  serves the full buffered history when it doesn't (or when omitted).
 *
 *  Returns ``"no_active_run"`` if the server has nothing to subscribe
 *  to (no run in flight and nothing in the registry's retention window).
 *  Throws on transport errors so the caller can decide whether to retry. */
export async function* openResumeStream(
  threadId: string,
  signal: AbortSignal,
  lastEventId?: string,
): AsyncGenerator<StreamYield | { type: "_no_active_run" }> {
  const headers: Record<string, string> = {
    Accept: "text/event-stream",
  };
  if (lastEventId) headers["Last-Event-ID"] = lastEventId;
  const resp = await fetch(`/api/threads/${threadId}/stream`, {
    method: "GET",
    headers,
    cache: "no-store",
    credentials: "include",
    signal,
  });
  if (resp.status === 404) {
    yield { type: "_no_active_run" };
    return;
  }
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  if (!resp.body) throw new Error("no body");
  yield* drainStream(resp.body);
}

/** Cancel the currently active run on a thread. Best-effort: swallows
 *  network errors so a "stop" button click never hard-fails the UI —
 *  the caller will observe the cancellation via the SSE terminal frame
 *  emitted by the backend's runner cleanup path. */
export async function cancelRun(threadId: string): Promise<void> {
  try {
    await fetch(`/api/threads/${threadId}/cancel`, {
      method: "POST",
      credentials: "include",
      cache: "no-store",
    });
  } catch {
    // Swallow — the only consequence is the runner keeps going. The
    // user can press stop again, or the run finishes on its own.
  }
}

async function* drainStream(
  body: ReadableStream<Uint8Array>,
): AsyncGenerator<StreamYield> {
  for await (const frame of drainSSE(body)) {
    if (frame.kind === "data")
      yield { type: "event", event: frame.event, id: frame.id };
    else if (frame.kind === "done") yield { type: "_done" };
    else if (frame.kind === "error")
      yield { type: "_error", payload: frame.payload };
    // "invalid" frames: log and skip
    else if (frame.kind === "invalid") {
      console.warn("[cc-stream] invalid SSE frame:", frame.reason);
    }
  }
}
