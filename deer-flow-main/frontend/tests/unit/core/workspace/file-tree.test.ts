// Copyright (c) 2025 Bytedance Ltd. and/or its affiliates
// SPDX-License-Identifier: Apache-2.0

import { describe, expect, it } from "vitest";

import { flattenTree } from "@/components/workspace/file-browser/FileTree";
import type { WorkspaceNode } from "@/core/workspace/types";

const tree: WorkspaceNode[] = [
  {
    name: "src",
    path: "src",
    type: "dir",
    children: [
      { name: "a.ts", path: "src/a.ts", type: "file", size: 10, mtime: 1 },
      {
        name: "lib",
        path: "src/lib",
        type: "dir",
        children: [
          {
            name: "b.ts",
            path: "src/lib/b.ts",
            type: "file",
            size: 20,
            mtime: 2,
          },
        ],
      },
    ],
  },
  { name: "README.md", path: "README.md", type: "file", size: 5, mtime: 3 },
];

describe("flattenTree", () => {
  it("shows only roots when nothing is expanded", () => {
    const flat = flattenTree(tree, new Set());
    expect(flat.map((r) => r.node.path)).toEqual(["src", "README.md"]);
    expect(flat.map((r) => r.depth)).toEqual([0, 0]);
  });

  it("descends into expanded directories", () => {
    const flat = flattenTree(tree, new Set(["src"]));
    expect(flat.map((r) => r.node.path)).toEqual([
      "src",
      "src/a.ts",
      "src/lib",
      "README.md",
    ]);
    expect(flat.map((r) => r.depth)).toEqual([0, 1, 1, 0]);
  });

  it("descends recursively when nested dirs are expanded", () => {
    const flat = flattenTree(tree, new Set(["src", "src/lib"]));
    expect(flat.map((r) => r.node.path)).toEqual([
      "src",
      "src/a.ts",
      "src/lib",
      "src/lib/b.ts",
      "README.md",
    ]);
    expect(flat.map((r) => r.depth)).toEqual([0, 1, 1, 2, 0]);
  });

  it("ignores expanded entries that don't exist in the tree", () => {
    const flat = flattenTree(tree, new Set(["does-not-exist"]));
    expect(flat.map((r) => r.node.path)).toEqual(["src", "README.md"]);
  });

  it("handles a directory with no children array as a leaf", () => {
    const partial: WorkspaceNode[] = [
      { name: "empty", path: "empty", type: "dir" },
    ];
    const flat = flattenTree(partial, new Set(["empty"]));
    expect(flat.map((r) => r.node.path)).toEqual(["empty"]);
  });
});
