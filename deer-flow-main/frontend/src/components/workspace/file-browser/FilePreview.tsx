"use client";

// Copyright (c) 2025 Bytedance Ltd. and/or its affiliates
// SPDX-License-Identifier: Apache-2.0

import { useEffect, useState } from "react";
import type { BundledLanguage } from "shiki";

import {
  CodeBlock,
  CodeBlockCopyButton,
} from "@/components/ai-elements/code-block";
import { SimpleMarkdown } from "@/components/workspace/cc-blocks/SimpleMarkdown";
import { workspaceFileUrl } from "@/core/workspace/types";
import { cn } from "@/lib/utils";

export interface FilePreviewProps {
  threadId: string;
  path: string;
  size?: number;
  className?: string;
}

/** Skip inline preview above this content-length (bytes). */
const INLINE_PREVIEW_MAX_BYTES = 1024 * 1024; // 1 MB

const IMAGE_EXTS = new Set([
  "png",
  "jpg",
  "jpeg",
  "gif",
  "webp",
  "svg",
  "avif",
  "ico",
  "bmp",
]);

const MARKDOWN_EXTS = new Set(["md", "mdx", "markdown"]);

/**
 * Map file extension → shiki BundledLanguage. Anything unmapped falls
 * through to `null` and is rendered as plain `<pre>`.
 *
 * Kept narrow on purpose: shiki ships hundreds of grammars but we only
 * care about what CC working directories typically produce.
 */
const LANG_BY_EXT: Record<string, BundledLanguage> = {
  ts: "ts",
  tsx: "tsx",
  js: "js",
  jsx: "jsx",
  mjs: "js",
  cjs: "js",
  py: "python",
  json: "json",
  jsonc: "json",
  yaml: "yaml",
  yml: "yaml",
  toml: "toml",
  sh: "bash",
  bash: "bash",
  zsh: "bash",
  rs: "rust",
  go: "go",
  java: "java",
  kt: "kotlin",
  c: "c",
  h: "c",
  cpp: "cpp",
  hpp: "cpp",
  cc: "cpp",
  css: "css",
  scss: "scss",
  html: "html",
  xml: "xml",
  sql: "sql",
  rb: "ruby",
  php: "php",
  lua: "lua",
  swift: "swift",
  dockerfile: "docker",
  makefile: "makefile",
};

function extOf(path: string): string {
  const slash = path.lastIndexOf("/");
  const name = slash >= 0 ? path.slice(slash + 1) : path;
  const dot = name.lastIndexOf(".");
  if (dot <= 0) return name.toLowerCase(); // handle `Dockerfile`, `Makefile`
  return name.slice(dot + 1).toLowerCase();
}

function baseNameOf(path: string): string {
  const slash = path.lastIndexOf("/");
  return slash >= 0 ? path.slice(slash + 1) : path;
}

