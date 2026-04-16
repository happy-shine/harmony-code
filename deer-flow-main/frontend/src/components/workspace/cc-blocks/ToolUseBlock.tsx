"use client";

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

export function ToolUseBlock({ block }: { block: ToolUseBlockType }) {
  const Renderer = rendererMap[block.name] ?? DefaultMcpRenderer;
  return (
    <div className="rounded border my-2 p-2 bg-neutral-50 dark:bg-neutral-900">
      <header className="flex items-center gap-2 text-xs">
        <span className="font-mono font-medium">{block.name}</span>
        <StatusDot status={block.status} />
      </header>
      <Renderer input={block.input} result={block.result} status={block.status} />
    </div>
  );
}

function StatusDot({ status }: { status: string }) {
  const color =
    status === "ok"
      ? "bg-green-500"
      : status === "error"
        ? "bg-red-500"
        : "bg-amber-500 animate-pulse";
  return <span className={`inline-block w-2 h-2 rounded-full ${color}`} />;
}
