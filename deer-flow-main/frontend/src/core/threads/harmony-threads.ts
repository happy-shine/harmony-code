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

export interface HarmonyThread {
  id: string;
  title: string | null;
  updated_at: string | null;
  has_session: boolean;
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
  if (!r.ok) throw new Error(`list threads failed: ${r.status}`);
  const j = (await r.json()) as HarmonyThreadList;
  return j.threads ?? [];
}

export async function deleteHarmonyThread(id: string): Promise<void> {
  const r = await fetch(`/api/threads/${id}`, {
    method: "DELETE",
    credentials: "include",
  });
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
