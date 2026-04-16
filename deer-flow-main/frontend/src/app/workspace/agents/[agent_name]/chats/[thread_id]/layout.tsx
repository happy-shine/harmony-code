"use client";

// ArtifactsProvider removed in M4.4; files live in the workspace file browser.
import { PromptInputProvider } from "@/components/ai-elements/prompt-input";
import { SubtasksProvider } from "@/core/tasks/context";

export default function AgentChatLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <SubtasksProvider>
      <PromptInputProvider>{children}</PromptInputProvider>
    </SubtasksProvider>
  );
}
