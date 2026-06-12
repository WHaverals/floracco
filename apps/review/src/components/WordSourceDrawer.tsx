import { useEffect, useState } from "react";
import { imageUrl, loadWordEntry } from "../api";
import type { WordEntryDetail } from "../types";
import { manuscriptImageCaption, manuscriptImageCountLabel } from "../utils/manuscriptImages";
import ManuscriptLightbox from "./ManuscriptLightbox";
import TrackedText from "./TrackedText";

export default function WordSourceDrawer({
  sourceEntryId,
  onClose,
}: {
  sourceEntryId: string;
  onClose: () => void;
}) {
  const [entry, setEntry] = useState<WordEntryDetail | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [lightboxPath, setLightboxPath] = useState<string | null>(null);
  const [mode, setMode] = useState<"clean" | "tracked">("clean");

  useEffect(() => {
    setLoading(true);
    setEntry(null);
    setError("");
    setMode("clean");
    loadWordEntry(sourceEntryId)
      .then(setEntry)
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false));
  }, [sourceEntryId]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !lightboxPath) {
        onClose();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, lightboxPath]);

  const meta = entry
    ? [entry.date, entry.folio, entry.register_id].filter(Boolean).join(" · ")
    : "";
  const manuscriptLabel = entry ? manuscriptImageCountLabel(entry.images) : "";
  const rich = entry?.rich ?? null;
  // Comments and tracked changes are part of the frozen Word evidence and must
  // be visible here (decision 2026-06-11) — not hidden behind a separate tool.
  const hasEditorialRecord = Boolean(
    rich && (rich.has_revisions || rich.comments.length > 0 || rich.notes.length > 0),
  );
  const summaryParts = rich
    ? [
        rich.summary.insertions ? `${rich.summary.insertions} insertion${rich.summary.insertions === 1 ? "" : "s"}` : "",
        rich.summary.deletions ? `${rich.summary.deletions} deletion${rich.summary.deletions === 1 ? "" : "s"}` : "",
        rich.summary.moves ? `${rich.summary.moves} move${rich.summary.moves === 1 ? "" : "s"}` : "",
        rich.comments.length ? `${rich.comments.length} comment${rich.comments.length === 1 ? "" : "s"}` : "",
        rich.notes.length ? `${rich.notes.length} note${rich.notes.length === 1 ? "" : "s"}` : "",
      ].filter(Boolean)
    : [];

  return (
    <div className="drawer-scrim" onClick={onClose}>
      <aside className="word-drawer" onClick={(event) => event.stopPropagation()}>
        <header className="word-drawer-head">
          <div>
            <p className="eyebrow">Word summary · frozen source</p>
            <h3>{entry?.label || sourceEntryId}</h3>
            {meta && <p className="muted word-drawer-meta">{meta}</p>}
          </div>
          <button type="button" className="drawer-close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </header>

        {loading && <p className="muted">Loading Word entry…</p>}
        {error && !loading && <p className="error-text">{error}</p>}

        {entry && !loading && (
          <div className="word-drawer-body">
            <code className="db-row-id">{entry.source_entry_id}</code>

            <section>
              <div className="word-drawer-narrative-head">
                <h4>Narrative</h4>
                {hasEditorialRecord && (
                  <div className="word-mode-toggle" role="group" aria-label="Reading mode">
                    <button
                      type="button"
                      className={mode === "clean" ? "mode-chip is-active" : "mode-chip"}
                      onClick={() => setMode("clean")}
                    >
                      Clean
                    </button>
                    <button
                      type="button"
                      className={mode === "tracked" ? "mode-chip is-active" : "mode-chip"}
                      onClick={() => setMode("tracked")}
                    >
                      Tracked changes
                    </button>
                  </div>
                )}
              </div>
              {hasEditorialRecord && (
                <p className="word-drawer-note muted">
                  This summary carries an editorial record ({summaryParts.join(", ")}). The Word file
                  itself stays frozen; this is its history, shown as evidence.
                </p>
              )}
              {hasEditorialRecord && rich ? (
                <TrackedText rich={rich} highlights={[]} mode={mode} />
              ) : (
                <p className="reading-text narrative db-narrative">{entry.text}</p>
              )}
            </section>

            <section>
              <h4>
                Manuscript
                {manuscriptLabel ? ` (${manuscriptLabel})` : ""}
              </h4>
              {entry.images.length === 0 && (
                <p className="muted">No manuscript image is linked to this entry.</p>
              )}
              <div className="word-drawer-images">
                {entry.images.map((img) => (
                  <figure key={img.path} className="word-drawer-figure">
                    <button
                      type="button"
                      className="image-zoom-button"
                      onClick={() => setLightboxPath(img.path)}
                    >
                      <img src={imageUrl(img.path)} alt={manuscriptImageCaption(img)} />
                    </button>
                    <figcaption>{manuscriptImageCaption(img)}</figcaption>
                  </figure>
                ))}
              </div>
            </section>
          </div>
        )}
      </aside>

      {lightboxPath && (
        <ManuscriptLightbox
          src={imageUrl(lightboxPath)}
          alt="Manuscript folio enlarged"
          onClose={() => setLightboxPath(null)}
        />
      )}
    </div>
  );
}
