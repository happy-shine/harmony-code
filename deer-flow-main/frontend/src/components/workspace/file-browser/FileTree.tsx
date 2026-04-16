"use client";

// Copyright (c) 2025 Bytedance Ltd. and/or its affiliates
// SPDX-License-Identifier: Apache-2.0

import { ChevronDownIcon, ChevronRightIcon } from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";

import type { WorkspaceNode } from "@/core/workspace/types";
import { cn } from "@/lib/utils";

export interface FileTreeProps {
  nodes: WorkspaceNode[];
  onSelect: (path: string) => void;
  selectedPath?: string;
  className?: string;
}

/**
 * Pre-order flattening of the visible tree. Collapsed directories
 * contribute themselves but not their children. The result drives
 * keyboard navigation (up/down between visible rows).
 */
export interface FlatRow {
  node: WorkspaceNode;
  depth: number;
}

export function flattenTree(
  nodes: WorkspaceNode[],
  expanded: ReadonlySet<string>,
  depth = 0,
  out: FlatRow[] = [],
): FlatRow[] {
  for (const node of nodes) {
    out.push({ node, depth });
    if (node.type === "dir" && expanded.has(node.path) && node.children) {
      flattenTree(node.children, expanded, depth + 1, out);
    }
  }
  return out;
}

export function FileTree({
  nodes,
  onSelect,
  selectedPath,
  className,
}: FileTreeProps) {
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const [focusedPath, setFocusedPath] = useState<string | undefined>(
    selectedPath,
  );
  const listRef = useRef<HTMLUListElement>(null);

  const flat = useMemo(() => flattenTree(nodes, expanded), [nodes, expanded]);

  // Keep focus synced with external selection changes (e.g. after click).
  useEffect(() => {
    if (selectedPath) setFocusedPath(selectedPath);
  }, [selectedPath]);

  const toggle = useCallback((path: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }, []);

  const activate = useCallback(
    (node: WorkspaceNode) => {
      if (node.type === "dir") toggle(node.path);
      else onSelect(node.path);
    },
    [onSelect, toggle],
  );

  const moveFocus = useCallback(
    (delta: number) => {
      if (flat.length === 0) return;
      const idx = flat.findIndex((r) => r.node.path === focusedPath);
      const next = idx < 0 ? 0 : Math.max(0, Math.min(flat.length - 1, idx + delta));
      const row = flat[next];
      if (row) setFocusedPath(row.node.path);
    },
    [flat, focusedPath],
  );

  const handleKey = useCallback(
    (e: KeyboardEvent<HTMLUListElement>) => {
      const current = flat.find((r) => r.node.path === focusedPath);
      switch (e.key) {
        case "ArrowDown":
          e.preventDefault();
          moveFocus(1);
          return;
        case "ArrowUp":
          e.preventDefault();
          moveFocus(-1);
          return;
        case "ArrowRight":
          if (!current) return;
          e.preventDefault();
          if (current.node.type === "dir" && !expanded.has(current.node.path)) {
            toggle(current.node.path);
          } else {
            moveFocus(1);
          }
          return;
        case "ArrowLeft":
          if (!current) return;
          e.preventDefault();
          if (current.node.type === "dir" && expanded.has(current.node.path)) {
            toggle(current.node.path);
          } else {
            // Jump to parent directory in the flat list.
            const parentPath = current.node.path.includes("/")
              ? current.node.path.slice(0, current.node.path.lastIndexOf("/"))
              : undefined;
            if (parentPath) setFocusedPath(parentPath);
          }
          return;
        case "Enter":
        case " ":
          if (!current) return;
          e.preventDefault();
          activate(current.node);
          return;
        default:
          return;
      }
    },
    [flat, focusedPath, expanded, moveFocus, toggle, activate],
  );

  if (nodes.length === 0) {
    return (
      <div
        className={cn(
          "px-3 py-2 text-xs text-neutral-500 dark:text-neutral-400",
          className,
        )}
      >
        Workspace is empty.
      </div>
    );
  }

  return (
    <ul
      ref={listRef}
      role="tree"
      aria-label="Workspace files"
      tabIndex={0}
      onKeyDown={handleKey}
      className={cn(
        "select-none py-1 text-sm outline-none focus:ring-1 focus:ring-blue-500/40",
        className,
      )}
    >
      {flat.map((row) => (
        <TreeRow
          key={row.node.path}
          node={row.node}
          depth={row.depth}
          expanded={expanded.has(row.node.path)}
          selected={selectedPath === row.node.path}
          focused={focusedPath === row.node.path}
          onToggle={toggle}
          onSelect={onSelect}
          onFocus={setFocusedPath}
        />
      ))}
    </ul>
  );
}

interface TreeRowProps {
  node: WorkspaceNode;
  depth: number;
  expanded: boolean;
  selected: boolean;
  focused: boolean;
  onToggle: (path: string) => void;
  onSelect: (path: string) => void;
  onFocus: (path: string) => void;
}

function TreeRow({
  node,
  depth,
  expanded,
  selected,
  focused,
  onToggle,
  onSelect,
  onFocus,
}: TreeRowProps) {
  const isDir = node.type === "dir";
  const paddingLeft = 8 + depth * 14;

  const handleClick = useCallback(() => {
    onFocus(node.path);
    if (isDir) onToggle(node.path);
    else onSelect(node.path);
  }, [isDir, node.path, onFocus, onSelect, onToggle]);

  return (
    <li
      role="treeitem"
      aria-level={depth + 1}
      aria-expanded={isDir ? expanded : undefined}
      aria-selected={selected || focused}
      data-path={node.path}
      className={cn(
        "flex cursor-pointer items-center gap-1 rounded px-1 py-0.5",
        "hover:bg-neutral-100 dark:hover:bg-neutral-800",
        selected && "bg-blue-100 text-blue-900 dark:bg-blue-950 dark:text-blue-100",
        !selected && focused && "bg-neutral-100 dark:bg-neutral-800",
      )}
      style={{ paddingLeft }}
      onClick={handleClick}
    >
      <span className="flex h-4 w-4 shrink-0 items-center justify-center text-neutral-400">
        {isDir ? (
          expanded ? (
            <ChevronDownIcon size={14} />
          ) : (
            <ChevronRightIcon size={14} />
          )
        ) : null}
      </span>
      <span className="truncate" title={node.path}>
        {node.name}
      </span>
    </li>
  );
}
