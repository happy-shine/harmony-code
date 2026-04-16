"use client";

export function WebFetchRenderer({
  input,
  result,
}: {
  input: unknown;
  result: unknown;
  status: string;
}) {
  const inp = input as { url?: string; query?: string } | null;
  return (
    <div className="mt-1 text-xs">
      {inp?.url && (
        <a
          href={inp.url}
          target="_blank"
          rel="noopener noreferrer"
          className="text-blue-500 hover:underline truncate block"
        >
          {inp.url}
        </a>
      )}
      {inp?.query && <div className="text-neutral-500">{inp.query}</div>}
      {result != null && (
        <pre className="bg-neutral-900 text-neutral-100 p-2 rounded mt-1 whitespace-pre-wrap max-h-64 overflow-auto font-mono">
          {typeof result === "string"
            ? result.slice(0, 2000)
            : JSON.stringify(result, null, 2)}
        </pre>
      )}
    </div>
  );
}
