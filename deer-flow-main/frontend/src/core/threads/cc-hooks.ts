"use client";

// Copyright (c) 2025 Bytedance Ltd. and/or its affiliates
// SPDX-License-Identifier: Apache-2.0

import { useCallback, useReducer, useRef, useState } from "react";

import {
  initialMessageState,
  messageReducer,
} from "@/core/messages/cc-reducer";
import type { Action } from "@/core/messages/cc-reducer";

import { openMessageStream } from "./cc-stream";

export function useThreadStream(threadId: string) {
  const [state, dispatch] = useReducer(
    messageReducer,
    undefined,
    initialMessageState,
  );
  const [status, setStatus] = useState<"idle" | "running" | "error">("idle");
  const [error, setError] = useState<unknown>(null);
  const abortRef = useRef<AbortController | null>(null);

  const send = useCallback(
    async (content: string, attachments: string[] = []) => {
      // Abort any in-flight stream
      if (abortRef.current) {
        abortRef.current.abort();
      }
      const controller = new AbortController();
      abortRef.current = controller;

      // Echo the user bubble locally — the gateway does not echo a
      // user-turn frame for the prompt itself, so without this the
      // transcript would jump straight from the previous turn to the
      // new assistant reply.
      const userMsgId = `u-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      dispatch({ type: "add_user_message", id: userMsgId, text: content, attachments } as Action);

      setStatus("running");
      setError(null);
      try {
        for await (const ev of openMessageStream(
          threadId,
          { content, attachments },
          controller.signal,
        )) {
          if (ev.type === "_done") break;
          if (ev.type === "_error") {
            setError(ev.payload);
            setStatus("error");
            return;
          }
          dispatch({ type: "ingest", event: ev.event } as Action);
          // Yield to the browser between per-token deltas so React can
          // commit + paint each update. Without this, multiple deltas
          // that arrive in the same microtask (or that are parsed out of
          // a single fetch chunk) get batched into one commit and the
          // assistant reply appears to "pop in" in chunks instead of
          // streaming smoothly. rAF naturally caps re-renders at the
          // display refresh rate so we don't thrash the DOM at >60Hz.
          if (
            ev.event.type === "stream_event" &&
            typeof requestAnimationFrame !== "undefined"
          ) {
            await new Promise<void>((resolve) =>
              requestAnimationFrame(() => resolve()),
            );
          }
        }
        setStatus("idle");
      } catch (e: unknown) {
        if (e instanceof DOMException && e.name === "AbortError") {
          setStatus("idle");
        } else {
          setError(e);
          setStatus("error");
        }
      } finally {
        if (abortRef.current === controller) {
          abortRef.current = null;
        }
      }
    },
    [threadId],
  );

  const cancel = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
  }, []);

  const hydrateFromHistory = useCallback(
    (
      entries: Array<
        | { kind: "user_turn"; id: string; text: string }
        | { kind: "event"; event: { type: string; [key: string]: unknown } }
      >,
    ) => {
      // Reset current state first so reopening a thread doesn't concat
      // the previous session's transcript onto whatever we just loaded.
      dispatch({ type: "reset" });
      for (const entry of entries) {
        if (entry.kind === "user_turn") {
          dispatch({
            type: "add_user_message",
            id: entry.id,
            text: entry.text,
          } as Action);
        } else {
          dispatch({ type: "ingest", event: entry.event } as Action);
        }
      }
    },
    [],
  );

  const reset = useCallback(() => {
    dispatch({ type: "reset" });
  }, []);

  return { ...state, status, error, send, cancel, reset, hydrateFromHistory };
}
