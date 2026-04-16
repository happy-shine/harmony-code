// Copyright (c) 2025 Bytedance Ltd. and/or its affiliates
// SPDX-License-Identifier: Apache-2.0

import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchWorkspaceTree, WorkspaceTreeError } from "@/core/workspace/api";
import { workspaceFileUrl } from "@/core/workspace/types";

describe("fetchWorkspaceTree", () => {
  const originalFetch = globalThis.fetch;

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it("returns parsed JSON on success", async () => {
    const body = {
      root: "/tmp/w",
      children: [
        { name: "a.md", path: "a.md", type: "file", size: 3, mtime: 1 },
        { name: "dir", path: "dir", type: "dir", children: [] },
      ],
    };
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => body,
    });

    const resp = await fetchWorkspaceTree("t1");
    expect(resp).toEqual(body);
  });

  it("encodes the thread id into the URL", async () => {
    const spy = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ root: "/", children: [] }),
    });
    globalThis.fetch = spy;

    await fetchWorkspaceTree("abc def/xyz");
    expect(spy).toHaveBeenCalledWith(
      "/api/threads/abc%20def%2Fxyz/workspace/tree",
      expect.any(Object),
    );
  });

  it("throws WorkspaceTreeError with detail on 413", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 413,
      json: async () => ({ detail: "workspace_tree_too_large" }),
    });

    const err = await fetchWorkspaceTree("t1").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(WorkspaceTreeError);
    expect((err as WorkspaceTreeError).status).toBe(413);
    expect((err as WorkspaceTreeError).code).toBe("workspace_tree_too_large");
  });

  it("throws WorkspaceTreeError with empty code when body is non-JSON", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      json: async () => {
        throw new Error("not json");
      },
    });

    const err = await fetchWorkspaceTree("t1").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(WorkspaceTreeError);
    expect((err as WorkspaceTreeError).status).toBe(500);
    expect((err as WorkspaceTreeError).code).toBe("");
  });
});

describe("workspaceFileUrl", () => {
  it("encodes each path segment but preserves forward slashes", () => {
    expect(workspaceFileUrl("t1", "dir/sub dir/file.md")).toBe(
      "/api/threads/t1/workspace/files/dir/sub%20dir/file.md",
    );
  });

  it("encodes the thread id", () => {
    expect(workspaceFileUrl("t/1", "a.md")).toBe(
      "/api/threads/t%2F1/workspace/files/a.md",
    );
  });

  it("handles single-segment paths", () => {
    expect(workspaceFileUrl("t1", "a.md")).toBe(
      "/api/threads/t1/workspace/files/a.md",
    );
  });
});
