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
  return (
    <div className="text-xs text-neutral-500 border-b border-neutral-200 dark:border-neutral-800 py-1 px-2 my-2 flex gap-3 flex-wrap">
      <span>
        session{" "}
        <code className="bg-neutral-100 dark:bg-neutral-800 px-1 rounded">
          {sessionId.slice(0, 8)}
        </code>
      </span>
      <span>
        model{" "}
        <code className="bg-neutral-100 dark:bg-neutral-800 px-1 rounded">
          {model}
        </code>
      </span>
      <span>
        cwd{" "}
        <code
          className="bg-neutral-100 dark:bg-neutral-800 px-1 rounded"
          title={cwd}
        >
          {cwd.split("/").slice(-2).join("/")}
        </code>
      </span>
      <span>{tools.length} tools</span>
      {mcpServers.map((m) => (
        <span key={m.name}>
          mcp:{m.name} {m.status === "connected" ? "\u2713" : "\u2717"}
        </span>
      ))}
    </div>
  );
}
