"use client";

import { useId, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { CopyButton } from "@/components/copy-button";
import { NewTabCue } from "@/components/new-tab-link";
import { SettingCard } from "@/components/settings/setting-card";
import { Button, buttonVariants } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { CONNECT_AGENT_GUIDE_URL } from "@/lib/agent-links";
import { getConnectInfo } from "@/lib/api";
import type { ConnectClient, ConnectProfile } from "@/lib/types";

const PROFILES = [
  { name: "full", label: "Full" },
  { name: "hunt", label: "Hunt" },
  { name: "apply", label: "Apply" },
  { name: "explore", label: "Explore" },
  { name: "templates", label: "Templates" },
  { name: "career", label: "Career" },
] as const satisfies readonly { name: ConnectProfile["name"]; label: string }[];

export function ConnectAssistantCard() {
  const [profile, setProfile] = useState<ConnectProfile["name"]>("full");
  const profileId = useId();
  const query = useQuery({
    queryKey: ["connect", profile],
    queryFn: () => getConnectInfo(profile),
  });

  return (
    <SettingCard
      id="connect-assistant"
      title="Connect your assistant"
      description={
        <div className="grid gap-4">
          <p>Choose your assistant below. Keep Maestro CS running on this computer while you use it.</p>
          <div className="flex items-center gap-3 text-foreground">
            <label htmlFor={profileId}>Profile</label>
            <Select
              value={profile}
              onValueChange={(next) => {
                if (PROFILES.some((p) => p.name === next)) setProfile(next as ConnectProfile["name"]);
              }}
            >
              <SelectTrigger id={profileId}>
                <SelectValue>{PROFILES.find((p) => p.name === profile)?.label}</SelectValue>
              </SelectTrigger>
              <SelectContent>
                {PROFILES.map(({ name, label }) => (
                  <SelectItem key={name} value={name}>{label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
      }
      errorTitle="Couldn't load assistant connections."
      query={query}
    >
      {(data) => (
        <ul className="grid divide-y">
          {data.clients.map((client) => (
            <ConnectClientRow key={client.id} client={client} bundleAvailable={data.bundle_available} />
          ))}
        </ul>
      )}
    </SettingCard>
  );
}

function ConnectClientRow({
  client: { label, kind, value },
  bundleAvailable,
}: {
  client: ConnectClient;
  bundleAvailable: boolean;
}) {
  const bundleHintId = useId();
  return (
    <li className="grid min-w-0 gap-2 py-4 first:pt-0 last:pb-0 @lg/setting:grid-cols-[8rem_minmax(0,1fr)]">
      <h3 className="text-label-large">{label}</h3>
      <div className="grid min-w-0 gap-2">
        {kind === "command" || kind === "url" || kind === "steps" ? (
          <div className="flex min-w-0 items-start gap-2">
            <code className="bg-muted min-w-0 flex-1 rounded-corner-xs p-2 font-mono text-body-small break-all">
              {value}
            </code>
            <CopyButton text={value} label={`Copy for ${label}`} />
          </div>
        ) : null}
        {kind === "deeplink" ? (
          <>
            <a href={value} className={buttonVariants({ variant: "outline", size: "sm", className: "justify-self-start" })}>
              Add to {label}
            </a>
            <p className="text-muted-foreground text-body-small">
              Opens {label} and asks you to confirm.
            </p>
          </>
        ) : kind === "download" ? (
          bundleAvailable ? (
            <>
              <a href={value} className={buttonVariants({ variant: "outline", size: "sm", className: "justify-self-start" })}>
                Download for Claude Desktop
              </a>
              <p className="text-muted-foreground text-body-small">
                Double-click the file, then choose Install in Claude Desktop.
              </p>
            </>
          ) : (
            <>
              <Button disabled variant="outline" size="sm" className="justify-self-start" aria-describedby={bundleHintId}>
                Download for Claude Desktop
              </Button>
              <p id={bundleHintId} className="text-muted-foreground text-body-small">
                The bundle is not on this install. See{" "}
                <a href={CONNECT_AGENT_GUIDE_URL} target="_blank" rel="noopener noreferrer" className="text-primary underline underline-offset-4">
                  the guide<NewTabCue />
                </a>.
              </p>
            </>
          )
        ) : kind === "steps" ? (
          <>
            <ol className="text-muted-foreground grid list-decimal gap-1 pl-5 text-body-small">
              <li>In the Codex app, open Plugins › Add › Create custom MCP server.</li>
              <li>Choose Server URL and paste this address.</li>
              <li>Set Authentication to No authentication.</li>
            </ol>
            <p className="text-muted-foreground text-body-small">
              Don&apos;t use the Tunnel option. It would let someone outside this computer reach Maestro.
            </p>
          </>
        ) : null}
      </div>
    </li>
  );
}
