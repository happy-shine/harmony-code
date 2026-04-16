// Copyright (c) 2025 Bytedance Ltd. and/or its affiliates
// SPDX-License-Identifier: Apache-2.0

import { describe, expect, it } from "vitest";

import type { StreamEvent } from "@/core/cc-events/types";
import {
  initialMessageState,
  messageReducer,
  type MessageState,
} from "@/core/messages/cc-reducer";

/** Helper: feed a sequence of events through the reducer. */
function ingestAll(events: StreamEvent[]): MessageState {
  let state = initialMessageState();
  for (const event of events) {
    state = messageReducer(state, { type: "ingest", event });
  }
  return state;
}

describe("messageReducer", () => {
  // -----------------------------------------------------------------------
  // 1. system.init → appends system_init UIMessage
  // -----------------------------------------------------------------------
  it("system.init creates a system_init UIMessage", () => {
    const evt: StreamEvent = {
      type: "system",
      subtype: "init",
      session_id: "sess-1",
      model: "claude-sonnet-4-20250514",
      cwd: "/tmp",
      tools: ["Read", "Write"],
      mcp_servers: [{ name: "fs", status: "connected" }],
    };

    const state = ingestAll([evt]);
    expect(state.messages).toHaveLength(1);

    const msg = state.messages[0]!;
    expect(msg.kind).toBe("system_init");
    if (msg.kind === "system_init") {
      expect(msg.sessionId).toBe("sess-1");
      expect(msg.model).toBe("claude-sonnet-4-20250514");
      expect(msg.cwd).toBe("/tmp");
      expect(msg.tools).toEqual(["Read", "Write"]);
      expect(msg.mcpServers).toEqual([{ name: "fs", status: "connected" }]);
    }
  });

  // -----------------------------------------------------------------------
  // 2. Multiple assistant frames with same message.id merge into one
  // -----------------------------------------------------------------------
  it("merges assistant frames with the same message.id", () => {
    const frame1: StreamEvent = {
      type: "assistant",
      message: {
        id: "msg-1",
        role: "assistant",
        content: [{ type: "text", text: "Hel" }],
      },
    };
    const frame2: StreamEvent = {
      type: "assistant",
      message: {
        id: "msg-1",
        role: "assistant",
        content: [{ type: "text", text: "Hello, world!" }],
        stop_reason: "end_turn",
      },
    };

    const state = ingestAll([frame1, frame2]);
    expect(state.messages).toHaveLength(1);

    const msg = state.messages[0]!;
    expect(msg.kind).toBe("assistant");
    if (msg.kind === "assistant") {
      expect(msg.blocks).toHaveLength(1);
      // The second frame's text replaces the first (full content, not delta)
      expect(msg.blocks[0]!.kind).toBe("text");
      if (msg.blocks[0]!.kind === "text") {
        expect(msg.blocks[0]!.text).toBe("Hello, world!");
        expect(msg.blocks[0]!.streaming).toBe(false);
      }
      expect(msg.stopReason).toBe("end_turn");
    }
  });

  // -----------------------------------------------------------------------
  // 3. user event with tool_result backfills onto matching tool_use block
  // -----------------------------------------------------------------------
  it("backfills tool_result onto matching tool_use block", () => {
    const assistantEvt: StreamEvent = {
      type: "assistant",
      message: {
        id: "msg-2",
        role: "assistant",
        content: [
          { type: "tool_use", id: "tu-1", name: "Read", input: { path: "/x" } },
        ],
      },
    };
    const userEvt: StreamEvent = {
      type: "user",
      message: {
        content: [
          {
            type: "tool_result",
            tool_use_id: "tu-1",
            content: "file contents here",
          },
        ],
      },
    };

    const state = ingestAll([assistantEvt, userEvt]);
    expect(state.messages).toHaveLength(1); // user tool_result events don't add a new message

    const msg = state.messages[0]!;
    expect(msg.kind).toBe("assistant");
    if (msg.kind === "assistant") {
      const block = msg.blocks[0]!;
      expect(block.kind).toBe("tool_use");
      if (block.kind === "tool_use") {
        expect(block.status).toBe("ok");
        expect(block.result).toBe("file contents here");
      }
    }
  });

  // -----------------------------------------------------------------------
  // 4. TodoWrite tool_use diverts to state.todos, NOT to assistant blocks
  // -----------------------------------------------------------------------
  it("diverts TodoWrite tool_use to state.todos", () => {
    const evt: StreamEvent = {
      type: "assistant",
      message: {
        id: "msg-3",
        role: "assistant",
        content: [
          { type: "text", text: "I'll track that." },
          {
            type: "tool_use",
            id: "tu-2",
            name: "TodoWrite",
            input: {
              todos: [
                { content: "Fix the bug", status: "pending" },
                { content: "Write tests", status: "in_progress" },
              ],
            },
          },
        ],
      },
    };

    const state = ingestAll([evt]);

    // TodoWrite block should NOT appear in assistant blocks
    const msg = state.messages[0]!;
    expect(msg.kind).toBe("assistant");
    if (msg.kind === "assistant") {
      const toolBlocks = msg.blocks.filter((b) => b.kind === "tool_use");
      expect(toolBlocks).toHaveLength(0);
      // Text block should still be present
      expect(msg.blocks).toHaveLength(1);
      expect(msg.blocks[0]!.kind).toBe("text");
    }

    // Todos should be captured
    expect(state.todos).toHaveLength(2);
    expect(state.todos[0]).toEqual({ content: "Fix the bug", status: "pending" });
    expect(state.todos[1]).toEqual({
      content: "Write tests",
      status: "in_progress",
    });
  });

  // -----------------------------------------------------------------------
  // 5. result event captured as state.result
  // -----------------------------------------------------------------------
  it("captures result event as state.result", () => {
    const evt: StreamEvent = {
      type: "result",
      subtype: "success",
      duration_ms: 12345,
      total_cost_usd: 0.03,
      usage: {
        input_tokens: 1000,
        output_tokens: 500,
      },
    };

    const state = ingestAll([evt]);
    expect(state.result).not.toBeNull();
    expect(state.result!.duration_ms).toBe(12345);
    expect(state.result!.total_cost_usd).toBe(0.03);
    expect(state.result!.usage).toEqual({
      input_tokens: 1000,
      output_tokens: 500,
    });
  });

  // -----------------------------------------------------------------------
  // Extra: reset action returns initial state
  // -----------------------------------------------------------------------
  it("reset action returns initial state", () => {
    const evt: StreamEvent = {
      type: "result",
      subtype: "success",
      duration_ms: 100,
    };
    let state = ingestAll([evt]);
    expect(state.result).not.toBeNull();

    state = messageReducer(state, { type: "reset" });
    expect(state.messages).toHaveLength(0);
    expect(state.todos).toHaveLength(0);
    expect(state.result).toBeNull();
  });

  // -----------------------------------------------------------------------
  // Extra: tool_result with is_error marks tool_use as error
  // -----------------------------------------------------------------------
  it("marks tool_use as error when tool_result has is_error", () => {
    const assistantEvt: StreamEvent = {
      type: "assistant",
      message: {
        id: "msg-4",
        role: "assistant",
        content: [
          { type: "tool_use", id: "tu-3", name: "Bash", input: { command: "fail" } },
        ],
      },
    };
    const userEvt: StreamEvent = {
      type: "user",
      message: {
        content: [
          {
            type: "tool_result",
            tool_use_id: "tu-3",
            content: "command not found",
            is_error: true,
          },
        ],
      },
    };

    const state = ingestAll([assistantEvt, userEvt]);
    const msg = state.messages[0]!;
    if (msg.kind === "assistant") {
      const block = msg.blocks[0]!;
      if (block.kind === "tool_use") {
        expect(block.status).toBe("error");
        expect(block.result).toBe("command not found");
      }
    }
  });

  // -----------------------------------------------------------------------
  // Extra: preserves backfilled tool_result when assistant re-sends same id
  // -----------------------------------------------------------------------
  it("preserves backfilled tool_result when assistant re-sends same message.id", () => {
    let s = initialMessageState();
    // 1. assistant with tool_use
    s = messageReducer(s, { type: "ingest", event: {
      type: "assistant",
      message: { id: "m1", content: [{ type: "tool_use", id: "tu1", name: "Read", input: { path: "x" } }] }
    } as any });
    // 2. user with tool_result
    s = messageReducer(s, { type: "ingest", event: {
      type: "user",
      message: { content: [{ type: "tool_result", tool_use_id: "tu1", content: "file content", is_error: false }] }
    } as any });
    // 3. assistant re-sends same message.id with tool_use + new text
    s = messageReducer(s, { type: "ingest", event: {
      type: "assistant",
      message: { id: "m1", content: [
        { type: "tool_use", id: "tu1", name: "Read", input: { path: "x" } },
        { type: "text", text: "Here is the file" }
      ] }
    } as any });

    const am = s.messages.find(m => m.kind === "assistant");
    expect(am).toBeDefined();
    if (am?.kind === "assistant") {
      const tu = am.blocks.find(b => b.kind === "tool_use");
      // tool_result backfill must survive the re-send
      if (tu?.kind === "tool_use") {
        expect(tu.status).toBe("ok");
        expect(tu.result).toBe("file content");
      }
      // new text block must also appear
      const txt = am.blocks.find(b => b.kind === "text");
      expect(txt).toBeDefined();
    }
  });

  // -----------------------------------------------------------------------
  // Extra: unknown event types are ignored
  // -----------------------------------------------------------------------
  it("ignores unknown event types", () => {
    const evt: StreamEvent = {
      type: "_adapter",
      subtype: "spawning",
    };
    const state = ingestAll([evt]);
    expect(state.messages).toHaveLength(0);
    expect(state.todos).toHaveLength(0);
    expect(state.result).toBeNull();
  });
});
