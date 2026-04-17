"use client";

import { useQueryClient } from "@tanstack/react-query";
import { FolderIcon } from "lucide-react";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  TextBlock,
  ThinkingBlock,
  ToolUseBlock,
  SystemInitBanner,
  ResultFooter,
} from "@/components/workspace/cc-blocks";
import { FileBrowser } from "@/components/workspace/file-browser";
import type { UIMessage, UIBlock } from "@/core/messages/cc-reducer";
import { useThreadStream } from "@/core/threads/cc-hooks";
import { fetchHarmonyHistory } from "@/core/threads/harmony-threads";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function AssistantMessage({ msg }: { msg: Extract<UIMessage, { kind: "assistant" }> }) {
  return (
    <div className="space-y-1">
      {msg.blocks.map((b: UIBlock, bi: number) => {
        if (b.kind === "text") return <TextBlock key={bi} text={b.text} streaming={b.streaming} />;
        if (b.kind === "thinking")
          return <ThinkingBlock key={bi} text={b.text} streaming={b.streaming} />;
        if (b.kind === "tool_use") return <ToolUseBlock key={b.id} block={b} />;
        return null;
      })}
    </div>
  );
}

function UserBubble({ text }: { text: string }) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[80%] rounded-lg bg-blue-600 px-3 py-2 text-sm text-white">
        {text}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function CCChatPage() {
  const { thread_id: threadIdFromPath } = useParams<{ thread_id: string }>();
  const isNew = threadIdFromPath === "new";

  const [threadId, setThreadId] = useState<string | null>(
    isNew ? null : threadIdFromPath,
  );
  const [input, setInput] = useState("");
  const [pendingMessage, setPendingMessage] = useState<string | null>(null);
  const [showFiles, setShowFiles] = useState(false);
  const creatingRef = useRef(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const qc = useQueryClient();

  // Keep the state in sync with the URL when the user clicks a
  // different thread in the sidebar. Next's client-side router navigates
  // within the same dynamic route without remounting this component, so
  // our initial ``useState`` only runs once — without this effect the
  // first-mounted thread id stays pinned and every subsequent click
  // renders a stale transcript.
  useEffect(() => {
    const fromPath = isNew ? null : threadIdFromPath;
    if (fromPath !== threadId) {
      setThreadId(fromPath);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [threadIdFromPath, isNew]);

  // Hook uses the current threadId. When threadId is null (new thread) we
  // pass an empty string; the hook's `send` won't be called until threadId
  // is set (see pendingMessage pattern below).
  const stream = useThreadStream(threadId ?? "");

  // Auto-scroll to bottom when messages change
  useEffect(() => {
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [stream.messages, stream.status]);

  // Refresh the sidebar whenever a stream completes — the server captures
  // the thread's friendly title on the first prompt, so the row written at
  // `createThread` time starts out title-less and only gains one after the
  // POST /messages handler runs. Re-invalidating on idle is cheap (one
  // JSON list request) and keeps the sidebar in sync without extra plumbing.
  useEffect(() => {
    if (stream.status === "idle") {
      void qc.invalidateQueries({ queryKey: ["harmony-threads"] });
    }
  }, [stream.status, qc]);

  // Rehydrate the transcript when the user lands on an existing thread
  // (e.g. clicks a sidebar row, or reloads the page mid-conversation).
  // Skipped for ``new`` threads — there's nothing stored yet — and
  // skipped re-runs once the thread id stabilizes, so sending a message
  // doesn't trigger a refetch that would wipe the live SSE-rendered
  // transcript we just built up.
  const hydratedThreadRef = useRef<string | null>(null);
  useEffect(() => {
    if (!threadId || isNew) return;
    // StrictMode invokes effects twice in dev: first effect sets the
    // ref, cleanup fires, then the effect re-runs seeing the ref already
    // set and skipping. A naive ``let cancelled = false; cleanup →
    // cancelled = true`` guard would then cancel the first (only) fetch.
    // Guard on ref identity at dispatch time instead — if the ref
    // still matches when the fetch resolves, the user is still on this
    // thread and the hydrate is still wanted.
    if (hydratedThreadRef.current === threadId) return;
    hydratedThreadRef.current = threadId;
    void (async () => {
      try {
        const entries = await fetchHarmonyHistory(threadId);
        if (hydratedThreadRef.current !== threadId) return;
        stream.hydrateFromHistory(entries);
      } catch {
        // 404 (unknown/not-yours) or network error — leave transcript
        // empty, the user can still send a message.
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [threadId, isNew]);

  // --- New-thread creation ------------------------------------------------
  const createThread = useCallback(async (): Promise<string> => {
    const r = await fetch("/api/threads", {
      method: "POST",
      credentials: "include",
    });
    if (!r.ok) throw new Error(`Create thread failed: ${r.status}`);
    const data = (await r.json()) as { id: string };
    setThreadId(data.id);
    // Update URL without triggering a Next.js navigation (avoids remount)
    history.replaceState(null, "", `/workspace/chats/${data.id}`);
    // Refresh the sidebar / Chats list so the new thread appears immediately.
    void qc.invalidateQueries({ queryKey: ["harmony-threads"] });
    return data.id;
  }, [qc]);

  // When threadId becomes available and there is a pending message, fire
  // `send`. This avoids the stale-closure problem: after `setThreadId` the
  // component re-renders, `useThreadStream` receives the new threadId, a
  // fresh `send` is created, and *then* this effect dispatches the message.
  useEffect(() => {
    if (threadId && pendingMessage) {
      const msg = pendingMessage;
      setPendingMessage(null);
      void stream.send(msg);
    }
    // `stream` is intentionally omitted — we only want to fire when
    // threadId/pendingMessage change, and the latest `stream.send` is
    // already captured because React re-creates it when threadId changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [threadId, pendingMessage]);

  // --- Send handler -------------------------------------------------------
  const handleSend = useCallback(async () => {
    const text = input.trim();
    if (!text) return;
    setInput("");

    if (!threadId) {
      // First message on a new thread: create it, then queue the message.
      // Guard against double-send while createThread is in flight.
      if (creatingRef.current) return;
      creatingRef.current = true;
      try {
        await createThread();
      } finally {
        creatingRef.current = false;
      }
      setPendingMessage(text);
      return;
    }

    await stream.send(text);
    inputRef.current?.focus();
  }, [input, threadId, createThread, stream]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        void handleSend();
      }
    },
    [handleSend],
  );

  // --- Render -------------------------------------------------------------
  return (
    <div className="flex h-full">
      <div className="flex h-full min-w-0 flex-1 flex-col">
      {/* Header with file-browser toggle (only when a thread exists) */}
      {threadId && (
        <div className="flex items-center justify-end border-b border-neutral-200 px-4 py-1 dark:border-neutral-800">
          <button
            type="button"
            onClick={() => setShowFiles((v) => !v)}
            aria-pressed={showFiles}
            className={
              "flex items-center gap-1 rounded px-2 py-1 text-xs " +
              (showFiles
                ? "bg-blue-100 text-blue-900 dark:bg-blue-950 dark:text-blue-100"
                : "text-neutral-600 hover:bg-neutral-100 dark:text-neutral-300 dark:hover:bg-neutral-800")
            }
          >
            <FolderIcon size={14} />
            Files
          </button>
        </div>
      )}

      {/* Messages area */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 pt-4">
        <div className="mx-auto max-w-3xl space-y-2 pb-4">
          {stream.messages.map((m: UIMessage, i: number) => {
            if (m.kind === "system_init") {
              return (
                <SystemInitBanner
                  key={`init-${i}`}
                  sessionId={m.sessionId}
                  model={m.model}
                  cwd={m.cwd}
                  tools={m.tools}
                  mcpServers={m.mcpServers}
                />
              );
            }
            if (m.kind === "user") {
              return <UserBubble key={m.id} text={m.text} />;
            }
            if (m.kind === "assistant") {
              return <AssistantMessage key={m.id} msg={m} />;
            }
            return null;
          })}

          {stream.result && (
            <ResultFooter
              duration_ms={stream.result.duration_ms}
              total_cost_usd={stream.result.total_cost_usd}
              usage={stream.result.usage}
            />
          )}

          {stream.status === "running" && (
            <div className="animate-pulse text-xs text-neutral-400">
              Claude is thinking...
            </div>
          )}

          {stream.status === "error" && (
            <div className="text-xs text-red-500">
              Error:{" "}
              {stream.error instanceof Error
                ? stream.error.message
                : String(stream.error)}
            </div>
          )}
        </div>
      </div>

      {/* Todos strip (when present) */}
      {stream.todos.length > 0 && (
        <div className="border-t border-neutral-200 px-4 py-2 dark:border-neutral-800">
          <div className="mx-auto max-w-3xl">
            <div className="mb-1 text-xs font-medium text-neutral-500">
              Tasks
            </div>
            <div className="space-y-1">
              {stream.todos.map((todo, i) => (
                <div key={i} className="flex items-center gap-2 text-xs">
                  <span className="inline-block w-4 text-center">
                    {todo.status === "completed"
                      ? "\u2705"
                      : todo.status === "in_progress"
                        ? "\u{1F504}"
                        : "\u2B1C"}
                  </span>
                  <span
                    className={
                      todo.status === "completed"
                        ? "text-neutral-400 line-through"
                        : ""
                    }
                  >
                    {todo.content}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Composer */}
      <div className="border-t border-neutral-200 px-4 py-3 dark:border-neutral-800">
        <div className="mx-auto flex max-w-3xl gap-2">
          <input
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Type a message..."
            disabled={stream.status === "running"}
            className="flex-1 rounded-lg border border-neutral-300 bg-transparent px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-50 dark:border-neutral-700"
            autoFocus
          />
          {stream.status === "running" ? (
            <button
              onClick={stream.cancel}
              className="rounded-lg bg-red-600 px-4 py-2 text-sm text-white hover:bg-red-700"
            >
              Stop
            </button>
          ) : (
            <button
              onClick={() => void handleSend()}
              disabled={!input.trim()}
              className="rounded-lg bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
            >
              Send
            </button>
          )}
        </div>
      </div>
      </div>

      {showFiles && threadId && (
        <aside className="hidden h-full w-[520px] shrink-0 md:flex">
          <FileBrowser threadId={threadId} className="h-full w-full" />
        </aside>
      )}
    </div>
  );
}
