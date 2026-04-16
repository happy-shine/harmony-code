// Copyright (c) 2025 Bytedance Ltd. and/or its affiliates
// SPDX-License-Identifier: Apache-2.0

/**
 * Workspace file-tree types.
 *
 * Mirror the backend contract in
 * `backend/app/gateway/routers/workspace.py`:
 *
 *   GET /api/threads/{tid}/workspace/tree
 *     → { root: string, children: WorkspaceNode[] }
 *
 *   GET /api/threads/{tid}/workspace/files/{path:path}
 *     → raw bytes with best-effort Content-Type
 *
 * `path` is always POSIX-separator relative to `root`.
 */

export type WorkspaceNodeType = "file" | "dir";

export interface WorkspaceNode {
  name: string;
  /** POSIX-separator path relative to the tree root. */
  path: string;
  type: WorkspaceNodeType;
  /** Bytes. Only present for files. */
  size?: number;
  /** Unix seconds (float). Only present for files. */
  mtime?: number;
  /** Present for directories (may be an empty array). */
  children?: WorkspaceNode[];
}

export interface WorkspaceTreeResponse {
  root: string;
  children: WorkspaceNode[];
}

/**
 * Build the URL the browser fetches to download a file. Path is
 * encoded segment-by-segment so forward slashes remain literal
 * (matching the backend's `{path:path}` matcher).
 */
export function workspaceFileUrl(threadId: string, path: string): string {
  const encoded = path
    .split("/")
    .map((seg) => encodeURIComponent(seg))
    .join("/");
  return `/api/threads/${encodeURIComponent(threadId)}/workspace/files/${encoded}`;
}
