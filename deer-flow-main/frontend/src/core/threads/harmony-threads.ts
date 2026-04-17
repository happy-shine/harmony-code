"use client";

/**
 * Harmony-code-native thread list + delete hooks.
 *
 * The legacy ``./hooks.ts`` file speaks to LangGraph's JS SDK; this
 * module talks to the harmony-code gateway directly over ``fetch``.
 * Kept separate so we can wholesale-remove the LangGraph holdover once
 * every importer has migrated, without touching this one.
 *
 * Endpoints used:
 *   - ``GET /api/threads`` — list of the caller's threads.
 *   - ``DELETE /api/threads/{id}`` — delete a thread row.
 *
 * All calls go through Next.js rewrites to the gateway
 * (``next.config.js``); ``credentials: "include"`` is unnecessary when
 * same-origin (the browser sends the ``harmony_session`` cookie
 * automatically) but harmless and future-proof for an eventual
 * cross-origin deploy.
 */

import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import {
  handleUnauthorized,
  UnauthorizedError,
} from "@/core/api/unauthorized";

export interface HarmonyThread {
  id: string;
  title: string | null;
  updated_at: string | null;
  has_session: boolean;
}

/** One entry in the thread history payload. ``user_turn`` maps to the
 *  reducer's ``add_user_message`` action; ``event`` feeds the normal
 *  ``ingest`` pipeline that assistant frames from live SSE also use. */
export type HarmonyHistoryEntry =
  | { kind: "user_turn"; id: string; text: string }
  | { kind: "event"; event: { type: string; [key: string]: unknown } };

export async function fetchHarmonyHistory(
  threadId: string,
): Promise<HarmonyHistoryEntry[]> {
  const r = await fetch(`/api/threads/${threadId}/history`, {
    method: "GET",
    credentials: "include",
    cache: "no-store",
  });
  if (handleUnauthorized(r)) throw new UnauthorizedError();
  if (!r.ok) {
    // 404 = unknown-or-not-yours; the page simply renders an empty
    // transcript in that case, so we surface a typed error for callers
    // that want to distinguish rather than throwing opaquely.
    let detail = `${r.status}`;
    try {
      const j = (await r.json()) as { detail?: string };
      if (j.detail) detail = j.detail;
    } catch {
      // fall through
    }
    throw new Error(detail);
  }
  const j = (await r.json()) as { messages: HarmonyHistoryEntry[] };
  return j.messages ?? [];
}

interface HarmonyThreadList {
  threads: HarmonyThread[];
}

export async function fetchHarmonyThreads(): Promise<HarmonyThread[]> {
  const r = await fetch("/api/threads", {
    method: "GET",
    credentials: "include",
    cache: "no-store",
  });
  if (handleUnauthorized(r)) throw new UnauthorizedError();
  if (!r.ok) throw new Error(`list threads failed: ${r.status}`);
  const j = (await r.json()) as HarmonyThreadList;
  return j.threads ?? [];
}

export async function deleteHarmonyThread(id: string): Promise<void> {
  const r = await fetch(`/api/threads/${id}`, {
    method: "DELETE",
    credentials: "include",
  });
  if (handleUnauthorized(r)) throw new UnauthorizedError();
  if (!r.ok) {
    // 409 = thread_busy (stream in flight); 404 = not-yours-or-unknown.
    let detail = `${r.status}`;
    try {
      const j = (await r.json()) as { detail?: string };
      if (j.detail) detail = j.detail;
    } catch {
      // fall through
    }
    throw new Error(detail);
  }
}

export function useHarmonyThreads() {
  return useQuery<HarmonyThread[]>({
    queryKey: ["harmony-threads"],
    queryFn: fetchHarmonyThreads,
    // Stay fresh during a session — threads are cheap to re-list and
    // the user expects a just-created thread to appear immediately.
    staleTime: 5_000,
  });
}

export function useDeleteHarmonyThread() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteHarmonyThread(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["harmony-threads"] });
    },
  });
}
