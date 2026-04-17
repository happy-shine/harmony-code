// Copyright (c) 2025 Bytedance Ltd. and/or its affiliates
// SPDX-License-Identifier: Apache-2.0

/**
 * TypeScript types for Claude Code `--output-format stream-json` frames.
 * Each type maps 1:1 to a CC stream event shape.
 */

// ---------------------------------------------------------------------------
// Content blocks
// ---------------------------------------------------------------------------

export type CCBlock =
  | { type: "text"; text: string }
  | { type: "thinking"; thinking: string }
  | { type: "tool_use"; id: string; name: string; input: unknown }
  | {
      type: "tool_result";
      tool_use_id: string;
      content: string | Array<{ type: string; [key: string]: unknown }>;
      is_error?: boolean;
    };

// ---------------------------------------------------------------------------
// Stream events
// ---------------------------------------------------------------------------

export type CCAssistantEvent = {
  type: "assistant";
  message: {
    id: string;
    role: "assistant";
    content: CCBlock[];
    stop_reason?: string;
  };
  parent_tool_use_id?: string;
};

export type CCUserEvent = {
  type: "user";
  message: { content: CCBlock[] };
  parent_tool_use_id?: string;
};

export type CCSystemInitEvent = {
  type: "system";
  subtype: "init";
  session_id: string;
  model: string;
  cwd: string;
  tools: string[];
  mcp_servers: Array<{ name: string; status: string }>;
};

export type CCResultEvent = {
  type: "result";
  subtype: "success" | "error";
  duration_ms: number;
  total_cost_usd?: number;
  usage?: {
    input_tokens: number;
    output_tokens: number;
    cache_read_tokens?: number;
    cache_write_tokens?: number;
  };
  result?: string;
  session_id?: string;
};

export type CCAdapterEvent = {
  type: "_adapter";
  subtype: "spawning" | "spawned" | "error";
  [key: string]: unknown;
};

// ---------------------------------------------------------------------------
// Streaming deltas (emitted when ``--include-partial-messages`` is set on
// the CLI). The shape mirrors Anthropic's Messages API streaming events
// nested under a ``stream_event`` envelope.
// ---------------------------------------------------------------------------

export type CCStreamEvent = {
  type: "stream_event";
  event:
    | {
        type: "message_start";
        message: { id: string; model?: string };
      }
    | {
        type: "content_block_start";
        index: number;
        content_block:
          | { type: "text"; text: string }
          | { type: "thinking"; thinking: string }
          | { type: "tool_use"; id: string; name: string; input: unknown };
      }
    | {
        type: "content_block_delta";
        index: number;
        delta:
          | { type: "text_delta"; text: string }
          | { type: "thinking_delta"; thinking: string }
          | { type: "input_json_delta"; partial_json: string };
      }
    | { type: "content_block_stop"; index: number }
    | { type: "message_delta"; delta: { stop_reason?: string } }
    | { type: "message_stop" };
  parent_tool_use_id?: string | null;
};

// ---------------------------------------------------------------------------
// Union — all recognized events plus catch-all for unknown frame types
// ---------------------------------------------------------------------------

export type StreamEvent =
  | CCAssistantEvent
  | CCUserEvent
  | CCSystemInitEvent
  | CCResultEvent
  | CCAdapterEvent
  | CCStreamEvent
  | { type: string; [key: string]: unknown };
