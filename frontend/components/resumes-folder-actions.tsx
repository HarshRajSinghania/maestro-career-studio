"use client";

import { useRef } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { X } from "lucide-react";
import { toast } from "sonner";

import { GuardedLink } from "@/components/guarded-link";
import { Button } from "@/components/ui/button";
import { useFocusHandoff } from "@/hooks/use-focus-return";
import { parseFlag, serializeFlag, useLocalStorageState } from "@/hooks/use-local-storage-state";
import { useSingleFlight } from "@/hooks/use-single-flight";
import { apiFetch } from "@/lib/api";
import { CONCEPT_ICONS } from "@/lib/concept-icons";
import { couldnt } from "@/lib/error-text";
import { copiesOn, RESUMES_FOLDER_KEY, RESUMES_FOLDER_URL, revealLabel } from "@/lib/resumes-folder";
import { anchorHref } from "@/lib/settings-tabs";
import type { ResumesFolderSettings } from "@/lib/types";

const NOTE_DISMISSED_KEY = "maestro-cs:resumes-folder-note-dismissed";

/** The resumes-folder setting, shared by every surface that offers the copy (one query key, one cache). */
export function useResumesFolder() {
  return useQuery({
    queryKey: RESUMES_FOLDER_KEY,
    queryFn: () => apiFetch<ResumesFolderSettings>(RESUMES_FOLDER_URL),
    staleTime: 60_000,
  });
}

type ShowCopyButtonProps = {
  applicationId: string;
  variant?: "outline" | "ghost" | "link";
  size?: "default" | "sm" | "icon-sm";
  /** The label moves to `aria-label` and only the folder glyph shows. */
  iconOnly?: boolean;
  disabled?: boolean;
  className?: string;
};

/** Reveals this application's saved PDF copy in the OS file manager. Nothing when copies are off. */
export function ShowCopyButton({
  applicationId,
  variant = "outline",
  size = "default",
  iconOnly = false,
  disabled = false,
  className,
}: ShowCopyButtonProps) {
  const { data } = useResumesFolder();
  const reveal = useMutation({
    mutationFn: () => apiFetch<{ path: string }>(`/api/applications/${applicationId}/show-copy`, { method: "POST" }),
    onError: (error: Error) => toast.error(couldnt("open the folder", error)),
  });
  // A double click opened two file-manager windows.
  const revealOnce = useSingleFlight(reveal.mutate);
  if (!copiesOn(data)) return null;
  const label = revealLabel(data!.platform);
  return (
    <Button
      variant={variant}
      size={size}
      className={className}
      aria-label={iconOnly ? label : undefined}
      pending={reveal.isPending}
      disabled={disabled}
      onClick={() => revealOnce(undefined)}
    >
      <CONCEPT_ICONS.resumesFolder className="size-4" aria-hidden="true" />
      {iconOnly ? null : label}
    </Button>
  );
}

/** A one-time line under the PDF card's buttons that says where the copy went. */
export function ResumesFolderNote({ applicationId }: { applicationId: string }) {
  const { data } = useResumesFolder();
  const [dismissed, setDismissed] = useLocalStorageState(NOTE_DISMISSED_KEY, parseFlag, serializeFlag);
  const noteRef = useRef<HTMLDivElement>(null);
  // Dismiss removes the focused button: focus goes to the nearest control that stays.
  useFocusHandoff(noteRef);
  if (!copiesOn(data) || dismissed) return null;
  return (
    <div
      ref={noteRef}
      className="text-muted-foreground flex flex-wrap items-center gap-x-2 gap-y-1 rounded-corner-md bg-surface-container-low py-1.5 pr-1.5 pl-3 text-body-small"
    >
      <span>A copy is saved in {data!.folder_display}.</span>
      <ShowCopyButton
        applicationId={applicationId}
        variant="link"
        size="sm"
        className="h-auto px-0 py-0 text-body-small"
      />
      <GuardedLink
        href={anchorHref("/settings", "resumes-folder")}
        className="text-primary underline underline-offset-4"
      >
        Change folder
      </GuardedLink>
      <Button
        variant="ghost"
        size="icon-sm"
        aria-label="Dismiss"
        className="ml-auto"
        onClick={() => setDismissed(true)}
      >
        <X className="size-4" />
      </Button>
    </div>
  );
}
