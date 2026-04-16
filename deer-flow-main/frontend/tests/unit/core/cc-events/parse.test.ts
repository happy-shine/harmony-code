// Copyright (c) 2025 Bytedance Ltd. and/or its affiliates
// SPDX-License-Identifier: Apache-2.0

import { describe, expect, it } from "vitest";

import { parseSSEFrame } from "@/core/cc-events/parse";

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
