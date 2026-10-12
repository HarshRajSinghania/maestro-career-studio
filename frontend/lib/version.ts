export const FRONTEND_VERSION = process.env.NEXT_PUBLIC_APP_VERSION ?? "dev";

/**
 * True when the two images demonstrably disagree.
 *
 * A version starting with "dev" ("dev" for a local build, "dev-<sha>" for a
 * workflow_dispatch image) means "unknown, do not compare": those builds
 * float free of any release, so a mismatch is noise. This exists to catch
 * the failure SYSTEM.md §9 records — a frontend-only change shipped without
 * rebuilding the frontend image, which once made fixed UI look broken for a
 * whole review.
 */
export function imagesDisagree(frontend: string, backend: string | undefined): boolean {
  if (!backend) return false;
  if (frontend.startsWith("dev") || backend.startsWith("dev")) return false;
  return frontend !== backend;
}

function release(version: string): number[] | null {
  const parts = /^(\d+)\.(\d+)\.(\d+)$/.exec(version);
  return parts ? parts.slice(1).map(Number) : null;
}

/**
 * True when the page's JavaScript is an older release than the backend: the tab was
 * opened before an update. The web app and the backend update together in a uv
 * install, so this almost always means "reload", not "the update failed".
 * Compared as numbers ("0.10.0" sorts before "0.9.0" as text).
 */
export function pageIsOlder(frontend: string, backend: string | undefined): boolean {
  const page = release(frontend);
  const server = backend === undefined ? null : release(backend);
  if (!page || !server) return false;
  for (let i = 0; i < 3; i += 1) {
    if (page[i] !== server[i]) return page[i] < server[i];
  }
  return false;
}
