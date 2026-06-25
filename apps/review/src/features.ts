/* Tools kept in the codebase but not shipped in the current pilot.
 *
 * Dev-aware on purpose: in `npm run dev` (import.meta.env.DEV === true) NOTHING
 * is hidden, so you keep developing all tools normally. In the built/deployed
 * app (DEV === false) the listed tools are greyed-out in the nav AND their
 * routes render the "not in this pilot" placeholder, so they can't be reached —
 * even by typing the URL.
 *
 * To bring a tool into the pilot, delete its key here and push (Render
 * auto-redeploys). No other change needed.
 */
export const HIDDEN_TOOLS: ReadonlySet<string> = import.meta.env.DEV
  ? new Set<string>()
  : new Set<string>(["reconcile", "reference"]);

export function isToolHidden(key: string): boolean {
  return HIDDEN_TOOLS.has(key);
}
