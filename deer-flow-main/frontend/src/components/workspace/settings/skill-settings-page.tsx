"use client";

import { SparklesIcon } from "lucide-react";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";
import {
  Item,
  ItemActions,
  ItemTitle,
  ItemContent,
  ItemDescription,
} from "@/components/ui/item";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useI18n } from "@/core/i18n/hooks";
import {
  useHarmonySkills,
  useToggleHarmonySkill,
  type HarmonySkill,
} from "@/core/skills/harmony-skills";

import { SettingsSection } from "./settings-section";

type SkillFilter = "custom" | "public";

export function SkillSettingsPage({ onClose }: { onClose?: () => void } = {}) {
  const { t } = useI18n();
  const { data: skills, isLoading, error } = useHarmonySkills();
  return (
    <SettingsSection
      title={t.settings.skills.title}
      description={t.settings.skills.description}
    >
      {isLoading ? (
        <div className="text-muted-foreground text-sm">{t.common.loading}</div>
      ) : error ? (
        <div className="text-sm text-red-500">
          Error: {error instanceof Error ? error.message : String(error)}
        </div>
      ) : (
        <SkillSettingsList skills={skills ?? []} onClose={onClose} />
      )}
    </SettingsSection>
  );
}

function SkillSettingsList({
  skills,
  onClose,
}: {
  skills: HarmonySkill[];
  onClose?: () => void;
}) {
  const { t } = useI18n();
  const router = useRouter();
  const [filter, setFilter] = useState<SkillFilter>("custom");
  const { mutate: toggle, isPending } = useToggleHarmonySkill();

  const filteredSkills = useMemo(() => {
    // "public" = global rows (user_id IS NULL, shared across users).
    // "custom" = rows owned by the caller. This split replaces deer-flow's
    // ``skill.category`` which the harmony backend doesn't carry.
    return skills.filter((s) =>
      filter === "public" ? s.user_id === null : s.user_id !== null,
    );
  }, [skills, filter]);

  const handleCreateSkill = () => {
    onClose?.();
    router.push("/workspace/chats/new?mode=skill");
  };

  return (
    <div className="flex w-full flex-col gap-4">
      <header className="flex justify-between">
        <div className="flex gap-2">
          <Tabs
            defaultValue="custom"
            onValueChange={(v) => setFilter(v as SkillFilter)}
          >
            <TabsList variant="line">
              <TabsTrigger value="custom">{t.common.custom}</TabsTrigger>
              <TabsTrigger value="public">{t.common.public}</TabsTrigger>
            </TabsList>
          </Tabs>
        </div>
        <div>
          <Button size="sm" onClick={handleCreateSkill}>
            <SparklesIcon className="size-4" />
            {t.settings.skills.createSkill}
          </Button>
        </div>
      </header>
      {filteredSkills.length === 0 && (
        <EmptySkill onCreateSkill={handleCreateSkill} />
      )}
      {filteredSkills.length > 0 &&
        filteredSkills.map((skill) => {
          const readOnly = skill.user_id === null;
          return (
            <Item className="w-full" variant="outline" key={skill.id}>
              <ItemContent>
                <ItemTitle>
                  <div className="flex items-center gap-2">
                    {skill.name}
                    {readOnly ? (
                      <span className="text-muted-foreground text-xs">
                        (global · read-only)
                      </span>
                    ) : null}
                  </div>
                </ItemTitle>
                <ItemDescription className="line-clamp-4">
                  {skill.source === "upload"
                    ? "Installed from upload"
                    : skill.source === "git"
                      ? "Installed from git"
                      : skill.source}
                </ItemDescription>
              </ItemContent>
              <ItemActions>
                <Switch
                  checked={skill.enabled}
                  disabled={readOnly || isPending}
                  onCheckedChange={(checked) =>
                    toggle({ id: skill.id, enabled: checked })
                  }
                />
              </ItemActions>
            </Item>
          );
        })}
    </div>
  );
}

function EmptySkill({ onCreateSkill }: { onCreateSkill: () => void }) {
  const { t } = useI18n();
  return (
    <Empty>
      <EmptyHeader>
        <EmptyMedia variant="icon">
          <SparklesIcon />
        </EmptyMedia>
        <EmptyTitle>{t.settings.skills.emptyTitle}</EmptyTitle>
        <EmptyDescription>
          {t.settings.skills.emptyDescription}
        </EmptyDescription>
      </EmptyHeader>
      <EmptyContent>
        <Button onClick={onCreateSkill}>{t.settings.skills.emptyButton}</Button>
      </EmptyContent>
    </Empty>
  );
}
