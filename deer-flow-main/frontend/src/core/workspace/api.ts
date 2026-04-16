// Copyright (c) 2025 Bytedance Ltd. and/or its affiliates
// SPDX-License-Identifier: Apache-2.0

import type { WorkspaceTreeResponse } from "./types";

export class WorkspaceTreeError extends Error {
  readonly status: number;
  /** Backend "detail" code when available, otherwise "". */
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "WorkspaceTreeError";
    this.status = status;
    this.code = code;
  }
}

interface ErrorBody {
  detail?: unknown;
}

/**
 * Fetch the workspace tree for a thread.
 *
 * Throws `WorkspaceTreeError` with the HTTP status and (when available)
 * the FastAPI `detail` string. 413 surfaces as code `workspace_tree_too_large`
 * so callers can render a truncation banner.
 */
export async function fetchWorkspaceTree(
  threadId: string,
  signal?: AbortSignal,
): Promise<WorkspaceTreeResponse> {
  const r = await fetch(
    `/api/threads/${encodeURIComponent(threadId)}/workspace/tree`,
    { signal },
  );
  if (!r.ok) {
    let code = "";
    try {
      const body = (await r.json()) as ErrorBody;
      if (typeof body.detail === "string") code = body.detail;
    } catch {
      // Non-JSON error body — fall through with empty code.
    }
    throw new WorkspaceTreeError(
      r.status,
      code,
      `workspace tree ${r.status}${code ? ` (${code})` : ""}`,
    );
  }
  return (await r.json()) as WorkspaceTreeResponse;
}
