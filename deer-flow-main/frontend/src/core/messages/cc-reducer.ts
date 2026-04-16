// Copyright (c) 2025 Bytedance Ltd. and/or its affiliates
// SPDX-License-Identifier: Apache-2.0

/**
 * Message reducer for Claude Code stream events.
 *
 * Ingests StreamEvent frames and produces a MessageState suitable for
 * rendering in the UI. Key behaviors:
 *  - Merges assistant frames with the same message.id (CC sends full
 *    content arrays, not deltas).
 *  - Diverts TodoWrite tool_use blocks to state.todos instead of UI blocks.
 *  - Backfills tool_result content from user events onto matching tool_use
 *    blocks in earlier assistant messages.
 */

import type {
  CCAssistantEvent,
  CCBlock,
  CCResultEvent,
  CCSystemInitEvent,
  CCUserEvent,
  StreamEvent,
} from "@/core/cc-events/types";

// ---------------------------------------------------------------------------
// UIBlock — UI-friendly representation of a CC content block
// ---------------------------------------------------------------------------

export type UIBlock =
  | { kind: "text"; text: string; streaming: boolean }
  | { kind: "thinking"; text: string; streaming: boolean; expanded: boolean }
  | {
      kind: "tool_use";
      id: string;
      name: string;
      input: unknown;
      status: "running" | "ok" | "error";
      result?: unknown;
    };

// ---------------------------------------------------------------------------
// UIMessage — a single renderable message
// ---------------------------------------------------------------------------

export type UIMessage =
  | { kind: "user"; id: string; text: string; attachments: string[] }
  | {
      kind: "assistant";
      id: string;
      blocks: UIBlock[];
      stopReason?: string;
      parentToolUseId?: string;
    }
  | {
      kind: "system_init";
      sessionId: string;
      model: string;
      cwd: string;
      tools: string[];
      mcpServers: Array<{ name: string; status: string }>;
    };

// ---------------------------------------------------------------------------
// Aggregate state
// ---------------------------------------------------------------------------

export type ResultState = {
  duration_ms: number;
  total_cost_usd?: number;
  usage?: { input_tokens: number; output_tokens: number };
};

export type Todo = { content: string; status: string; activeForm?: string };

export type MessageState = {
  messages: UIMessage[];
  todos: Todo[];
  result: ResultState | null;
};

export function initialMessageState(): MessageState {
  return { messages: [], todos: [], result: null };
}

// ---------------------------------------------------------------------------
// Actions
// ---------------------------------------------------------------------------

export type Action = { type: "ingest"; event: StreamEvent } | { type: "reset" };

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Convert a CC content block to a UIBlock. Returns undefined for blocks
 *  that should not appear in the UI (e.g. tool_result — those are backfilled). */
function blockToUIBlock(block: CCBlock, hasStopReason: boolean): UIBlock | undefined {
  switch (block.type) {
    case "text":
      return { kind: "text", text: block.text, streaming: !hasStopReason };
    case "thinking":
      return {
        kind: "thinking",
        text: block.thinking,
        streaming: !hasStopReason,
        expanded: false,
      };
    case "tool_use":
      return {
        kind: "tool_use",
        id: block.id,
        name: block.name,
        input: block.input,
        status: "running",
      };
    case "tool_result":
      // tool_result blocks appear in user events; they are handled separately
      // via backfill logic, not rendered directly.
      return undefined;
    default:
      return undefined;
  }
}

/** Is this block a TodoWrite tool_use? */
function isTodoWrite(block: CCBlock): boolean {
  return block.type === "tool_use" && block.name === "TodoWrite";
}

/** Extract todos from a TodoWrite tool_use input. */
function extractTodos(input: unknown): Todo[] {
  if (
    input != null &&
    typeof input === "object" &&
    "todos" in input &&
    Array.isArray((input as { todos: unknown }).todos)
  ) {
    return ((input as { todos: unknown[] }).todos)
      .filter(
        (t): t is { content: string; status: string } =>
          t != null &&
          typeof t === "object" &&
          "content" in t &&
          "status" in t &&
          typeof (t as { content: unknown }).content === "string" &&
          typeof (t as { status: unknown }).status === "string",
      )
      .map((t) => ({ content: t.content, status: t.status }));
  }
  return [];
}

/**
 * Merge new blocks into existing blocks.
 *
 * CC sends the full `message.content` array with every assistant event for
 * the same message.id, so the new array is authoritative. However we must
 * preserve any `status`/`result` data we previously backfilled onto tool_use
 * blocks from user tool_result events.
 */
function mergeBlocks(prev: UIBlock[], next: UIBlock[]): UIBlock[] {
  // Build a lookup of previously-backfilled tool_use data by id
  const prevToolData = new Map<
    string,
    { status: "running" | "ok" | "error"; result?: unknown }
  >();
  for (const b of prev) {
    if (b.kind === "tool_use" && b.status !== "running") {
      prevToolData.set(b.id, { status: b.status, result: b.result });
    }
  }

  return next.map((b) => {
    if (b.kind === "tool_use") {
      const existing = prevToolData.get(b.id);
      if (existing) {
        return { ...b, status: existing.status, result: existing.result };
      }
    }
    return b;
  });
}

