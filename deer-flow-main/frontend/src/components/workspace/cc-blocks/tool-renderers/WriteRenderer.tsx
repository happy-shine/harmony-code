"use client";

export function WriteRenderer({
  input,
  result,
}: {
  input: unknown;
  result: unknown;
  status: string;
}) {
  const inp = input as { file_path?: string; content?: string } | null;
  return (
    <div className="mt-1 text-xs font-mono">
      {inp?.file_path && (
        <div className="text-neutral-500 truncate" title={inp.file_path}>
          {inp.file_path}
        </div>
      )}
      {inp?.content != null && (
        <details>
          <summary className="cursor-pointer text-neutral-500">
            Content ({inp.content.length} chars)
          </summary>
          <pre className="bg-neutral-900 text-neutral-100 p-2 rounded mt-1 whitespace-pre-wrap max-h-64 overflow-auto">
            {inp.content}
          </pre>
        </details>
      )}
      {result != null && typeof result === "string" && result.length > 0 && (
        <div className="text-green-600 dark:text-green-400 mt-1">{result}</div>
      )}
    </div>
  );
}
