"use client";

import { PlusIcon, Trash2Icon } from "lucide-react";
import { useState } from "react";
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
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemTitle,
} from "@/components/ui/item";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { useI18n } from "@/core/i18n/hooks";
import {
  useCreateMCPServer,
  useDeleteMCPServer,
  useHarmonyMCPServers,
  useToggleMCPServer,
  type HarmonyMCPServer,
  type MCPServerCreateInput,
} from "@/core/mcp/harmony-mcp";

import { SettingsSection } from "./settings-section";

type Transport = "stdio" | "sse" | "http";

export function ToolSettingsPage() {
  const { t } = useI18n();
  const { data: servers, isLoading, error } = useHarmonyMCPServers();
  const [addOpen, setAddOpen] = useState(false);
  const [serverToDelete, setServerToDelete] = useState<HarmonyMCPServer | null>(
    null,
  );

  return (
    <SettingsSection
      title={t.settings.tools.title}
      description={t.settings.tools.description}
    >
      <div className="flex w-full flex-col gap-4">
        <div className="flex justify-end">
          <Button size="sm" onClick={() => setAddOpen(true)}>
            <PlusIcon className="size-4" />
            Add MCP server
          </Button>
        </div>
        {isLoading ? (
          <div className="text-muted-foreground text-sm">
            {t.common.loading}
          </div>
        ) : error ? (
          <div className="text-sm text-red-500">
            Error: {error instanceof Error ? error.message : String(error)}
          </div>
        ) : servers && servers.length > 0 ? (
          <MCPServerList
            servers={servers}
            onRequestDelete={(s) => setServerToDelete(s)}
          />
        ) : (
          <div className="text-muted-foreground rounded-lg border border-dashed p-4 text-sm">
            No MCP servers yet. Click &quot;Add MCP server&quot; to wire one up.
          </div>
        )}
      </div>

      <AddMCPDialog open={addOpen} onOpenChange={setAddOpen} />
      <DeleteMCPDialog
        server={serverToDelete}
        onClose={() => setServerToDelete(null)}
      />
    </SettingsSection>
  );
}

function describeServer(s: HarmonyMCPServer): string {
  if (s.transport === "stdio") {
    const argStr = s.args.length > 0 ? " " + s.args.join(" ") : "";
    return `stdio · ${s.command ?? "<no command>"}${argStr}`;
  }
  return `${s.transport}${s.url ? ` · ${s.url}` : ""}`;
}

function MCPServerList({
  servers,
  onRequestDelete,
}: {
  servers: HarmonyMCPServer[];
  onRequestDelete: (s: HarmonyMCPServer) => void;
}) {
  const { mutate: toggle, isPending: togglePending } = useToggleMCPServer();
  return (
    <div className="flex w-full flex-col gap-3">
      {servers.map((s) => {
        const readOnly = s.user_id === null;
        return (
          <Item className="w-full" variant="outline" key={s.id}>
            <ItemContent>
              <ItemTitle>
                <div className="flex items-center gap-2">
                  <div>{s.name}</div>
                  {readOnly ? (
                    <span className="text-muted-foreground text-xs">
                      (global · read-only)
                    </span>
                  ) : null}
                </div>
              </ItemTitle>
              <ItemDescription className="line-clamp-4">
                {describeServer(s)}
              </ItemDescription>
            </ItemContent>
            <ItemActions>
              <Switch
                checked={s.enabled}
                disabled={readOnly || togglePending}
                onCheckedChange={(checked) =>
                  toggle({ id: s.id, enabled: checked })
                }
              />
              <Button
                variant="ghost"
                size="icon"
                disabled={readOnly}
                onClick={() => onRequestDelete(s)}
                aria-label="Delete MCP server"
                title={readOnly ? "Global rows cannot be deleted" : "Delete"}
              >
                <Trash2Icon className="text-muted-foreground size-4" />
              </Button>
            </ItemActions>
          </Item>
        );
      })}
    </div>
  );
}

function parseArgs(raw: string): string[] {
  // Whitespace-tokenized. For quoted args the user can paste JSON-array
  // syntax (starts with ``[``), which we parse as JSON.
  const trimmed = raw.trim();
  if (!trimmed) return [];
  if (trimmed.startsWith("[")) {
    try {
      const parsed = JSON.parse(trimmed);
      if (Array.isArray(parsed)) {
        return parsed.map((x) => String(x));
      }
    } catch {
      // fall through to whitespace split
    }
  }
  return trimmed.split(/\s+/).filter(Boolean);
}

