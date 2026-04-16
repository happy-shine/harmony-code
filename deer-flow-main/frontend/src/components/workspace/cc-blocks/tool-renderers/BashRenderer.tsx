"use client";

export function BashRenderer({
  input,
  result,
}: {
  input: unknown;
  result: unknown;
  status: string;
}) {
  const inp = input as { command?: string } | null;
  return (
    <div className="mt-1 text-xs font-mono">
      {inp?.command && (
        <div className="bg-neutral-800 text-neutral-100 p-2 rounded">
          <span className="text-green-400">$</span> {inp.command}
        </div>
      )}
      {result != null && (
        <pre className="bg-neutral-900 text-neutral-100 p-2 rounded mt-1 whitespace-pre-wrap max-h-96 overflow-auto">
          {typeof result === "string"
            ? result
            : JSON.stringify(result, null, 2)}
        </pre>
      )}
    </div>
  );
}
