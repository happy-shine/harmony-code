"use client";

/**
 * Harmony-code-native skill hooks.
 *
 * Replaces the LangGraph-shaped ``{skills: [...]}`` response + PUT-by-name
 * flow (which the harmony backend does not implement) with the real
 * endpoints from ``app/gateway/routers/skills.py``:
 *   - ``GET    /api/skills``        → list of SkillOut
 *   - ``PATCH  /api/skills/{id}``   → toggle enabled, rename, etc.
 *
 * Kept separate from the legacy ``./hooks.ts`` so we can remove the
 * holdover wholesale once all importers have migrated.
 */

import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

export interface HarmonySkill {
  id: string;
  user_id: string | null;
  name: string;
  source: string; // "upload" | "git" | ... — opaque display tag
  path: string;
  enabled: boolean;
}

export async function fetchHarmonySkills(): Promise<HarmonySkill[]> {
  const r = await fetch("/api/skills", { method: "GET", credentials: "include", cache: "no-store" });
  if (!r.ok) throw new Error(`list skills failed: ${r.status}`);
  return (await r.json()) as HarmonySkill[];
}

export async function patchHarmonySkill(
  id: string,
  patch: Partial<Pick<HarmonySkill, "enabled" | "name">>,
): Promise<HarmonySkill> {
  const r = await fetch(`/api/skills/${id}`, {
    method: "PATCH",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
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
  return (await r.json()) as HarmonySkill;
}

export function useHarmonySkills() {
  return useQuery<HarmonySkill[]>({
    queryKey: ["harmony-skills"],
    queryFn: fetchHarmonySkills,
    staleTime: 5_000,
  });
}

export function useToggleHarmonySkill() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      patchHarmonySkill(id, { enabled }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["harmony-skills"] });
    },
  });
}
