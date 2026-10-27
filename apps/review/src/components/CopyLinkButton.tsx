import { useEffect, useState } from "react";

/* "Copy link" (UI review 2026-10-14, #44): the same small outline chip as "Hide
 * this record…", top right of a page's heading. It copies a clean link to what
 * the page shows (no editor or worklist state). If the browser refuses the
 * clipboard, the link is shown in a field to copy by hand. */
export default function CopyLinkButton({
  path,
  title = "Copy a link to this page",
  disabledReason,
}: {
  /** The page's path and query, e.g. "/reference/title/235". */
  path: string;
  title?: string;
  /** When set, the chip says so instead of copying (e.g. a link too long to send). */
  disabledReason?: string;
}) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
  const url = `${window.location.origin}${path}`;

  useEffect(() => setState("idle"), [path]);
  useEffect(() => {
    if (state !== "copied") return;
    const timer = window.setTimeout(() => setState("idle"), 2000);
    return () => window.clearTimeout(timer);
  }, [state]);

  const copy = () => {
    if (!navigator.clipboard?.writeText) {
      setState("failed");
      return;
    }
    navigator.clipboard.writeText(url).then(
      () => setState("copied"),
      () => setState("failed"),
    );
  };

  if (disabledReason) {
    return <span className="copy-link-chip is-disabled">{disabledReason}</span>;
  }
  return (
    <span className="copy-link">
      <button
        type="button"
        className={state === "copied" ? "copy-link-chip is-copied" : "copy-link-chip"}
        onClick={copy}
        title={title}
      >
        {state === "copied" ? "Link copied" : "Copy link"}
      </button>
      {state === "failed" && (
        <span className="copy-link-fallback">
          The browser did not allow copying. Copy the link from here:
          <input
            readOnly
            value={url}
            autoFocus
            onFocus={(event) => event.target.select()}
            aria-label="Link to this page"
          />
          <button type="button" className="link-like" onClick={() => setState("idle")}>
            Close
          </button>
        </span>
      )}
    </span>
  );
}
