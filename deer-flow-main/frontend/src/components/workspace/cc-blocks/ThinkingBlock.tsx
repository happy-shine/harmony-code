"use client";

import { useState } from "react";

export function ThinkingBlock({
  text,
  streaming,
}: {
  text: string;
  streaming: boolean;
}) {
  const [open, setOpen] = useState(false);
  return (
    <details
      open={open}
      onToggle={(e) =>
        setOpen((e.currentTarget as HTMLDetailsElement).open)
      }
      className="rounded border border-neutral-700/30 bg-neutral-100 dark:bg-neutral-900 p-2 my-2"
    >
      <summary className="cursor-pointer text-xs text-neutral-500">
        thinking {streaming && "..."}
      </summary>
      <pre className="whitespace-pre-wrap text-xs text-neutral-600 dark:text-neutral-400 mt-1">
        {text}
      </pre>
    </details>
  );
}
