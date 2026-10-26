import { useEffect } from "react";
import { NavigationType, useBlocker, type BlockerFunction } from "react-router-dom";
import { leaveQuestion, unsavedLabels } from "../utils/unsavedChanges";

const shouldBlock: BlockerFunction = ({ currentLocation, nextLocation, historyAction }) => {
  // A navigation right after a save or a deliberate Cancel. History entries keep
  // their state, so Back/Forward never counts as one.
  if (
    historyAction !== NavigationType.Pop &&
    (nextLocation.state as { leaveUnsaved?: boolean } | null)?.leaveUnsaved
  )
    return false;
  // Same page with the record's ?fix and ?inv unchanged (e.g. the Needs-review
  // toggle): the open record and its editors stay mounted.
  if (currentLocation.pathname === nextLocation.pathname) {
    const a = new URLSearchParams(currentLocation.search);
    const b = new URLSearchParams(nextLocation.search);
    if (a.get("fix") === b.get("fix") && a.get("inv") === b.get("inv")) return false;
  }
  return unsavedLabels().length > 0;
};

/** The app's one navigation blocker (a router supports only one): asks before
 * a link, a record switch or Back/Forward would discard typed, unsaved input. */
export default function UnsavedChangesGuard() {
  const blocker = useBlocker(shouldBlock);
  useEffect(() => {
    if (blocker.state !== "blocked") return;
    const labels = unsavedLabels();
    if (labels.length === 0 || window.confirm(leaveQuestion(labels))) setTimeout(blocker.proceed, 0);
    else blocker.reset();
  }, [blocker]);
  return null;
}
