"use client";

import {
  DownloadIcon,
  PenLineIcon,
  PlusIcon,
  Trash2Icon,
  UploadIcon,
} from "lucide-react";
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
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  exportMemory,
  useClearMemory,
  useCreateMemoryFact,
  useDeleteMemoryFact,
  useHarmonyMemory,
  useImportMemory,
  useUpdateMemoryFact,
  type MemoryFact,
  type MemoryFactInput,
  type MemoryFactPatchInput,
} from "@/core/memory/harmony-memory";
import { formatTimeAgo } from "@/core/utils/datetime";

import { SettingsSection } from "./settings-section";

type FactFormState = {
  content: string;
  category: string;
  confidence: string;
};

const DEFAULT_FACT_FORM_STATE: FactFormState = {
  content: "",
  category: "context",
  confidence: "0.8",
};

function confidenceLevel(value: number): { label: string; cls: string } {
  const v = Math.min(1, Math.max(0, value));
  if (v >= 0.85) return { label: "Very high", cls: "text-emerald-500" };
  if (v >= 0.65) return { label: "High", cls: "text-blue-500" };
  return { label: "Normal", cls: "text-muted-foreground" };
}

function upperFirst(s: string) {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export function MemorySettingsPage() {
  const { data: memory, isLoading, error } = useHarmonyMemory();
  const clearMemory = useClearMemory();
  const deleteFact = useDeleteMemoryFact();
  const importMemoryMutation = useImportMemory();
  const [query, setQuery] = useState("");
  const [factEditorOpen, setFactEditorOpen] = useState(false);
  const [factToEdit, setFactToEdit] = useState<MemoryFact | null>(null);
  const [factForm, setFactForm] = useState<FactFormState>(
    DEFAULT_FACT_FORM_STATE,
  );
  const [factToDelete, setFactToDelete] = useState<MemoryFact | null>(null);
  const [clearDialogOpen, setClearDialogOpen] = useState(false);
  const [isExporting, setIsExporting] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const createFact = useCreateMemoryFact();
  const updateFact = useUpdateMemoryFact();

  const filteredFacts = useMemo(() => {
    if (!memory) return [];
    const needle = query.trim().toLowerCase();
    if (!needle) return memory.facts;
    return memory.facts.filter((f) =>
      `${f.content} ${f.category}`.toLowerCase().includes(needle),
    );
  }, [memory, query]);

  function openCreate() {
    setFactToEdit(null);
    setFactForm(DEFAULT_FACT_FORM_STATE);
    setFactEditorOpen(true);
  }

  function openEdit(f: MemoryFact) {
    setFactToEdit(f);
    setFactForm({
      content: f.content,
      category: f.category,
      confidence: String(f.confidence),
    });
    setFactEditorOpen(true);
  }

  async function handleSaveFact() {
    const content = factForm.content.trim();
    if (!content) {
      toast.error("Content is required");
      return;
    }
    const confidence = Number(factForm.confidence);
    if (!Number.isFinite(confidence) || confidence < 0 || confidence > 1) {
      toast.error("Confidence must be between 0 and 1");
      return;
    }
    const input: MemoryFactInput = {
      content,
      category: factForm.category.trim() || "context",
      confidence,
    };
    try {
      if (factToEdit) {
        const patch: MemoryFactPatchInput = { ...input };
        await updateFact.mutateAsync({ id: factToEdit.id, input: patch });
        toast.success("Fact updated");
      } else {
        await createFact.mutateAsync(input);
        toast.success("Fact added");
      }
      setFactEditorOpen(false);
      setFactToEdit(null);
      setFactForm(DEFAULT_FACT_FORM_STATE);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  }

  async function handleDeleteFact() {
    if (!factToDelete) return;
    try {
      await deleteFact.mutateAsync(factToDelete.id);
      toast.success("Fact deleted");
      setFactToDelete(null);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  }

  async function handleClear() {
    try {
      const res = await clearMemory.mutateAsync();
      toast.success(`Cleared ${res.deleted} fact${res.deleted === 1 ? "" : "s"}`);
      setClearDialogOpen(false);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  }

  async function handleExport() {
    try {
      setIsExporting(true);
      const data = await exportMemory();
      const fileName = `harmony-memory-${data.lastUpdated.replace(/[:.]/g, "-")}.json`;
      const blob = new Blob([JSON.stringify(data, null, 2)], {
        type: "application/json",
      });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = fileName;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      toast.success("Memory exported");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    } finally {
      setIsExporting(false);
    }
  }

  async function handleFileSelection(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    try {
      const parsed = JSON.parse(await file.text()) as {
        facts?: Array<Partial<MemoryFact>>;
      };
      if (!Array.isArray(parsed.facts)) {
        toast.error("Invalid file: expected { facts: [...] }");
        return;
      }
      const res = await importMemoryMutation.mutateAsync({
        facts: parsed.facts,
      });
      toast.success(`Imported ${res.imported} fact${res.imported === 1 ? "" : "s"}`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  }

  const isFactFormPending = createFact.isPending || updateFact.isPending;

  return (
    <>
      <SettingsSection
        title="Memory"
        description="Facts the agent should keep in mind across threads."
      >
        {isLoading ? (
          <div className="text-muted-foreground text-sm">Loading…</div>
        ) : error ? (
          <div className="text-sm text-red-500">
            Error: {error instanceof Error ? error.message : String(error)}
          </div>
        ) : !memory ? (
          <div className="text-muted-foreground text-sm">No memory yet.</div>
        ) : (
          <div className="space-y-4">
            <div className="flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
              <Input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search memory"
                className="sm:max-w-xs"
              />
              <div className="flex flex-wrap gap-2">
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".json,application/json"
                  className="hidden"
                  onChange={(e) => void handleFileSelection(e)}
                />
                <Button
                  variant="outline"
                  onClick={() => fileInputRef.current?.click()}
                  disabled={importMemoryMutation.isPending}
                >
                  <UploadIcon className="mr-2 h-4 w-4" />
                  Import
                </Button>
                <Button
                  variant="outline"
                  onClick={() => void handleExport()}
                  disabled={isExporting}
                >
                  <DownloadIcon className="mr-2 h-4 w-4" />
                  {isExporting ? "Exporting…" : "Export"}
                </Button>
                <Button variant="outline" onClick={openCreate}>
                  <PlusIcon className="mr-2 h-4 w-4" />
                  Add fact
                </Button>
                <Button
                  variant="destructive"
                  onClick={() => setClearDialogOpen(true)}
                  disabled={clearMemory.isPending || memory.facts.length === 0}
                >
                  Clear all
                </Button>
              </div>
            </div>
            {filteredFacts.length === 0 ? (
              <div className="text-muted-foreground rounded-lg border border-dashed p-4 text-sm">
                {query
                  ? "No matching facts."
                  : "No saved facts yet. Click \u201cAdd fact\u201d to create one."}
              </div>
            ) : (
              <div className="space-y-3">
                {filteredFacts.map((f) => {
                  const { label, cls } = confidenceLevel(f.confidence);
                  return (
                    <div
                      key={f.id}
                      className="flex flex-col gap-3 rounded-md border p-3 sm:flex-row sm:items-start sm:justify-between"
                    >
                      <div className="min-w-0 space-y-2">
                        <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
                          <span>
                            <span className="text-muted-foreground">Category:</span>{" "}
                            {upperFirst(f.category)}
                          </span>
                          <span>
                            <span className="text-muted-foreground">Confidence:</span>{" "}
                            <span className={cls}>{label}</span>
                          </span>
                          <span>
                            <span className="text-muted-foreground">Added:</span>{" "}
                            {formatTimeAgo(f.createdAt)}
                          </span>
                        </div>
                        <p className="text-sm break-words">{f.content}</p>
                      </div>
                      <div className="flex shrink-0 items-center gap-1 self-start sm:ml-3">
                        <Button
                          variant="ghost"
                          size="icon"
                          onClick={() => openEdit(f)}
                          aria-label="Edit"
                          title="Edit"
                        >
                          <PenLineIcon className="h-4 w-4" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="text-destructive hover:text-destructive"
                          onClick={() => setFactToDelete(f)}
                          aria-label="Delete"
                          title="Delete"
                        >
                          <Trash2Icon className="h-4 w-4" />
                        </Button>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}
      </SettingsSection>

      {/* Add / edit fact dialog */}
      <Dialog
        open={factEditorOpen}
        onOpenChange={(open) => {
          setFactEditorOpen(open);
          if (!open) {
            setFactToEdit(null);
            setFactForm(DEFAULT_FACT_FORM_STATE);
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{factToEdit ? "Edit fact" : "Add fact"}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <label className="text-sm font-medium">Content</label>
              <Textarea
                rows={4}
                value={factForm.content}
                onChange={(e) =>
                  setFactForm((c) => ({ ...c, content: e.target.value }))
                }
                placeholder="e.g. User prefers terse, bullet-point summaries."
              />
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-2">
                <label className="text-sm font-medium">Category</label>
                <Input
                  value={factForm.category}
                  onChange={(e) =>
                    setFactForm((c) => ({ ...c, category: e.target.value }))
                  }
                  placeholder="context"
                />
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium">
                  Confidence{" "}
                  <span className="text-muted-foreground text-xs">(0–1)</span>
                </label>
                <Input
                  type="number"
                  min="0"
                  max="1"
                  step="0.05"
                  value={factForm.confidence}
                  onChange={(e) =>
                    setFactForm((c) => ({ ...c, confidence: e.target.value }))
                  }
                />
              </div>
            </div>
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setFactEditorOpen(false)}
              disabled={isFactFormPending}
            >
              Cancel
            </Button>
            <Button
              onClick={() => void handleSaveFact()}
              disabled={isFactFormPending}
            >
              {isFactFormPending ? "Saving…" : "Save"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete fact dialog */}
      <Dialog
        open={factToDelete !== null}
        onOpenChange={(o) => !o && setFactToDelete(null)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete this fact?</DialogTitle>
            <DialogDescription>
              This cannot be undone.
            </DialogDescription>
          </DialogHeader>
          {factToDelete ? (
            <div className="bg-muted rounded-md border p-3 text-sm break-words">
              {factToDelete.content}
            </div>
          ) : null}
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setFactToDelete(null)}
              disabled={deleteFact.isPending}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={() => void handleDeleteFact()}
              disabled={deleteFact.isPending}
            >
              {deleteFact.isPending ? "Deleting…" : "Delete"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Clear all dialog */}
      <Dialog open={clearDialogOpen} onOpenChange={setClearDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Clear all memory?</DialogTitle>
            <DialogDescription>
              Every saved fact will be removed. This cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setClearDialogOpen(false)}
              disabled={clearMemory.isPending}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={() => void handleClear()}
              disabled={clearMemory.isPending}
            >
              {clearMemory.isPending ? "Clearing…" : "Clear all"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
