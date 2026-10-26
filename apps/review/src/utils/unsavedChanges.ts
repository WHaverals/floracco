import { useEffect, useRef } from "react";

/* Unsaved reviewer input (UI review 2026-10-14, item 3). An editor that holds
 * typed, unsaved text registers here while it is dirty. The router guard
 * (components/UnsavedChangesGuard) asks before a navigation would unmount it,
 * a parent asks before closing an editor in place (confirmDiscard), and closing
 * or reloading the tab gets the browser's own question. Initials alone never
 * count as unsaved. Same shape as utils/escapeLayers.ts: one module-level set
 * plus one lazily added window listener. */
type Entry = { label: () => string; scopes: string[] };
const entries = new Set<Entry>();
let listening = false;

function onBeforeUnload(event: BeforeUnloadEvent) {
  if (entries.size === 0) return;
  event.preventDefault();
  event.returnValue = ""; // older Chromium needs it; the browser shows its own text
}

/** Register `label` as unsaved while `dirty` is true. `scopes` lets a parent ask
 * about exactly the editors it is about to close (see confirmDiscard). */
export function useUnsavedChange(dirty: boolean, label: string, scopes: string[] = []): void {
  const labelRef = useRef(label);
  labelRef.current = label;
  const scopeKey = scopes.join("\u0000");
  useEffect(() => {
    if (!dirty) return;
    if (!listening) {
      window.addEventListener("beforeunload", onBeforeUnload);
      listening = true;
    }
    const entry: Entry = { label: () => labelRef.current, scopes: scopeKey ? scopeKey.split("\u0000") : [] };
    entries.add(entry);
    return () => {
      entries.delete(entry);
    };
  }, [dirty, scopeKey]);
}

export function unsavedLabels(scope?: string): string[] {
  return [...new Set([...entries].filter((e) => !scope || e.scopes.includes(scope)).map((e) => e.label()))];
}

const listed = (l: string[]) => (l.length === 1 ? l[0] : `${l.slice(0, -1).join(", ")} and ${l[l.length - 1]}`);

export function leaveQuestion(labels: string[]): string {
  return labels.length === 1
    ? `You have an unsaved change to ${labels[0]}. Leave without saving?`
    : `You have unsaved changes to ${listed(labels)}. Leave without saving them?`;
}

/** Before closing an editor in place: true when nothing in `scope` is unsaved,
 * or the reviewer agrees to discard it. */
export function confirmDiscard(scope: string): boolean {
  const labels = unsavedLabels(scope);
  if (labels.length === 0) return true;
  return window.confirm(
    labels.length === 1
      ? `You have an unsaved change to ${labels[0]}. Discard it?`
      : `You have unsaved changes to ${listed(labels)}. Discard them?`,
  );
}

/** navigate(to, LEAVE_UNSAVED): a navigation that follows a save or a deliberate
 * Cancel, so the guard does not ask. */
export const LEAVE_UNSAVED = { state: { leaveUnsaved: true } };
