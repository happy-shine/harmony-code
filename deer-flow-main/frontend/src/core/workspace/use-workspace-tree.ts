"use client";

// Copyright (c) 2025 Bytedance Ltd. and/or its affiliates
// SPDX-License-Identifier: Apache-2.0

import { useCallback, useEffect, useRef, useState } from "react";

import { fetchWorkspaceTree } from "./api";
import type { WorkspaceTreeResponse } from "./types";

export interface UseWorkspaceTreeResult {
  data?: WorkspaceTreeResponse;
  error?: Error;
  loading: boolean;
  refresh: () => void;
}

/**
 * Fetch-on-mount hook for the workspace tree.
 *
 * Re-fetches whenever `threadId` changes or `refresh()` is invoked. In-flight
 * requests are aborted before a new one is issued and on unmount. An empty
 * `threadId` is treated as "no thread yet" — loading stays false and no
 * request is dispatched.
 */
export function useWorkspaceTree(threadId: string): UseWorkspaceTreeResult {
  const [data, setData] = useState<WorkspaceTreeResponse | undefined>();
  const [error, setError] = useState<Error | undefined>();
  const [loading, setLoading] = useState(false);
  const [nonce, setNonce] = useState(0);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!threadId) {
      setData(undefined);
      setError(undefined);
      setLoading(false);
      return;
    }

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setLoading(true);
    setError(undefined);

    fetchWorkspaceTree(threadId, controller.signal)
      .then((resp) => {
        if (controller.signal.aborted) return;
        setData(resp);
        setLoading(false);
      })
      .catch((e: unknown) => {
        if (controller.signal.aborted) return;
        if (e instanceof DOMException && e.name === "AbortError") return;
        setError(e instanceof Error ? e : new Error(String(e)));
        setLoading(false);
      });

    return () => {
      controller.abort();
      if (abortRef.current === controller) {
        abortRef.current = null;
      }
    };
  }, [threadId, nonce]);

  const refresh = useCallback(() => {
    setNonce((n) => n + 1);
  }, []);

  return { data, error, loading, refresh };
}
