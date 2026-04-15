"use client";

import { useRef, useState } from "react";

export default function DevCCPage() {
  const [threadId, setThreadId] = useState<string | null>(null);
  const [content, setContent] = useState("say hi in one word");
  const [log, setLog] = useState<string[]>([]);
  const abortRef = useRef<AbortController | null>(null);

  const createThread = async () => {
    const r = await fetch("/api/threads", { method: "POST" });
    const j = (await r.json()) as { id: string };
    setThreadId(j.id);
    setLog((l) => [...l, `[created thread ${j.id}]`]);
  };

  const send = async () => {
    if (!threadId) return;
    abortRef.current = new AbortController();
    try {
      const r = await fetch(`/api/threads/${threadId}/messages`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content }),
        signal: abortRef.current.signal,
      });
      if (!r.body) {
        setLog((l) => [...l, "[no response body]"]);
        return;
      }
      const reader = r.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const parts = buf.split("\n\n");
        const complete = parts.slice(0, -1);
        buf = parts[parts.length - 1] ?? "";
        for (const frame of complete) {
          if (frame.length > 0) setLog((l) => [...l, frame]);
        }
      }
    } catch (err) {
      if ((err as { name?: string }).name === "AbortError") {
        setLog((l) => [...l, "[aborted]"]);
      } else {
        setLog((l) => [...l, `[error] ${String(err)}`]);
      }
    }
  };

  return (
    <div style={{ fontFamily: "monospace", padding: 16 }}>
      <button onClick={createThread}>New thread</button>
      <span style={{ marginLeft: 12 }}>thread: {threadId ?? "(none)"}</span>
      <div style={{ marginTop: 12 }}>
        <input
          value={content}
          onChange={(e) => setContent(e.target.value)}
          style={{ width: 600 }}
        />
        <button onClick={send} disabled={!threadId} style={{ marginLeft: 8 }}>
          Send
        </button>
        <button
          onClick={() => abortRef.current?.abort()}
          style={{ marginLeft: 8 }}
        >
          Stop
        </button>
      </div>
      <pre
        style={{
          marginTop: 16,
          background: "#111",
          color: "#0f0",
          padding: 8,
          maxHeight: 500,
          overflow: "auto",
          whiteSpace: "pre-wrap",
        }}
      >
        {log.join("\n\n")}
      </pre>
    </div>
  );
}