function kindOf(path: string): "image" | "markdown" | "code" | "text" {
  const ext = extOf(path);
  if (IMAGE_EXTS.has(ext)) return "image";
  if (MARKDOWN_EXTS.has(ext)) return "markdown";
  if (LANG_BY_EXT[ext]) return "code";
  // Handle extensionless well-known names.
  const lower = baseNameOf(path).toLowerCase();
  if (lower === "dockerfile" || lower === "makefile") return "code";
  return "text";
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function FilePreview({
  threadId,
  path,
  size,
  className,
}: FilePreviewProps) {
  const kind = kindOf(path);
  const fileUrl = workspaceFileUrl(threadId, path);
  const tooLarge = typeof size === "number" && size > INLINE_PREVIEW_MAX_BYTES;

  return (
    <div className={cn("flex h-full flex-col", className)}>
      <header className="flex items-center justify-between gap-2 border-b border-neutral-200 px-3 py-2 dark:border-neutral-800">
        <div className="min-w-0">
          <div className="truncate text-sm font-medium" title={path}>
            {baseNameOf(path)}
          </div>
          <div className="truncate text-xs text-neutral-500" title={path}>
            {path}
          </div>
        </div>
        <a
          href={fileUrl}
          download={baseNameOf(path)}
          className="shrink-0 rounded border border-neutral-300 px-2 py-1 text-xs hover:bg-neutral-100 dark:border-neutral-700 dark:hover:bg-neutral-800"
        >
          Download{typeof size === "number" ? ` (${formatSize(size)})` : ""}
        </a>
      </header>

      <div className="flex-1 overflow-auto">
        {tooLarge ? (
          <BinaryFallback path={path} size={size} url={fileUrl} reason="large" />
        ) : kind === "image" ? (
          <ImagePreview url={fileUrl} alt={baseNameOf(path)} />
        ) : kind === "markdown" ? (
          <TextFetchPreview
            url={fileUrl}
            path={path}
            renderer="markdown"
          />
        ) : kind === "code" ? (
          <TextFetchPreview url={fileUrl} path={path} renderer="code" />
        ) : (
          <TextFetchPreview url={fileUrl} path={path} renderer="plain" />
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sub-renderers
// ---------------------------------------------------------------------------

function ImagePreview({ url, alt }: { url: string; alt: string }) {
  return (
    <div className="flex h-full items-center justify-center p-4">
      { }
      <img
        src={url}
        alt={alt}
        className="max-h-full max-w-full object-contain"
      />
    </div>
  );
}

function BinaryFallback({
  path,
  size,
  url,
  reason,
}: {
  path: string;
  size?: number;
  url: string;
  reason: "large" | "binary" | "error";
}) {
  const message =
    reason === "large"
      ? "File is larger than 1 MB. Download it to view."
      : reason === "error"
        ? "Preview unavailable."
        : "Binary file. Download to view.";
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 p-6 text-center text-sm text-neutral-500">
      <div>{message}</div>
      <div className="text-xs">
        {baseNameOf(path)}
        {typeof size === "number" ? ` — ${formatSize(size)}` : ""}
      </div>
      <a
        href={url}
        download={baseNameOf(path)}
        className="rounded border border-neutral-300 px-3 py-1 text-xs hover:bg-neutral-100 dark:border-neutral-700 dark:hover:bg-neutral-800"
      >
        Download
      </a>
    </div>
  );
}

type Renderer = "markdown" | "code" | "plain";

function TextFetchPreview({
  url,
  path,
  renderer,
}: {
  url: string;
  path: string;
  renderer: Renderer;
}) {
  const [state, setState] = useState<
    | { status: "loading" }
    | { status: "ok"; text: string }
    | { status: "binary" }
    | { status: "error"; message: string }
  >({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    setState({ status: "loading" });
    void (async () => {
      try {
        const r = await fetch(url);
        if (!r.ok) {
          throw new Error(`HTTP ${r.status}`);
        }
        const buf = await r.arrayBuffer();
        if (cancelled) return;
        // Best-effort binary sniff: a NUL byte in the first 4 KB is a
        // reliable signal that decoding as UTF-8 will be garbage.
        const sample = new Uint8Array(buf.slice(0, 4096));
        if (sample.some((b) => b === 0)) {
          setState({ status: "binary" });
          return;
        }
        const text = new TextDecoder("utf-8", { fatal: false }).decode(buf);
        setState({ status: "ok", text });
      } catch (e: unknown) {
        if (cancelled) return;
        setState({
          status: "error",
          message: e instanceof Error ? e.message : String(e),
        });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [url]);

  if (state.status === "loading") {
    return (
      <div className="p-4 text-xs text-neutral-500">Loading preview...</div>
    );
  }
  if (state.status === "binary") {
    return <BinaryFallback path={path} url={url} reason="binary" />;
  }
  if (state.status === "error") {
    return (
      <div className="p-4 text-xs text-red-500">
        Preview failed: {state.message}
      </div>
    );
  }

  if (renderer === "markdown") {
    return (
      <div className="prose prose-sm dark:prose-invert max-w-none p-4">
        <SimpleMarkdown>{state.text}</SimpleMarkdown>
      </div>
    );
  }
  if (renderer === "code") {
    const ext = extOf(path);
    const lang = LANG_BY_EXT[ext] ?? ("text" as BundledLanguage);
    return (
      <div className="p-3">
        <CodeBlock code={state.text} language={lang} showLineNumbers>
          <CodeBlockCopyButton />
        </CodeBlock>
      </div>
    );
  }
  return (
    <pre className="max-w-none whitespace-pre-wrap break-words p-4 font-mono text-xs">
      {state.text}
    </pre>
  );
}
