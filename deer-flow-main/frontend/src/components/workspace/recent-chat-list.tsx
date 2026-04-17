"use client";

/**
 * Sidebar recent-chats list.
 *
 * Previously powered by LangGraph's ``useThreads()`` with rename / share /
 * export / state-dump wired to the LangGraph SDK. After the M5 rewrite
 * the backend doesn't surface a thread title, state dump, or streaming
 * message list by id, so this harmony-code version is deliberately
 * trimmed to: list the caller's threads and delete one.
 *
 * Rename / share / markdown+json export come back in M-future once the
 * backend exposes a persisted title + message log.
 */
import { MoreHorizontal, Trash2 } from "lucide-react";
import Link from "next/link";
import { useParams, usePathname, useRouter } from "next/navigation";
import { useCallback } from "react";
import { toast } from "sonner";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarMenu,
  SidebarMenuAction,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar";
import { useI18n } from "@/core/i18n/hooks";
import {
  useDeleteHarmonyThread,
  useHarmonyThreads,
} from "@/core/threads/harmony-threads";

export function RecentChatList() {
  const { t } = useI18n();
  const router = useRouter();
  const pathname = usePathname();
  const { thread_id: threadIdFromPath } = useParams<{ thread_id?: string }>();

  const { data: threads = [] } = useHarmonyThreads();
  const { mutate: deleteThread } = useDeleteHarmonyThread();

  const handleDelete = useCallback(
    (threadId: string) => {
      deleteThread(threadId, {
        onSuccess: () => {
          if (threadId === threadIdFromPath) {
            // Navigate away from the thread we just removed. Prefer the
            // next remaining thread (newest-first list), fall back to the
            // "new chat" route.
            const remaining = threads.filter((th) => th.id !== threadId);
            const next = remaining[0];
            router.push(next ? `/workspace/chats/${next.id}` : "/workspace/chats/new");
          }
        },
        onError: (err) => {
          toast.error(
            err instanceof Error ? err.message : "Failed to delete thread",
          );
        },
      });
    },
    [deleteThread, router, threadIdFromPath, threads],
  );

  if (threads.length === 0) return null;

  return (
    <SidebarGroup>
      <SidebarGroupLabel>{t.sidebar.recentChats}</SidebarGroupLabel>
      <SidebarGroupContent className="group-data-[collapsible=icon]:pointer-events-none group-data-[collapsible=icon]:-mt-8 group-data-[collapsible=icon]:opacity-0">
        <SidebarMenu>
          <div className="flex w-full flex-col gap-1">
            {threads.map((thread) => {
              const href = `/workspace/chats/${thread.id}`;
              const isActive = href === pathname;
              return (
                <SidebarMenuItem
                  key={thread.id}
                  className="group/side-menu-item"
                >
                  <SidebarMenuButton isActive={isActive} asChild>
                    <div>
                      <Link
                        className="text-muted-foreground block w-full whitespace-nowrap font-mono text-xs group-hover/side-menu-item:overflow-hidden"
                        href={href}
                      >
                        {thread.id}
                      </Link>
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <SidebarMenuAction
                            showOnHover
                            className="bg-background/50 hover:bg-background"
                          >
                            <MoreHorizontal />
                            <span className="sr-only">{t.common.more}</span>
                          </SidebarMenuAction>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent
                          className="w-48 rounded-lg"
                          side="right"
                          align="start"
                        >
                          <DropdownMenuItem
                            onSelect={() => handleDelete(thread.id)}
                          >
                            <Trash2 className="text-muted-foreground" />
                            <span>{t.common.delete}</span>
                          </DropdownMenuItem>
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </div>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              );
            })}
          </div>
        </SidebarMenu>
      </SidebarGroupContent>
    </SidebarGroup>
  );
}
