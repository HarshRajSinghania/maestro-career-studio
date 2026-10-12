"use client";

import { useId, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { AutosaveStatus } from "@/components/settings/autosave-status";
import { SettingCard, SettingCardAction } from "@/components/settings/setting-card";
import { ACTION_ROW, SwitchRow } from "@/components/settings/setting-layout";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { useLeaveGuard } from "@/hooks/use-leave-guard";
import { apiFetch } from "@/lib/api";
import { CONCEPT_ICONS } from "@/lib/concept-icons";
import { couldnt } from "@/lib/error-text";
import { RESUMES_FOLDER_KEY, RESUMES_FOLDER_URL } from "@/lib/resumes-folder";
import type { ResumesFolderSettings } from "@/lib/types";

const UNAVAILABLE: Record<"not_here" | "always_on_copy", string> = {
  not_here: "Your resumes are already in the applications folder of your Maestro checkout.",
  always_on_copy: "This copy keeps no resumes folder.",
};

const sentence = ({ message }: { message: string }) => message;

type Patch = { enabled?: boolean; folder?: string | null };

/** Where Maestro saves a visible copy of each finished resume PDF (Settings > Tailoring). */
export function ResumesFolderSection() {
  const qc = useQueryClient();
  const id = useId();
  const setting = useQuery({
    queryKey: RESUMES_FOLDER_KEY,
    queryFn: () => apiFetch<ResumesFolderSettings>(RESUMES_FOLDER_URL),
  });

  // The switch saves on toggle and the folder saves behind its own Save, so one mutation serves both.
  const save = useMutation({
    mutationFn: (patch: Patch) =>
      apiFetch<ResumesFolderSettings>(RESUMES_FOLDER_URL, {
        method: "PUT",
        body: JSON.stringify(patch),
      }),
    onSuccess: (result, patch) => {
      qc.setQueryData(RESUMES_FOLDER_KEY, result);
      if ("folder" in patch) toast.success("Resumes folder changed");
    },
    onError: (error: Error, patch) =>
      toast.error(couldnt("folder" in patch ? "use that folder" : "save this setting", error)),
  });

  return (
    <SettingCard
      id="resumes-folder"
      title="Resumes folder"
      description="Maestro saves a copy of each finished resume PDF here, so you can find it on your computer and in upload dialogs."
      errorTitle="Couldn't load the resumes folder."
      skeleton="h-11 w-full"
      query={setting}
    >
      {(data) =>
        !data.available ? (
          <p className="text-muted-foreground text-body-medium">
            {UNAVAILABLE[data.reason ?? "not_here"]}
          </p>
        ) : (
          <div className="grid gap-4">
            <SettingCardAction>
              <AutosaveStatus pending={save.isPending} failed={save.isError} />
            </SettingCardAction>
            <SwitchRow htmlFor={id} label="Save a copy of each resume">
              <Switch
                id={id}
                checked={data.enabled}
                onCheckedChange={(checked) => {
                  if (!save.isPending) save.mutate({ enabled: checked });
                }}
              />
            </SwitchRow>
            {data.enabled && (
              <FolderRow data={data} saving={save.isPending} onSave={(f) => save.mutateAsync({ folder: f })} />
            )}
            {/* A sentence the server wrote about the last failed copy, not a thrown error. */}
            {data.last_error && (
              <p className="text-destructive text-body-small">{sentence(data.last_error)}</p>
            )}
          </div>
        )
      }
    </SettingCard>
  );
}

function FolderRow({
  data,
  saving,
  onSave,
}: {
  data: ResumesFolderSettings;
  saving: boolean;
  onSave: (folder: string | null) => Promise<unknown>;
}) {
  const inputId = useId();
  // The typed path; null = the field is closed. A rejected path stays in the field to fix.
  const [draft, setDraft] = useState<string | null>(null);
  useLeaveGuard(draft !== null);

  const open = useMutation({
    mutationFn: () => apiFetch<void>(`${RESUMES_FOLDER_URL}/open`, { method: "POST" }),
    onError: (error: Error) => toast.error(couldnt("open the folder", error)),
  });

  const submit = (folder: string | null) => onSave(folder).then(() => setDraft(null), () => {});

  return (
    <div className="grid gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="min-w-0 text-body-medium wrap-anywhere">
          <CONCEPT_ICONS.resumesFolder
            className="text-muted-foreground mr-2 inline size-4 align-text-bottom"
            aria-hidden="true"
          />
          {data.folder_display}
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="outline" size="sm" pending={open.isPending} onClick={() => open.mutate()}>
            Open folder
          </Button>
          {draft === null && (
            <Button variant="outline" size="sm" onClick={() => setDraft(data.folder ?? "")}>
              Change folder
            </Button>
          )}
          {data.custom && draft === null && (
            <Button variant="ghost" size="sm" disabled={saving} onClick={() => void submit(null)}>
              Use the default
            </Button>
          )}
        </div>
      </div>
      {draft !== null && (
        <div className="grid gap-1.5">
          <Label htmlFor={inputId}>Folder path</Label>
          <Input
            id={inputId}
            value={draft}
            // readOnly, not disabled, while it saves: a disabled field drops focus to <body>.
            readOnly={saving}
            onChange={(event) => setDraft(event.target.value)}
          />
          <div className={ACTION_ROW}>
            <Button variant="ghost" onClick={() => setDraft(null)}>
              Cancel
            </Button>
            <Button
              focusableWhenDisabled
              disabled={saving || !draft.trim()}
              className="data-disabled:pointer-events-none data-disabled:opacity-50"
              onClick={() => void submit(draft.trim())}
            >
              Save
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
