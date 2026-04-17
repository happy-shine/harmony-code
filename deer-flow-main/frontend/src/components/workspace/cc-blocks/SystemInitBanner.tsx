"use client";

import { ChevronRightIcon } from "lucide-react";
import { useState } from "react";

/**
 * Compact turn-header that collapses session/cwd/MCP detail by default.
 *
 * The previous design spammed the transcript with every tool name and
 * MCP status on every assistant turn, which visually drowned the real
 * conversation. This version shows a single unobtrusive line
 * ("claude-opus-4-7 · 55 tools · 2/5 mcp") and reveals the rest only
 * when the user expands it — same information density, vastly calmer
 * chat surface.
 */
export function SystemInitBanner({
  sessionId,
  model,
  cwd,
  tools,
  mcpServers,
}: {
  sessionId: string;
  model: string;
  cwd: string;
  tools: string[];
  mcpServers: Array<{ name: string; status: string }>;
}) {
  const [open, setOpen] = useState(false);
  const connectedMcp = mcpServers.filter((m) => m.status === "connected").length;
  const cwdShort = cwd.split("/").slice(-2).join("/");

  return (
    <div className="my-2 text-xs text-neutral-500 dark:text-neutral-400">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="group inline-flex items-center gap-1.5 rounded px-1.5 py-0.5 hover:bg-neutral-100 dark:hover:bg-neutral-800"
        aria-expanded={open}
      >
        <ChevronRightIcon
          size={12}
          className={
            "transition-transform " + (open ? "rotate-90" : "")
          }
        />
        <span className="font-mono text-[11px]">{model}</span>
        <span className="text-neutral-400 dark:text-neutral-600">·</span>
        <span>{tools.length} tools</span>
        {mcpServers.length > 0 && (
          <>
            <span className="text-neutral-400 dark:text-neutral-600">·</span>
            <span>
              {connectedMcp}/{mcpServers.length} mcp
            </span>
          </>
        )}
      </button>
      {open && (
        <div className="mt-1 ml-5 flex flex-col gap-1 rounded border border-neutral-200 bg-neutral-50 px-2 py-1.5 dark:border-neutral-800 dark:bg-neutral-900">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <DetailPair label="session" value={sessionId.slice(0, 8)} />
            <DetailPair label="cwd" value={cwdShort} fullValue={cwd} />
          </div>
          {mcpServers.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {mcpServers.map((m) => (
                <McpChip key={m.name} name={m.name} status={m.status} />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function DetailPair({
  label,
  value,
  fullValue,
}: {
  label: string;
  value: string;
  fullValue?: string;
}) {
  return (
    <span>
      <span className="text-neutral-400 dark:text-neutral-600">{label}</span>{" "}
      <code
        className="rounded bg-neutral-200 px-1 dark:bg-neutral-800"
        title={fullValue ?? value}
      >
        {value}
      </code>
    </span>
  );
}

function McpChip({ name, status }: { name: string; status: string }) {
  const ok = status === "connected";
  return (
    <span
      title={status}
      className={
        "inline-flex items-center gap-1 rounded px-1.5 py-0.5 " +
        (ok
          ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-400"
          : "bg-neutral-100 text-neutral-500 dark:bg-neutral-800 dark:text-neutral-500")
      }
    >
      <span
        className={
          "size-1.5 rounded-full " +
          (ok ? "bg-emerald-500" : "bg-neutral-400")
        }
      />
      {name}
    </span>
  );
}
