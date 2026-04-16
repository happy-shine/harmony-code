// Artifacts side panel removed in M4.4 — files now live in the workspace
// file browser (`@/components/workspace/file-browser`). This component is
// retained as a thin shell around the chat children so the agent chat page
// still compiles until the full LangGraph-shaped message flow is replaced
// in M5.

const ChatBox: React.FC<{ children: React.ReactNode; threadId: string }> = ({
  children,
  threadId,
}) => {
  void threadId;
  return <div className="relative flex size-full">{children}</div>;
};

export { ChatBox };
