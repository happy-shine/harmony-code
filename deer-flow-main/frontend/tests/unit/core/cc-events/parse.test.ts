// Copyright (c) 2025 Bytedance Ltd. and/or its affiliates
// SPDX-License-Identifier: Apache-2.0

import { describe, expect, it } from "vitest";

import { drainSSE, parseSSEFrame, type ParsedFrame } from "@/core/cc-events/parse";

describe("parseSSEFrame", () => {
  it("parses a data-only frame", () => {
    const frame = 'data: {"type":"assistant","message":{"id":"m1"}}';
    const r = parseSSEFrame(frame);
    expect(r.kind).toBe("data");
    if (r.kind === "data") expect(r.event.type).toBe("assistant");
  });

  it("parses a done event", () => {
    const frame = "event: done\ndata: {}";
    const r = parseSSEFrame(frame);
    expect(r.kind).toBe("done");
  });

  it("parses an error event", () => {
    const frame = 'event: error\ndata: {"code":"boom"}';
    const r = parseSSEFrame(frame);
    expect(r.kind).toBe("error");
    if (r.kind === "error") expect(r.payload.code).toBe("boom");
  });

  it("handles malformed json by returning invalid", () => {
    const frame = "data: not-json";
    const r = parseSSEFrame(frame);
    expect(r.kind).toBe("invalid");
  });

  it("parses multi-line data", () => {
    const frame =
      'data: {"type":"system",\ndata: "subtype":"init","session_id":"s1"}';
    const r = parseSSEFrame(frame);
    expect(r.kind).toBe("data");
  });
});

describe("drainSSE", () => {
  it("yields frames separated by CRLF (sse-starlette's default)", async () => {
    // sse-starlette emits "\r\n\r\n" between frames. The parser must treat
    // CRLF-separated frames identically to LF-separated frames.
    const body = new TextEncoder().encode(
      'data: {"type":"system","subtype":"init","session_id":"s1"}\r\n\r\n' +
        'data: {"type":"assistant","message":{"id":"m1"}}\r\n\r\n' +
        "event: done\r\ndata: {}\r\n\r\n",
    );

    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(body);
        controller.close();
      },
    });

    const frames: ParsedFrame[] = [];
    for await (const f of drainSSE(stream)) {
      frames.push(f);
    }

    expect(frames.map((f) => f.kind)).toEqual(["data", "data", "done"]);
    if (frames[0]?.kind === "data") expect(frames[0].event.type).toBe("system");
    if (frames[1]?.kind === "data")
      expect(frames[1].event.type).toBe("assistant");
  });

  it("yields frames from a ReadableStream, handling chunk boundaries", async () => {
    // Two chunks that split mid-frame
    const chunk1 = new TextEncoder().encode(
      'data: {"type":"system","subtype":"init","session_id":"s1"}\n\ndata: {"typ',
    );
    const chunk2 = new TextEncoder().encode(
      'e":"assistant","message":{"id":"m1"}}\n\n',
    );

    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(chunk1);
        controller.enqueue(chunk2);
        controller.close();
      },
    });

    const frames: ParsedFrame[] = [];
    for await (const f of drainSSE(stream)) {
      frames.push(f);
    }

    expect(frames).toHaveLength(2);
    expect(frames[0]?.kind).toBe("data");
    expect(frames[1]?.kind).toBe("data");
    if (frames[0]?.kind === "data") expect(frames[0].event.type).toBe("system");
    if (frames[1]?.kind === "data")
      expect(frames[1].event.type).toBe("assistant");
  });
});
