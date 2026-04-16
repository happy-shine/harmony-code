"use client";

export function EditRenderer({
  input,
  result,
}: {
  input: unknown;
  result: unknown;
  status: string;
}) {
  const inp = input as {
    file_path?: string;
    old_string?: string;
    new_string?: string;
  } | null;
  return (
    <div className="mt-1 text-xs font-mono">
      {inp?.file_path && (
        <div className="text-neutral-500 truncate" title={inp.file_path}>
          {inp.file_path}
        </div>
      )}
      {inp?.old_string != null && (
        <pre className="bg-red-950/30 text-red-300 p-1 rounded mt-1 whitespace-pre-wrap max-h-32 overflow-auto border-l-2 border-red-500">
          {inp.old_string}
        </pre>
      )}
      {inp?.new_string != null && (
        <pre className="bg-green-950/30 text-green-300 p-1 rounded mt-1 whitespace-pre-wrap max-h-32 overflow-auto border-l-2 border-green-500">
          {inp.new_string}
        </pre>
      )}
      {result != null && typeof result === "string" && result.length > 0 && (
        <div className="text-green-600 dark:text-green-400 mt-1">{result}</div>
      )}
    </div>
  );
}
