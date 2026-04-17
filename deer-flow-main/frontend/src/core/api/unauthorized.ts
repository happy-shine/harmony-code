/**
 * Centralized 401 handling for harmony API calls.
 *
 * All hand-written ``fetch`` wrappers under ``core/*`` (threads, memory,
 * MCP, skills) import :func:`handleUnauthorized` and call it before
 * ``response.ok`` branches. On 401 we assume the session cookie expired
 * or was never set and bounce the user to ``/login`` — which is nicer
 * than the runtime "Create thread failed: 401" overlay.
 *
 * Idempotent: a guard flag prevents a cascade of in-flight requests
 * from each trying to navigate. The first 401 wins; the rest silently
 * short-circuit their promises via the thrown sentinel.
 */

const LOGIN_PATH = "/login";

let redirecting = false;

export class UnauthorizedError extends Error {
  constructor() {
    super("unauthorized");
    this.name = "UnauthorizedError";
  }
}

/** Called from ``fetch`` wrappers. Returns true if the response was a 401
 *  (caller should stop processing). When triggered, schedules a navigation
 *  to ``/login``, preserving the current URL so the login page can bounce
 *  the user back after sign-in. */
export function handleUnauthorized(res: Response): boolean {
  if (res.status !== 401) return false;
  if (typeof window === "undefined") return true; // SSR — caller will throw
  if (redirecting) return true;
  redirecting = true;
  // Avoid looping if the /login page itself 401s.
  if (window.location.pathname.startsWith(LOGIN_PATH)) {
    redirecting = false;
    return true;
  }
  const next = window.location.pathname + window.location.search;
  // Hard nav rather than Next router.push — by the time a 401 surfaces
  // we're often inside a failing query or mutation, and a full reload
  // guarantees the stale fetch doesn't race the login page mount.
  window.location.href = `${LOGIN_PATH}?next=${encodeURIComponent(next)}`;
  return true;
}
