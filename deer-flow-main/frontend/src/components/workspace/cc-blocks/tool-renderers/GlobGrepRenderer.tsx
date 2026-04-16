"use client";

export function GlobGrepRenderer({
  input,
  result,
}: {
  input: unknown;
  result: unknown;
  status: string;
}) {
  const inp = input as {
    pattern?: string;
    path?: string;
    glob?: string;
  } | null;
  const label = inp?.pattern ? `/${inp.pattern}/` : (inp?.glob ?? "");
  return (
    <div className="mt-1 text-xs font-mono">
      {label && (
        <div className="text-neutral-500">
          {label} {inp?.path ? `in ${inp.path}` : ""}
        </div>
      )}
      {result != null && (
        <pre className="bg-neutral-900 text-neutral-100 p-2 rounded mt-1 whitespace-pre-wrap max-h-64 overflow-auto">
          {typeof result === "string"
            ? result
            : JSON.stringify(result, null, 2)}
        </pre>
      )}
    </div>
  );
}
