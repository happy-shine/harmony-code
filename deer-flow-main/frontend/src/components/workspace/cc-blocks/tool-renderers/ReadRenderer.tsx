"use client";

export function ReadRenderer({
  input,
  result,
}: {
  input: unknown;
  result: unknown;
  status: string;
}) {
  const inp = input as { file_path?: string } | null;
  return (
    <div className="mt-1 text-xs font-mono">
      {inp?.file_path && (
        <div className="text-neutral-500 truncate" title={inp.file_path}>
          {inp.file_path}
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
