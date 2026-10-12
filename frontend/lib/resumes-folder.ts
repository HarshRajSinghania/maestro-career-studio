import type { ResumesFolderSettings } from "@/lib/types";

export const RESUMES_FOLDER_KEY = ["settings", "resumes-folder"] as const;
export const RESUMES_FOLDER_URL = "/api/settings/resumes-folder";

/** The OS file manager's name, for "Show in …" buttons. */
export function revealLabel(platform: ResumesFolderSettings["platform"]): string {
  if (platform === "macos") return "Show in Finder";
  if (platform === "windows") return "Show in File Explorer";
  return "Show in folder";
}

/** True when this machine saves copies and the setting is on. */
export function copiesOn(data: ResumesFolderSettings | undefined): boolean {
  return !!data && data.available && data.enabled;
}
