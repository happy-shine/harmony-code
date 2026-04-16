"use client";

// Copyright (c) 2025 Bytedance Ltd. and/or its affiliates
// SPDX-License-Identifier: Apache-2.0

import {
  ArrowLeftIcon,
  FolderIcon,
  RefreshCcwIcon,
} from "lucide-react";
import { useCallback, useMemo, useState } from "react";

import { WorkspaceTreeError } from "@/core/workspace/api";
import type { WorkspaceNode } from "@/core/workspace/types";
import { useWorkspaceTree } from "@/core/workspace/use-workspace-tree";
import { cn } from "@/lib/utils";

import { FilePreview } from "./FilePreview";
import { FileTree } from "./FileTree";

export interface FileBrowserProps {
  threadId: string;
  className?: string;
}

/**
 * Depth-first lookup for the selected node so the preview pane can
 * show `size` without a second round-trip.
 */
function findNode(
  nodes: WorkspaceNode[],
  path: string,
): WorkspaceNode | undefined {
  for (const n of nodes) {
    if (n.path === path) return n;
    if (n.children) {
      const hit = findNode(n.children, path);
      if (hit) return hit;
    }
  }
  return undefined;
}

export function FileBrowser({ threadId, className }: FileBrowserProps) {
  const { data, error, loading, refresh } = useWorkspaceTree(threadId);
  const [selected, setSelected] = useState<string | undefined>();
  const [narrowShowPreview, setNarrowShowPreview] = useState(false);

  const nodes = useMemo<WorkspaceNode[]>(
    () => data?.children ?? [],
    [data],
  );
  const selectedNode = useMemo(
    () => (selected ? findNode(nodes, selected) : undefined),
    [nodes, selected],
  );

  const handleSelect = useCallback((path: string) => {
    setSelected(path);
    setNarrowShowPreview(true);
  }, []);

  const truncated =
    error instanceof WorkspaceTreeError && error.status === 413;

  return (
    <div
      className={cn(
        "flex h-full w-full overflow-hidden border-l border-neutral-200 dark:border-neutral-800",
        className,
      )}
    >
      {/* Left pane: tree */}
      <aside
        className={cn(
          "flex h-full w-full flex-col overflow-hidden md:w-72 md:shrink-0 md:border-r md:border-neutral-200 md:dark:border-neutral-800",
          narrowShowPreview && selected ? "hidden md:flex" : "flex",
        )}
      >
        <header className="flex items-center justify-between gap-2 border-b border-neutral-200 px-3 py-2 dark:border-neutral-800">
          <div className="flex min-w-0 items-center gap-1">
            <FolderIcon size={14} className="text-neutral-500" />
            <span className="truncate text-xs font-medium" title={data?.root}>
              {data?.root ?? "Workspace"}
            </span>
          </div>
          <button
            type="button"
            onClick={refresh}
            disabled={loading}
            aria-label="Refresh"
            className="rounded p-1 text-neutral-500 hover:bg-neutral-100 disabled:opacity-50 dark:hover:bg-neutral-800"
          >
            <RefreshCcwIcon
              size={14}
              className={loading ? "animate-spin" : undefined}
            />
          </button>
        </header>

        {truncated && (
          <div className="border-b border-amber-300 bg-amber-50 px-3 py-1 text-xs text-amber-900 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-200">
            Workspace tree truncated — too many files. Narrow the working
            directory or download files directly.
          </div>
        )}

        <div className="flex-1 overflow-auto">
          {loading && !data && (
            <div className="px-3 py-2 text-xs text-neutral-500">Loading...</div>
          )}
          {error && !truncated && (
            <div className="px-3 py-2 text-xs text-red-500">
              Failed to load: {error.message}
            </div>
          )}
          {data && (
            <FileTree
              nodes={nodes}
              onSelect={handleSelect}
              selectedPath={selected}
            />
          )}
        </div>
      </aside>

      {/* Right pane: preview */}
      <section
        className={cn(
          "h-full min-w-0 flex-1 overflow-hidden",
          narrowShowPreview && selected ? "flex" : "hidden md:flex",
          "flex-col",
        )}
      >
        {selected && selectedNode ? (
          <>
            <div className="md:hidden">
              <button
                type="button"
                onClick={() => setNarrowShowPreview(false)}
                className="flex items-center gap-1 border-b border-neutral-200 px-3 py-2 text-xs text-neutral-600 hover:bg-neutral-100 dark:border-neutral-800 dark:text-neutral-300 dark:hover:bg-neutral-800"
              >
                <ArrowLeftIcon size={14} />
                Back
              </button>
            </div>
            <FilePreview
              threadId={threadId}
              path={selectedNode.path}
              size={selectedNode.size}
            />
          </>
        ) : (
          <div className="flex h-full items-center justify-center p-6 text-xs text-neutral-500">
            Select a file to preview.
          </div>
        )}
      </section>
    </div>
  );
}
