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
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

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

  const reset = useCallback(() => {
    dispatch({ type: "reset" });
  }, []);

  return { ...state, status, error, send, cancel, reset };
}
