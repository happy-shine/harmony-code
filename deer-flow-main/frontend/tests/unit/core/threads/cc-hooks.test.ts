// Copyright (c) 2025 Bytedance Ltd. and/or its affiliates
// SPDX-License-Identifier: Apache-2.0

import { afterEach, describe, expect, it, vi } from "vitest";

// Test the stream client directly (openMessageStream) since testing
// React hooks requires renderHook from @testing-library/react which may
// not be installed.
import { openMessageStream } from "@/core/threads/cc-stream";
import type { StreamYield } from "@/core/threads/cc-stream";

function makeSSEStream(frames: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  const body = frames.join("\n\n") + "\n\n";
  return new ReadableStream({
    start(controller) {
      controller.enqueue(encoder.encode(body));
      controller.close();
    },
  });
}

describe("openMessageStream", () => {
  const originalFetch = globalThis.fetch;

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it("yields events from SSE stream", async () => {
    const sseFrames = [
      'data: {"type":"system","subtype":"init","session_id":"s1"}',
      'data: {"type":"assistant","message":{"id":"m1","content":[{"type":"text","text":"hi"}]}}',
      "event: done\ndata: {}",
    ];
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      body: makeSSEStream(sseFrames),
    });

    const results: StreamYield[] = [];
    const controller = new AbortController();
    for await (const ev of openMessageStream(
      "t1",
      { content: "test" },
      controller.signal,
    )) {
      results.push(ev);
    }

    expect(results).toHaveLength(3);
    expect(results[0]?.type).toBe("event");
    expect(results[1]?.type).toBe("event");
    expect(results[2]?.type).toBe("_done");
  });

  it("throws on non-ok response", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({ ok: false, status: 500 });

    const controller = new AbortController();
    await expect(async () => {
      for await (const _ of openMessageStream(
        "t1",
        { content: "test" },
        controller.signal,
      )) {
        // should not reach
      }
    }).rejects.toThrow("HTTP 500");
  });
});
