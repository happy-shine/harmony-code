// Copyright (c) 2025 Bytedance Ltd. and/or its affiliates
// SPDX-License-Identifier: Apache-2.0

import { drainSSE } from "@/core/cc-events/parse";
import type { StreamEvent } from "@/core/cc-events/types";

export type StreamYield =
  | { type: "event"; event: StreamEvent }
  | { type: "_done" }
  | { type: "_error"; payload: Record<string, unknown> };

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

  for await (const frame of drainSSE(resp.body)) {
    if (frame.kind === "data") yield { type: "event", event: frame.event };
    else if (frame.kind === "done") yield { type: "_done" };
    else if (frame.kind === "error")
      yield { type: "_error", payload: frame.payload };
    // "invalid" frames: log and skip
    else if (frame.kind === "invalid") {
      console.warn("[cc-stream] invalid SSE frame:", frame.reason);
    }
  }
}
