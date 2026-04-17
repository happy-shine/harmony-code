"use client";

/**
 * Harmony-code-native MCP hooks.
 *
 * Replaces the LangGraph-shaped ``/api/mcp/config`` flow (which the
 * harmony backend does not implement) with the real endpoints:
 *   - ``GET    /api/mcp``          → list the caller's MCP servers
 *   - ``PATCH  /api/mcp/{id}``     → enable/disable or edit a server
 *
 * Kept separate from the legacy ``./hooks.ts`` so we can remove the
 * LangGraph holdover wholesale once all importers have migrated.
 */

import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

export interface HarmonyMCPServer {
  id: string;
  user_id: string | null;
  name: string;
  transport: string;
  command: string | null;
  args: string[];
  url: string | null;
  headers: Record<string, string>;
  env: Record<string, string>;
  enabled: boolean;
}

export interface MCPServerCreateInput {
  name: string;
  transport: "stdio" | "sse" | "http";
  command?: string | null;
  args?: string[];
  url?: string | null;
  headers?: Record<string, string>;
  env?: Record<string, string>;
  enabled?: boolean;
}

export async function createMCPServer(
  body: MCPServerCreateInput,
): Promise<HarmonyMCPServer> {
  const r = await fetch("/api/mcp", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    let detail = `${r.status}`;
    try {
      const j = (await r.json()) as { detail?: string };
      if (j.detail) detail = j.detail;
    } catch {
      // fall through
    }
    throw new Error(detail);
  }
  return (await r.json()) as HarmonyMCPServer;
}

export async function deleteMCPServer(id: string): Promise<void> {
  const r = await fetch(`/api/mcp/${id}`, {
    method: "DELETE",
    credentials: "include",
  });
  if (!r.ok) {
    let detail = `${r.status}`;
    try {
      const j = (await r.json()) as { detail?: string };
      if (j.detail) detail = j.detail;
    } catch {
      // fall through
    }
    throw new Error(detail);
  }
}

export async function fetchMCPServers(): Promise<HarmonyMCPServer[]> {
  const r = await fetch("/api/mcp", { method: "GET", credentials: "include", cache: "no-store" });
  if (!r.ok) throw new Error(`list mcp failed: ${r.status}`);
  return (await r.json()) as HarmonyMCPServer[];
}

export async function patchMCPServer(
  id: string,
  patch: Partial<Pick<HarmonyMCPServer, "enabled" | "name" | "args" | "env" | "headers" | "url" | "command">>,
): Promise<HarmonyMCPServer> {
  const r = await fetch(`/api/mcp/${id}`, {
    method: "PATCH",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!r.ok) {
    // 403 = not-yours (global rows), 404 = unknown id
    let detail = `${r.status}`;
    try {
      const j = (await r.json()) as { detail?: string };
      if (j.detail) detail = j.detail;
    } catch {
      // fall through
    }
    throw new Error(detail);
  }
  return (await r.json()) as HarmonyMCPServer;
}

export function useHarmonyMCPServers() {
  return useQuery<HarmonyMCPServer[]>({
    queryKey: ["harmony-mcp"],
    queryFn: fetchMCPServers,
    staleTime: 5_000,
  });
}

export function useToggleMCPServer() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      patchMCPServer(id, { enabled }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["harmony-mcp"] });
    },
  });
}

export function useCreateMCPServer() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: MCPServerCreateInput) => createMCPServer(body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["harmony-mcp"] });
    },
  });
}

export function useDeleteMCPServer() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteMCPServer(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["harmony-mcp"] });
    },
  });
}
