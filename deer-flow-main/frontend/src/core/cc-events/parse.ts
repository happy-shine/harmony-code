// Copyright (c) 2025 Bytedance Ltd. and/or its affiliates
// SPDX-License-Identifier: Apache-2.0

import type { StreamEvent } from "./types";

// ---------------------------------------------------------------------------
// Parsed frame variants
// ---------------------------------------------------------------------------

export type ParsedFrame =
  | { kind: "data"; event: StreamEvent; id?: string }
  | { kind: "done" }
  | { kind: "error"; payload: Record<string, unknown> }
  | { kind: "invalid"; reason: string };

// ---------------------------------------------------------------------------
// parseSSEFrame — turn a single SSE frame (text between blank lines) into a
// typed ParsedFrame.
// ---------------------------------------------------------------------------

export function parseSSEFrame(frame: string): ParsedFrame {
  // Note: Per SSE spec, "data:hello" (no space) is valid. This parser requires
  // "data: " (with space), matching our gateway's output format (sse-starlette).
  // Lines may be terminated by "\n" or "\r\n" — strip trailing "\r" per-line.
  const lines = frame.split("\n").map((l) => (l.endsWith("\r") ? l.slice(0, -1) : l));
  let eventName = "";
  let id: string | undefined;
  const dataLines: string[] = [];

  for (const line of lines) {
    if (line.startsWith("event: ")) {
      eventName = line.slice(7).trim();
    } else if (line.startsWith("data: ")) {
      dataLines.push(line.slice(6));
    } else if (line.startsWith("id: ")) {
      // Backend emits ``<run_id>:<event_id>``. We pass it through opaque
      // so callers can echo it as ``Last-Event-ID`` on reconnect — only
      // the backend interprets the structure.
      id = line.slice(4).trim();
    }
  }

  const dataStr = dataLines.join("\n");

  if (eventName === "done") {
    return { kind: "done" };
  }

  if (eventName === "error") {
    try {
      return {
        kind: "error",
        payload: JSON.parse(dataStr) as Record<string, unknown>,
      };
    } catch {
      return { kind: "invalid", reason: "error payload not json" };
    }
  }

  try {
    return { kind: "data", event: JSON.parse(dataStr) as StreamEvent, id };
  } catch {
    return { kind: "invalid", reason: "data not json" };
  }
}

// ---------------------------------------------------------------------------
// drainSSE — async generator that reads a ReadableStream<Uint8Array> and
// yields ParsedFrame values as complete SSE frames arrive.
// ---------------------------------------------------------------------------

export async function* drainSSE(
  body: ReadableStream<Uint8Array>,
): AsyncGenerator<ParsedFrame> {
  const reader = body.getReader();
  const dec = new TextDecoder();
  let buf = "";

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) return;
      buf += dec.decode(value, { stream: true });

      // Normalize CRLF → LF so frame boundary detection works regardless of
      // the server's line endings. sse-starlette emits "\r\n\r\n" between
      // frames; splitting on "\n\n" alone would miss those boundaries.
      buf = buf.replace(/\r\n/g, "\n");

      const frames = buf.split("\n\n");
      buf = frames.pop() ?? "";

      for (const f of frames) {
        if (f.trim()) yield parseSSEFrame(f);
      }
    }
  } finally {
    reader.releaseLock();
  }
}
