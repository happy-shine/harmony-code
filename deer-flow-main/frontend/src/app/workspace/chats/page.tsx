"use client";

/**
 * /workspace/chats — the harmony-code chat list.
 *
 * Lists every thread owned by the signed-in user (backed by
 * ``GET /api/threads`` on the gateway) and offers a "new chat" affordance
 * that routes to ``/workspace/chats/new`` (the ``[thread_id]`` page
 * creates the backend row lazily on the first send).
 *
 * This page replaced the LangGraph-era ``useThreads()`` implementation
 * when LangGraph was removed; ``titleOfThread`` / ``pathOfThread`` lived
 * in ``core/threads/utils.ts`` and depended on LangGraph-shaped thread
 * objects, so we render directly against the harmony-code shape here.
 */

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  WorkspaceBody,
  WorkspaceContainer,
  WorkspaceHeader,
} from "@/components/workspace/workspace-container";
import { useI18n } from "@/core/i18n/hooks";
import { useHarmonyThreads } from "@/core/threads/harmony-threads";
import type { HarmonyThread } from "@/core/threads/harmony-threads";
import { formatTimeAgo } from "@/core/utils/datetime";

function titleOfHarmonyThread(t: HarmonyThread): string {
  // Backend doesn't carry a title yet (M-future). Falling back to the
  // thread id keeps the list navigable without pretending we have one.
  return t.id;
}

export default function ChatsPage() {
  const { t } = useI18n();
  const { data: threads, isLoading, error } = useHarmonyThreads();
  const [search, setSearch] = useState("");

  useEffect(() => {
    document.title = `${t.pages.chats} - ${t.pages.appName}`;
  }, [t.pages.chats, t.pages.appName]);

  const filtered = useMemo(() => {
    if (!threads) return [];
    const needle = search.toLowerCase();
    if (!needle) return threads;
    return threads.filter((th) =>
      titleOfHarmonyThread(th).toLowerCase().includes(needle),
    );
  }, [threads, search]);

  return (
    <WorkspaceContainer>
      <WorkspaceHeader />
      <WorkspaceBody>
        <div className="flex size-full flex-col">
          <header className="flex shrink-0 items-center justify-center gap-3 pt-8">
            <Input
              type="search"
              className="h-12 w-full max-w-(--container-width-md) text-xl"
              placeholder={t.chats.searchChats}
              autoFocus
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            <Link
              href="/workspace/chats/new"
              className="rounded-lg bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-700"
            >
              New chat
            </Link>
          </header>
          <main className="min-h-0 flex-1">
            <ScrollArea className="size-full py-4">
              <div className="mx-auto flex size-full max-w-(--container-width-md) flex-col">
                {isLoading && (
                  <div className="text-muted-foreground p-4 text-sm">
                    Loading…
                  </div>
                )}
                {error && (
                  <div className="p-4 text-sm text-red-500">
                    Failed to load threads:{" "}
                    {error instanceof Error ? error.message : String(error)}
                  </div>
                )}
                {!isLoading && !error && filtered.length === 0 && (
                  <div className="text-muted-foreground p-4 text-sm">
                    No chats yet. Click “New chat” to start one.
                  </div>
                )}
                {filtered.map((thread) => (
                  <Link
                    key={thread.id}
                    href={`/workspace/chats/${thread.id}`}
                  >
                    <div className="flex flex-col gap-2 border-b p-4 hover:bg-neutral-50 dark:hover:bg-neutral-900">
                      <div className="font-mono text-sm">
                        {titleOfHarmonyThread(thread)}
                      </div>
                      {thread.updated_at && (
                        <div className="text-muted-foreground text-xs">
                          {formatTimeAgo(thread.updated_at)}
                        </div>
                      )}
                    </div>
                  </Link>
                ))}
              </div>
            </ScrollArea>
          </main>
        </div>
      </WorkspaceBody>
    </WorkspaceContainer>
  );
}
