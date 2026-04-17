"use client";

import { CheckCircle2, ChevronRight, LoaderIcon, XCircle } from "lucide-react";
import { useState } from "react";

import type { UIBlock } from "@/core/messages/cc-reducer";

import {
  ReadRenderer,
  WriteRenderer,
  EditRenderer,
  BashRenderer,
  GlobGrepRenderer,
  WebFetchRenderer,
  DefaultMcpRenderer,
} from "./tool-renderers";

type ToolUseBlockType = Extract<UIBlock, { kind: "tool_use" }>;

type RendererProps = { input: unknown; result: unknown; status: string };

const rendererMap: Record<string, React.FC<RendererProps>> = {
  Read: ReadRenderer,
  Write: WriteRenderer,
  Edit: EditRenderer,
  Bash: BashRenderer,
  Glob: GlobGrepRenderer,
  Grep: GlobGrepRenderer,
  WebFetch: WebFetchRenderer,
  WebSearch: WebFetchRenderer,
};

// ---------------------------------------------------------------------------
// Title / subtitle extraction — best-effort per tool. Keeps the card's
// headline concise even when the raw input is a nested object. Default is
// the tool name itself, which at least tells the user *what* was called.
// ---------------------------------------------------------------------------

function basename(p: string): string {
  const idx = Math.max(p.lastIndexOf("/"), p.lastIndexOf("\\"));
  return idx >= 0 ? p.slice(idx + 1) : p;
}

function firstLine(s: string, max = 80): string {
  const line = s.split("\n", 1)[0]?.trim() ?? "";
  return line.length > max ? line.slice(0, max) + "…" : line;
}

function titleForTool(name: string, input: unknown): string {
  const inp = (input ?? {}) as Record<string, unknown>;
  switch (name) {
    case "Bash": {
      const desc = inp.description;
      const cmd = inp.command;
      if (typeof desc === "string" && desc.trim()) return desc;
      if (typeof cmd === "string" && cmd.trim()) return firstLine(cmd);
      return "Bash";
    }
    case "Read":
    case "Write":
    case "Edit": {
      const fp = inp.file_path;
      if (typeof fp === "string" && fp) return basename(fp);
      return name;
    }
    case "Glob":
    case "Grep": {
      const pat = inp.pattern;
      return typeof pat === "string" && pat ? pat : name;
    }
    case "WebFetch":
    case "WebSearch": {
      const url = inp.url ?? inp.query;
      return typeof url === "string" && url ? url : name;
    }
    case "Task": {
      const st = inp.subagent_type;
      const prompt = inp.prompt;
      if (typeof st === "string" && st) return st;
      if (typeof prompt === "string" && prompt) return firstLine(prompt);
      return "Task";
    }
    case "Skill": {
      const skill = inp.skill;
      return typeof skill === "string" && skill ? skill : "Skill";
    }
    default: {
      // Fallback: a short, friendly-looking input summary. For single-key
      // objects like {query: "..."} we show the value; for anything else
      // we just use the tool name.
      const keys = Object.keys(inp);
      if (keys.length === 1) {
        const v = inp[keys[0]!];
        if (typeof v === "string" && v.trim()) return firstLine(v);
      }
      return name;
    }
  }
}

function statusLabel(status: string): string {
  if (status === "ok") return "Completed";
  if (status === "error") return "Failed";
  return "Running…";
}

function StatusIcon({ status }: { status: string }) {
  if (status === "ok")
    return <CheckCircle2 className="size-4 text-neutral-400" strokeWidth={1.5} />;
  if (status === "error")
    return <XCircle className="size-4 text-red-500" strokeWidth={1.5} />;
  return (
    <LoaderIcon
      className="size-4 animate-spin text-amber-500"
      strokeWidth={1.5}
    />
  );
}

// Keep MCP tools tagged with their source (e.g. "mcp:github") so the
// right-pill gives users enough context to know where the call came from.
function kindLabel(name: string): string {
  if (name.startsWith("mcp__")) {
    // ``mcp__<server>__<tool>`` → "mcp:<server>"
    const parts = name.split("__");
    return parts.length >= 2 ? `mcp:${parts[1]}` : "MCP";
  }
  return name;
}

export function ToolUseBlock({ block }: { block: ToolUseBlockType }) {
  const [open, setOpen] = useState(false);
  const Renderer = rendererMap[block.name] ?? DefaultMcpRenderer;
  const title = titleForTool(block.name, block.input);
  const status = block.status;
  const isError = status === "error";

  return (
    <div
      className={
        "my-1.5 overflow-hidden rounded-lg border text-sm transition-colors " +
        (isError
          ? "border-red-200 bg-red-50 dark:border-red-900/40 dark:bg-red-950/20"
          : "border-neutral-200 bg-neutral-50 dark:border-neutral-800 dark:bg-neutral-900/40")
      }
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-3 px-3 py-2 text-left hover:bg-neutral-100/60 dark:hover:bg-neutral-800/40"
        aria-expanded={open}
      >
        <ChevronRight
          size={14}
          className={
            "shrink-0 text-neutral-400 transition-transform " +
            (open ? "rotate-90" : "")
          }
        />
        <StatusIcon status={status} />
        <div className="min-w-0 flex-1">
          <div className="truncate font-medium text-neutral-800 dark:text-neutral-100">
            {title}
          </div>
          <div className="truncate text-xs text-neutral-500 dark:text-neutral-400">
            {statusLabel(status)}
          </div>
        </div>
        <span className="shrink-0 rounded-md bg-neutral-200/70 px-2 py-0.5 font-mono text-xs text-neutral-600 dark:bg-neutral-800 dark:text-neutral-300">
          {kindLabel(block.name)}
        </span>
      </button>
      {open && (
        <div className="border-t border-neutral-200 bg-white px-3 py-2 dark:border-neutral-800 dark:bg-neutral-950">
          <Renderer
            input={block.input}
            result={block.result}
            status={status}
          />
        </div>
      )}
    </div>
  );
}
