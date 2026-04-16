"use client";

export function DefaultMcpRenderer({
  input,
  result,
}: {
  input: unknown;
  result: unknown;
  status: string;
}) {
  return (
    <div className="mt-1 text-xs font-mono">
      <details>
        <summary className="cursor-pointer text-neutral-500">Input</summary>
        <pre className="whitespace-pre-wrap text-neutral-600 dark:text-neutral-400 mt-1 max-h-48 overflow-auto">
          {JSON.stringify(input, null, 2)}
        </pre>
      </details>
      {result != null && (
        <details open>
          <summary className="cursor-pointer text-neutral-500 mt-1">
            Result
          </summary>
          <pre className="whitespace-pre-wrap text-neutral-600 dark:text-neutral-400 mt-1 max-h-96 overflow-auto">
            {typeof result === "string"
              ? result
              : JSON.stringify(result, null, 2)}
          </pre>
        </details>
      )}
    </div>
  );
}
