import { useEffect, useState } from "react";
import { imageUrl, loadWordEntry } from "../api";
import type { WordEntryDetail } from "../types";
import { manuscriptImageCaption, manuscriptImageCountLabel } from "../utils/manuscriptImages";
import ManuscriptLightbox from "./ManuscriptLightbox";

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

  useEffect(() => {
    setLoading(true);
    setEntry(null);
    setError("");
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

  return (
    <div className="drawer-scrim" onClick={onClose}>
      <aside className="word-drawer" onClick={(event) => event.stopPropagation()}>
        <header className="word-drawer-head">
          <div>
            <p className="eyebrow">Word source · the authority</p>
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
            {entry.has_revisions && (
              <p className="word-drawer-note muted">
                This entry has tracked changes. The clean (revisions-accepted) reading text is shown
                here; the full editorial history lives in the Changes tool.
              </p>
            )}

            <section>
              <h4>Narrative</h4>
              <p className="reading-text narrative db-narrative">{entry.text}</p>
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
