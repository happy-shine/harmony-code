"use client";

/**
 * Harmony-code-native memory hooks.
 *
 * Wraps the v1 memory API (facts-only; summary blocks are always empty
 * shells — the backend doesn't run a summarization pipeline yet, so the
 * UI simply doesn't render the summary section for harmony).
 *
 * Endpoints::
 *   GET    /api/memory                       → UserMemory
 *   DELETE /api/memory                       → clear
 *   POST   /api/memory/facts                 → create
 *   PATCH  /api/memory/facts/{id}            → edit
 *   DELETE /api/memory/facts/{id}            → delete
 *   GET    /api/memory/export                → same as GET /api/memory
 *   POST   /api/memory/import                → bulk replace
 */

import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

export interface MemoryFact {
  id: string;
  content: string;
  category: string;
  confidence: number;
  source: string;
  createdAt: string;
}

export interface UserMemory {
  version: string;
  lastUpdated: string;
  facts: MemoryFact[];
}

export interface MemoryFactInput {
  content: string;
  category?: string;
  confidence?: number;
}

export interface MemoryFactPatchInput {
  content?: string;
  category?: string;
  confidence?: number;
}

async function throwDetail(r: Response): Promise<never> {
  let detail = `${r.status}`;
  try {
    const j = (await r.json()) as { detail?: string };
    if (j.detail) detail = j.detail;
  } catch {
    // fall through
  }
  throw new Error(detail);
}

export async function fetchMemory(): Promise<UserMemory> {
  const r = await fetch("/api/memory", {
    method: "GET",
    credentials: "include",
    cache: "no-store",
  });
  if (!r.ok) await throwDetail(r);
  return (await r.json()) as UserMemory;
}

export async function exportMemory(): Promise<UserMemory> {
  const r = await fetch("/api/memory/export", {
    method: "GET",
    credentials: "include",
    cache: "no-store",
  });
  if (!r.ok) await throwDetail(r);
  return (await r.json()) as UserMemory;
}

export async function importMemory(body: {
  facts: Array<Partial<MemoryFact>>;
}): Promise<{ imported: number }> {
  const r = await fetch("/api/memory/import", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) await throwDetail(r);
  return (await r.json()) as { imported: number };
}

export async function createMemoryFact(
  body: MemoryFactInput,
): Promise<MemoryFact> {
  const r = await fetch("/api/memory/facts", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) await throwDetail(r);
  return (await r.json()) as MemoryFact;
}

export async function updateMemoryFact(
  id: string,
  body: MemoryFactPatchInput,
): Promise<MemoryFact> {
  const r = await fetch(`/api/memory/facts/${id}`, {
    method: "PATCH",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) await throwDetail(r);
  return (await r.json()) as MemoryFact;
}

export async function deleteMemoryFact(id: string): Promise<void> {
  const r = await fetch(`/api/memory/facts/${id}`, {
    method: "DELETE",
    credentials: "include",
  });
  if (!r.ok) await throwDetail(r);
}

export async function clearMemory(): Promise<{ deleted: number }> {
  const r = await fetch("/api/memory", {
    method: "DELETE",
    credentials: "include",
  });
  if (!r.ok) await throwDetail(r);
  return (await r.json()) as { deleted: number };
}

export function useHarmonyMemory() {
  return useQuery<UserMemory>({
    queryKey: ["harmony-memory"],
    queryFn: fetchMemory,
    staleTime: 5_000,
  });
}

export function useCreateMemoryFact() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: MemoryFactInput) => createMemoryFact(body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["harmony-memory"] });
    },
  });
}

export function useUpdateMemoryFact() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, input }: { id: string; input: MemoryFactPatchInput }) =>
      updateMemoryFact(id, input),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["harmony-memory"] });
    },
  });
}

export function useDeleteMemoryFact() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteMemoryFact(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["harmony-memory"] });
    },
  });
}

export function useClearMemory() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => clearMemory(),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["harmony-memory"] });
    },
  });
}

export function useImportMemory() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { facts: Array<Partial<MemoryFact>> }) =>
      importMemory(body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["harmony-memory"] });
    },
  });
}
