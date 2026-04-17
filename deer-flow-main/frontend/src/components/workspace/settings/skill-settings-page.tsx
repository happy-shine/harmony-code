"use client";

import { GitBranchIcon, SparklesIcon, Trash2Icon, UploadIcon } from "lucide-react";
import { useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";
import { Input } from "@/components/ui/input";
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
  useDeleteHarmonySkill,
  useGitInstallSkill,
  useHarmonySkills,
  useToggleHarmonySkill,
  useUploadSkillZip,
  type HarmonySkill,
} from "@/core/skills/harmony-skills";

import { SettingsSection } from "./settings-section";

type SkillFilter = "custom" | "public";

export function SkillSettingsPage(_: { onClose?: () => void } = {}) {
  const { t } = useI18n();
  const { data: skills, isLoading, error } = useHarmonySkills();
  const [gitOpen, setGitOpen] = useState(false);
  const [skillToDelete, setSkillToDelete] = useState<HarmonySkill | null>(null);

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
        <SkillSettingsList
          skills={skills ?? []}
          onOpenGit={() => setGitOpen(true)}
          onRequestDelete={(s) => setSkillToDelete(s)}
        />
      )}

      <GitInstallDialog open={gitOpen} onOpenChange={setGitOpen} />
      <DeleteSkillDialog
        skill={skillToDelete}
        onClose={() => setSkillToDelete(null)}
      />
    </SettingsSection>
  );
}

function SkillSettingsList({
  skills,
  onOpenGit,
  onRequestDelete,
}: {
  skills: HarmonySkill[];
  onOpenGit: () => void;
  onRequestDelete: (s: HarmonySkill) => void;
}) {
  const { t } = useI18n();
  const [filter, setFilter] = useState<SkillFilter>("custom");
  const { mutate: toggle, isPending: togglePending } = useToggleHarmonySkill();
  const { mutateAsync: uploadZip, isPending: uploadPending } =
    useUploadSkillZip();
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const filteredSkills = useMemo(() => {
    // "public" = global rows (user_id IS NULL); "custom" = caller-owned.
    return skills.filter((s) =>
      filter === "public" ? s.user_id === null : s.user_id !== null,
    );
  }, [skills, filter]);

  async function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".zip")) {
      toast.error("Skill files must be .zip archives");
      return;
    }
    try {
      const created = await uploadZip(file);
      toast.success(`Installed skill: ${created.name}`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <div className="flex w-full flex-col gap-4">
      <header className="flex items-center justify-between">
        <Tabs
          defaultValue="custom"
          onValueChange={(v) => setFilter(v as SkillFilter)}
        >
          <TabsList variant="line">
            <TabsTrigger value="custom">{t.common.custom}</TabsTrigger>
            <TabsTrigger value="public">{t.common.public}</TabsTrigger>
          </TabsList>
        </Tabs>
        <div className="flex gap-2">
          <input
            ref={fileInputRef}
            type="file"
            accept=".zip,application/zip"
            className="hidden"
            onChange={handleFileChange}
          />
          <Button
            size="sm"
            variant="outline"
            onClick={() => fileInputRef.current?.click()}
            disabled={uploadPending}
          >
            <UploadIcon className="size-4" />
            {uploadPending ? "Uploading…" : "Upload .zip"}
          </Button>
          <Button size="sm" variant="outline" onClick={onOpenGit}>
            <GitBranchIcon className="size-4" />
            Install from Git
          </Button>
        </div>
      </header>
      {filteredSkills.length === 0 ? (
        <EmptySkill
          filter={filter}
          onUploadClick={() => fileInputRef.current?.click()}
          onGitClick={onOpenGit}
        />
      ) : (
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
                  disabled={readOnly || togglePending}
                  onCheckedChange={(checked) =>
                    toggle({ id: skill.id, enabled: checked })
                  }
                />
                <Button
                  variant="ghost"
                  size="icon"
                  disabled={readOnly}
                  onClick={() => onRequestDelete(skill)}
                  aria-label="Delete skill"
                  title={readOnly ? "Global skills cannot be deleted" : "Delete"}
                >
                  <Trash2Icon className="text-muted-foreground size-4" />
                </Button>
              </ItemActions>
            </Item>
          );
        })
      )}
    </div>
  );
}

function EmptySkill({
  filter,
  onUploadClick,
  onGitClick,
}: {
  filter: SkillFilter;
  onUploadClick: () => void;
  onGitClick: () => void;
}) {
  return (
    <Empty>
      <EmptyHeader>
        <EmptyMedia variant="icon">
          <SparklesIcon />
        </EmptyMedia>
        <EmptyTitle>
          {filter === "custom" ? "No custom skills yet" : "No global skills"}
        </EmptyTitle>
        <EmptyDescription>
          {filter === "custom"
            ? "Install a skill from a .zip archive or clone one from a Git repository."
            : "Global skills are provisioned by an admin."}
        </EmptyDescription>
      </EmptyHeader>
      {filter === "custom" ? (
        <EmptyContent>
          <div className="flex gap-2">
            <Button variant="outline" onClick={onUploadClick}>
              <UploadIcon className="size-4" />
              Upload .zip
            </Button>
            <Button onClick={onGitClick}>
              <GitBranchIcon className="size-4" />
              Install from Git
            </Button>
          </div>
        </EmptyContent>
      ) : null}
    </Empty>
  );
}

function GitInstallDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [url, setUrl] = useState("");
  const [name, setName] = useState("");
  const { mutateAsync, isPending } = useGitInstallSkill();

  async function handleSubmit() {
    if (!url.trim()) {
      toast.error("URL is required");
      return;
    }
    try {
      const created = await mutateAsync({
        url: url.trim(),
        name: name.trim() || undefined,
      });
      toast.success(`Installed skill: ${created.name}`);
      setUrl("");
      setName("");
      onOpenChange(false);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!o) {
          setUrl("");
          setName("");
        }
        onOpenChange(o);
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Install skill from Git</DialogTitle>
          <DialogDescription>
            Shallow-clones the repository; ``SKILL.md`` must exist at the root.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-2">
            <label className="text-sm font-medium">Repository URL</label>
            <Input
              autoFocus
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://github.com/owner/skill-name"
            />
          </div>
          <div className="space-y-2">
            <label className="text-sm font-medium">
              Name{" "}
              <span className="text-muted-foreground text-xs">(optional)</span>
            </label>
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Leave blank to auto-detect from SKILL.md"
            />
          </div>
        </div>
        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={isPending}
          >
            Cancel
          </Button>
          <Button onClick={() => void handleSubmit()} disabled={isPending}>
            {isPending ? "Installing…" : "Install"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function DeleteSkillDialog({
  skill,
  onClose,
}: {
  skill: HarmonySkill | null;
  onClose: () => void;
}) {
  const { mutateAsync, isPending } = useDeleteHarmonySkill();
  async function handleDelete() {
    if (!skill) return;
    try {
      await mutateAsync(skill.id);
      toast.success(`Removed ${skill.name}`);
      onClose();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  }
  return (
    <Dialog open={skill !== null} onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Delete skill?</DialogTitle>
          <DialogDescription>
            {skill ? (
              <>
                Remove <span className="font-mono">{skill.name}</span>? The
                skill directory will be cleaned up on disk.
              </>
            ) : null}
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={isPending}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            onClick={() => void handleDelete()}
            disabled={isPending}
          >
            {isPending ? "Removing…" : "Delete"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
