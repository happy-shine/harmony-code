"use client";

// Copyright (c) 2025 Bytedance Ltd. and/or its affiliates
// SPDX-License-Identifier: Apache-2.0

import { useCallback, useEffect, useReducer, useRef, useState } from "react";

import {
  initialMessageState,
  messageReducer,
} from "@/core/messages/cc-reducer";
import type { Action } from "@/core/messages/cc-reducer";

import {
  cancelRun,
  openMessageStream,
  openResumeStream,
  type StreamYield,
} from "./cc-stream";

/** Max consecutive reconnect attempts before giving up and surfacing an
 *  error to the user. The backend keeps a finished run's bus around for
 *  ~30 minutes (RunnerRegistry.IDLE_RETENTION_SECONDS), so a few quick
 *  retries is enough to ride out any blip the network throws at us. */
const MAX_RECONNECTS = 5;
/** Backoff schedule between reconnects (ms). Capped — we don't want to
 *  wait minutes between attempts when the user is staring at the screen.
 *  Index past the end uses the last value. */
const RECONNECT_BACKOFF_MS = [250, 500, 1000, 2000, 4000];

export function useThreadStream(threadId: string) {
  const [state, dispatch] = useReducer(
    messageReducer,
    undefined,
    initialMessageState,
  );
  const [status, setStatus] = useState<"idle" | "running" | "error">("idle");
  const [error, setError] = useState<unknown>(null);
  // ``abortRef`` is the AbortController for the currently-active SSE
  // fetch (POST or GET reconnect). Aborting it severs THIS browser's
  // subscription to the runner — the runner itself keeps going on the
  // server. ``cancel()`` (the user-facing stop button) hits POST /cancel
  // separately, which is what actually terminates the agent.
  const abortRef = useRef<AbortController | null>(null);
  // Last opaque SSE id we received from the backend
  // (``<run_id>:<event_id>``). Echoed back as ``Last-Event-ID`` on
  // reconnect so the backend can resume from the next event. Reset to
  // ``null`` when starting a fresh turn.
  const lastEventIdRef = useRef<string | null>(null);
  // ``send`` may unmount mid-flight (page nav). Track that so callbacks
  // captured by the running async iterator don't update React state on
  // an unmounted component.
  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      // Drop the SSE subscription on unmount but DO NOT cancel the
      // backend run — the runner is decoupled from this HTTP request
      // and will finish on its own. A future remount on the same
      // thread can resume via openResumeStream.
      abortRef.current?.abort();
      abortRef.current = null;
    };
  }, []);

  /** Drive a stream to completion, dispatching events into the reducer.
   *  Returns a sentinel describing how the stream ended so the caller
   *  can decide whether to reconnect. */
  const consumeStream = useCallback(
    async (
      gen: AsyncGenerator<StreamYield | { type: "_no_active_run" }>,
    ): Promise<"done" | "error" | "no_active_run" | "interrupted"> => {
      try {
        for await (const ev of gen) {
          if (ev.type === "_no_active_run") return "no_active_run";
          if (ev.type === "_done") return "done";
          if (ev.type === "_error") {
            if (mountedRef.current) {
              setError(ev.payload);
              setStatus("error");
            }
            return "error";
          }
          if (ev.id) lastEventIdRef.current = ev.id;
          dispatch({ type: "ingest", event: ev.event } as Action);
          // Yield to the browser between per-token deltas so React can
          // commit + paint each update. See the previous comment for the
          // full rationale on why rAF here matters for streaming UX.
          if (
            ev.event.type === "stream_event" &&
            typeof requestAnimationFrame !== "undefined"
          ) {
            await new Promise<void>((resolve) =>
              requestAnimationFrame(() => resolve()),
            );
          }
        }
        // Generator exhausted without a `_done` sentinel — treat as a
        // mid-stream interruption (network drop, server closed body).
        return "interrupted";
      } catch (e: unknown) {
        if (e instanceof DOMException && e.name === "AbortError") {
          return "interrupted";
        }
        // Fetch-level failure (TypeError "Failed to fetch", HTTP non-2xx
        // from the initial response, etc.). Caller decides retry policy.
        throw e;
      }
    },
    [],
  );

  const send = useCallback(
    async (content: string, attachments: string[] = []) => {
      // Abort any in-flight subscription. NOTE: this only severs *our*
      // SSE link — it does NOT cancel the backend runner. If a previous
      // run is still going, the new POST will get 409 ``thread_busy``
      // and we surface that as an error.
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      lastEventIdRef.current = null;

      // Echo the user bubble locally — the gateway does not echo a
      // user-turn frame for the prompt itself.
      const userMsgId = `u-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      dispatch({ type: "add_user_message", id: userMsgId, text: content, attachments } as Action);

      setStatus("running");
      setError(null);
      try {
        const initial = openMessageStream(
          threadId,
          { content, attachments },
          controller.signal,
        );
        let outcome = await consumeStream(initial);

        // Auto-reconnect loop. Triggered when the stream was interrupted
        // (network drop / proxy idle-timeout / page came back from
        // background) but the run itself may still be alive on the
        // backend. We keep re-subscribing via GET /stream with the last
        // seen event id until we see a terminal frame, hit the retry
        // cap, or the user navigates away.
        let attempts = 0;
        while (
          outcome === "interrupted" &&
          attempts < MAX_RECONNECTS &&
          !controller.signal.aborted &&
          mountedRef.current
        ) {
          const delay = RECONNECT_BACKOFF_MS[Math.min(attempts, RECONNECT_BACKOFF_MS.length - 1)] ?? 4000;
          await new Promise<void>((resolve) => setTimeout(resolve, delay));
          if (controller.signal.aborted || !mountedRef.current) break;
          attempts += 1;
          try {
            const resume = openResumeStream(
              threadId,
              controller.signal,
              lastEventIdRef.current ?? undefined,
            );
            outcome = await consumeStream(resume);
            if (outcome === "no_active_run") {
              // Server has nothing buffered — the run finished and was
              // GC'd, OR it never started. Either way, treat as natural
              // end: the rest of the assistant reply is unrecoverable
              // from the live stream, but the history endpoint
              // (`GET /threads/:tid/history`) still has the full final
              // transcript on next page load.
              outcome = "done";
              break;
            }
          } catch {
            // Reconnect attempt failed at the transport layer — fall
            // through and retry until the budget runs out.
            outcome = "interrupted";
          }
        }

        if (!mountedRef.current) return;
        if (outcome === "done") {
          setStatus("idle");
        } else if (outcome === "interrupted") {
          // Out of reconnect budget. Surface as error so the UI can
          // prompt the user to reload (the history endpoint will
          // backfill whatever finished while disconnected).
          setError(new Error("stream interrupted; reconnect budget exhausted"));
          setStatus("error");
        }
        // "error" outcome already set status inside consumeStream.
      } catch (e: unknown) {
        if (!mountedRef.current) return;
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
    [threadId, consumeStream],
  );

  /** User-facing stop button. Hits the backend ``/cancel`` endpoint to
   *  actually terminate the agent (SIGTERM the CC subprocess), THEN
   *  aborts our local SSE subscription so the in-flight reconnect loop
   *  unwinds. Order matters: aborting first would tear down the SSE,
   *  the runner would keep going, and the user would see "stopped" in
   *  the UI while the backend is still spending tokens. */
  const cancel = useCallback(() => {
    void cancelRun(threadId);
    abortRef.current?.abort();
    abortRef.current = null;
    setStatus("idle");
  }, [threadId]);

  /** Subscribe to a thread's currently active run without sending a new
   *  message — used when remounting onto a thread that has a running
   *  agent (e.g. user navigated away and came back). No-op if no run
   *  is active on the server. */
  const resume = useCallback(async () => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setStatus("running");
    setError(null);
    try {
      const gen = openResumeStream(
        threadId,
        controller.signal,
        lastEventIdRef.current ?? undefined,
      );
      const outcome = await consumeStream(gen);
      if (!mountedRef.current) return;
      if (outcome === "done" || outcome === "no_active_run") setStatus("idle");
      else if (outcome === "error") {
        // status already set inside consumeStream
      } else {
        setStatus("idle"); // interrupted on a passive resume — let user reload
      }
    } catch (e) {
      if (!mountedRef.current) return;
      if (e instanceof DOMException && e.name === "AbortError") setStatus("idle");
      else {
        setError(e);
        setStatus("error");
      }
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
    }
  }, [threadId, consumeStream]);

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
    lastEventIdRef.current = null;
  }, []);

  return {
    ...state,
    status,
    error,
    send,
    cancel,
    resume,
    reset,
    hydrateFromHistory,
  };
}