// ---------------------------------------------------------------------------
// Event handlers
// ---------------------------------------------------------------------------

function handleSystemInit(
  state: MessageState,
  evt: CCSystemInitEvent,
): MessageState {
  const msg: UIMessage = {
    kind: "system_init",
    sessionId: evt.session_id,
    model: evt.model,
    cwd: evt.cwd,
    tools: evt.tools,
    mcpServers: evt.mcp_servers,
  };
  return { ...state, messages: [...state.messages, msg] };
}

function handleResult(state: MessageState, evt: CCResultEvent): MessageState {
  const result: ResultState = {
    duration_ms: evt.duration_ms,
    total_cost_usd: evt.total_cost_usd,
    usage:
      evt.usage != null
        ? {
            input_tokens: evt.usage.input_tokens,
            output_tokens: evt.usage.output_tokens,
          }
        : undefined,
  };
  return { ...state, result };
}

function handleAssistant(
  state: MessageState,
  evt: CCAssistantEvent,
): MessageState {
  const { message, parent_tool_use_id } = evt;
  const hasStopReason = message.stop_reason != null && message.stop_reason !== "";

  // Separate TodoWrite blocks from regular blocks
  let newTodos = state.todos;
  const contentBlocks = message.content.filter((b) => {
    if (isTodoWrite(b)) {
      if (b.type === "tool_use") {
        newTodos = extractTodos(b.input);
      }
      return false;
    }
    return true;
  });

  // Convert CC blocks to UIBlocks
  const uiBlocks: UIBlock[] = [];
  for (const b of contentBlocks) {
    const uib = blockToUIBlock(b, hasStopReason);
    if (uib) uiBlocks.push(uib);
  }

  // Find existing assistant message with same id
  const existingIdx = state.messages.findIndex(
    (m) => m.kind === "assistant" && m.id === message.id,
  );

  if (existingIdx >= 0) {
    // Merge into existing message
    const existing = state.messages[existingIdx]!;
    if (existing.kind !== "assistant") return state; // type guard — should not happen

    const mergedBlocks = mergeBlocks(existing.blocks, uiBlocks);
    const updated: UIMessage = {
      kind: "assistant",
      id: message.id,
      blocks: mergedBlocks,
      stopReason: message.stop_reason ?? existing.stopReason,
      parentToolUseId: parent_tool_use_id ?? existing.parentToolUseId,
    };

    const newMessages = [...state.messages];
    newMessages[existingIdx] = updated;
    return { ...state, messages: newMessages, todos: newTodos };
  }

  // New assistant message
  const msg: UIMessage = {
    kind: "assistant",
    id: message.id,
    blocks: uiBlocks,
    stopReason: message.stop_reason,
    parentToolUseId: parent_tool_use_id,
  };
  return { ...state, messages: [...state.messages, msg], todos: newTodos };
}

function handleUser(state: MessageState, evt: CCUserEvent): MessageState {
  // User events in CC contain tool_result blocks that should be backfilled
  // onto the corresponding tool_use blocks in previous assistant messages.
  const toolResults = evt.message.content.filter(
    (b): b is Extract<CCBlock, { type: "tool_result" }> =>
      b.type === "tool_result",
  );

  if (toolResults.length === 0) {
    // Plain user text message (rare in CC agent mode, but possible)
    const textBlocks = evt.message.content.filter(
      (b): b is Extract<CCBlock, { type: "text" }> => b.type === "text",
    );
    const text = textBlocks.map((b) => b.text).join("\n");
    if (text) {
      const msg: UIMessage = {
        kind: "user",
        id: `user-${state.messages.length}`,
        text,
        attachments: [],
      };
      return { ...state, messages: [...state.messages, msg] };
    }
    return state;
  }

  // Backfill tool results onto matching tool_use blocks
  const newMessages = state.messages.map((m) => {
    if (m.kind !== "assistant") return m;

    let changed = false;
    const newBlocks = m.blocks.map((b) => {
      if (b.kind !== "tool_use") return b;

      const result = toolResults.find((tr) => tr.tool_use_id === b.id);
      if (!result) return b;

      changed = true;
      return {
        ...b,
        status: (result.is_error ? "error" : "ok") as "ok" | "error",
        result: result.content,
      };
    });

    return changed ? { ...m, blocks: newBlocks } : m;
  });

  return { ...state, messages: newMessages };
}

// ---------------------------------------------------------------------------
// Reducer
// ---------------------------------------------------------------------------

export function messageReducer(
  state: MessageState,
  action: Action,
): MessageState {
  if (action.type === "reset") {
    return initialMessageState();
  }

  const { event } = action;

  switch (event.type) {
    case "system":
      if ("subtype" in event && event.subtype === "init") {
        return handleSystemInit(state, event as CCSystemInitEvent);
      }
      return state;

    case "result":
      return handleResult(state, event as CCResultEvent);

    case "assistant":
      return handleAssistant(state, event as CCAssistantEvent);

    case "user":
      return handleUser(state, event as CCUserEvent);

    default:
      // _adapter, hooks, unknown — ignore
      return state;
  }
}
