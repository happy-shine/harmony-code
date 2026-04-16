"use client";

import { SimpleMarkdown } from "./SimpleMarkdown";

export function TextBlock({
  text,
  streaming,
}: {
  text: string;
  streaming: boolean;
}) {
  return (
    <div className="cc-text-block">
      <SimpleMarkdown>{text}</SimpleMarkdown>
      {streaming && <span className="animate-pulse text-neutral-400">▌</span>}
    </div>
  );
}
