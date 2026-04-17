"use client";

import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemTitle,
} from "@/components/ui/item";
import { Switch } from "@/components/ui/switch";
import { useI18n } from "@/core/i18n/hooks";
import {
  useHarmonyMCPServers,
  useToggleMCPServer,
  type HarmonyMCPServer,
} from "@/core/mcp/harmony-mcp";

import { SettingsSection } from "./settings-section";

export function ToolSettingsPage() {
  const { t } = useI18n();
  const { data: servers, isLoading, error } = useHarmonyMCPServers();

  return (
    <SettingsSection
      title={t.settings.tools.title}
      description={t.settings.tools.description}
    >
      {isLoading ? (
        <div className="text-muted-foreground text-sm">{t.common.loading}</div>
      ) : error ? (
        <div className="text-sm text-red-500">
          Error: {error instanceof Error ? error.message : String(error)}
        </div>
      ) : servers && servers.length > 0 ? (
        <MCPServerList servers={servers} />
      ) : (
        <div className="text-muted-foreground rounded-lg border border-dashed p-4 text-sm">
          No MCP servers configured yet.
        </div>
      )}
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

function MCPServerList({ servers }: { servers: HarmonyMCPServer[] }) {
  const { mutate: toggle, isPending } = useToggleMCPServer();
  return (
    <div className="flex w-full flex-col gap-4">
      {servers.map((s) => {
        // Global rows (user_id IS NULL) are read-only for non-admins; the
        // backend returns 403 on PATCH, so disable the switch up front.
        const readOnly = s.user_id === null;
        return (
          <Item className="w-full" variant="outline" key={s.id}>
            <ItemContent>
              <ItemTitle>
                <div className="flex items-center gap-2">
                  <div>{s.name}</div>
                  {readOnly ? (
                    <span className="text-muted-foreground text-xs">(global · read-only)</span>
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
                disabled={readOnly || isPending}
                onCheckedChange={(checked) =>
                  toggle({ id: s.id, enabled: checked })
                }
              />
            </ItemActions>
          </Item>
        );
      })}
    </div>
  );
}