function parseKvLines(raw: string): Record<string, string> {
  // "KEY=value" per line; empty lines ignored. Anything without ``=`` is
  // silently skipped (we'd rather the UI stay forgiving than surface a
  // parser error for a trailing empty line).
  const out: Record<string, string> = {};
  for (const line of raw.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    const eq = trimmed.indexOf("=");
    if (eq < 0) continue;
    out[trimmed.slice(0, eq).trim()] = trimmed.slice(eq + 1);
  }
  return out;
}

function AddMCPDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [name, setName] = useState("");
  const [transport, setTransport] = useState<Transport>("stdio");
  const [command, setCommand] = useState("");
  const [argsRaw, setArgsRaw] = useState("");
  const [url, setUrl] = useState("");
  const [headersRaw, setHeadersRaw] = useState("");
  const [envRaw, setEnvRaw] = useState("");
  const { mutateAsync, isPending } = useCreateMCPServer();

  function reset() {
    setName("");
    setTransport("stdio");
    setCommand("");
    setArgsRaw("");
    setUrl("");
    setHeadersRaw("");
    setEnvRaw("");
  }

  async function handleSubmit() {
    if (!name.trim()) {
      toast.error("Name is required");
      return;
    }
    const body: MCPServerCreateInput = {
      name: name.trim(),
      transport,
      enabled: true,
    };
    if (transport === "stdio") {
      if (!command.trim()) {
        toast.error("Command is required for stdio transport");
        return;
      }
      body.command = command.trim();
      body.args = parseArgs(argsRaw);
      body.env = parseKvLines(envRaw);
    } else {
      if (!url.trim()) {
        toast.error("URL is required for sse/http transport");
        return;
      }
      body.url = url.trim();
      body.headers = parseKvLines(headersRaw);
    }
    try {
      await mutateAsync(body);
      toast.success(`Added MCP server: ${body.name}`);
      onOpenChange(false);
      reset();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!o) reset();
        onOpenChange(o);
      }}
    >
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Add MCP server</DialogTitle>
          <DialogDescription>
            Register a new MCP server for this user. Takes effect on the next
            spawned message.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-2">
            <label className="text-sm font-medium">Name</label>
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. github"
              autoFocus
            />
          </div>
          <div className="space-y-2">
            <label className="text-sm font-medium">Transport</label>
            <Select
              value={transport}
              onValueChange={(v) => setTransport(v as Transport)}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="stdio">stdio (local subprocess)</SelectItem>
                <SelectItem value="sse">sse (remote Server-Sent Events)</SelectItem>
                <SelectItem value="http">http (remote JSON-RPC)</SelectItem>
              </SelectContent>
            </Select>
          </div>

          {transport === "stdio" ? (
            <>
              <div className="space-y-2">
                <label className="text-sm font-medium">Command</label>
                <Input
                  value={command}
                  onChange={(e) => setCommand(e.target.value)}
                  placeholder="npx"
                />
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium">
                  Args{" "}
                  <span className="text-muted-foreground text-xs">
                    (whitespace-separated, or a JSON array for quoted items)
                  </span>
                </label>
                <Input
                  value={argsRaw}
                  onChange={(e) => setArgsRaw(e.target.value)}
                  placeholder="-y @modelcontextprotocol/server-github"
                />
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium">
                  Env{" "}
                  <span className="text-muted-foreground text-xs">
                    (one KEY=value per line)
                  </span>
                </label>
                <Textarea
                  rows={3}
                  value={envRaw}
                  onChange={(e) => setEnvRaw(e.target.value)}
                  placeholder="GITHUB_TOKEN=ghp_..."
                />
              </div>
            </>
          ) : (
            <>
              <div className="space-y-2">
                <label className="text-sm font-medium">URL</label>
                <Input
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  placeholder="https://mcp.example.com/sse"
                />
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium">
                  Headers{" "}
                  <span className="text-muted-foreground text-xs">
                    (one Header-Name=value per line)
                  </span>
                </label>
                <Textarea
                  rows={3}
                  value={headersRaw}
                  onChange={(e) => setHeadersRaw(e.target.value)}
                  placeholder="Authorization=Bearer …"
                />
              </div>
            </>
          )}
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
            {isPending ? "Adding…" : "Add"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function DeleteMCPDialog({
  server,
  onClose,
}: {
  server: HarmonyMCPServer | null;
  onClose: () => void;
}) {
  const { mutateAsync, isPending } = useDeleteMCPServer();
  async function handleDelete() {
    if (!server) return;
    try {
      await mutateAsync(server.id);
      toast.success(`Removed ${server.name}`);
      onClose();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  }
  return (
    <Dialog open={server !== null} onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Delete MCP server?</DialogTitle>
          <DialogDescription>
            {server ? (
              <>
                Remove <span className="font-mono">{server.name}</span>? Threads
                spawned after this will no longer see it.
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
