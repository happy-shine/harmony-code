"use client";

import type { UIBlock } from "@/core/messages/cc-reducer";

type ToolUseBlockType = Extract<UIBlock, { kind: "tool_use" }>;

export function ToolUseBlock({ block }: { block: ToolUseBlockType }) {
  return (
    <div className="rounded border my-2 p-2 bg-neutral-50 dark:bg-neutral-900">
      <header className="flex items-center gap-2 text-xs">
        <span className="font-mono font-medium">{block.name}</span>
        <StatusDot status={block.status} />
      </header>
      <div className="mt-1 text-xs">
        <details>
          <summary className="cursor-pointer text-neutral-500">Input</summary>
          <pre className="whitespace-pre-wrap text-xs text-neutral-600 dark:text-neutral-400 mt-1 max-h-48 overflow-auto">
            {JSON.stringify(block.input, null, 2)}
          </pre>
        </details>
        {block.result != null && (
          <details open>
            <summary className="cursor-pointer text-neutral-500 mt-1">
              Result
            </summary>
            <pre className="whitespace-pre-wrap text-xs text-neutral-600 dark:text-neutral-400 mt-1 max-h-96 overflow-auto">
              {typeof block.result === "string"
                ? block.result
                : JSON.stringify(block.result, null, 2)}
            </pre>
          </details>
        )}
      </div>
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
