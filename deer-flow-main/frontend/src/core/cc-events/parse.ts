// Copyright (c) 2025 Bytedance Ltd. and/or its affiliates
// SPDX-License-Identifier: Apache-2.0

import type { StreamEvent } from "./types";

// ---------------------------------------------------------------------------
// Parsed frame variants
// ---------------------------------------------------------------------------

export type ParsedFrame =
  | { kind: "data"; event: StreamEvent }
  | { kind: "done" }
  | { kind: "error"; payload: Record<string, unknown> }
  | { kind: "invalid"; reason: string };

// ---------------------------------------------------------------------------
// parseSSEFrame — turn a single SSE frame (text between blank lines) into a
// typed ParsedFrame.
// ---------------------------------------------------------------------------

export function parseSSEFrame(frame: string): ParsedFrame {
  const lines = frame.split("\n");
  let eventName = "";
  const dataLines: string[] = [];

  for (const line of lines) {
    if (line.startsWith("event: ")) {
      eventName = line.slice(7).trim();
    } else if (line.startsWith("data: ")) {
      dataLines.push(line.slice(6));
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
    return { kind: "data", event: JSON.parse(dataStr) as StreamEvent };
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
