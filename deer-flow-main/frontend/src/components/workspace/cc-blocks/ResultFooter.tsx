export function ResultFooter({
  duration_ms,
  total_cost_usd,
  usage,
}: {
  duration_ms: number;
  total_cost_usd?: number;
  usage?: { input_tokens: number; output_tokens: number };
}) {
  return (
    <div className="text-xs text-neutral-500 border-t border-neutral-200 dark:border-neutral-800 py-1 px-2 my-2 flex gap-3">
      <span>{(duration_ms / 1000).toFixed(1)}s</span>
      {total_cost_usd != null && <span>${total_cost_usd.toFixed(4)}</span>}
      {usage && (
        <span>
          {usage.input_tokens}&rarr;{usage.output_tokens} tok
        </span>
      )}
    </div>
  );
}
