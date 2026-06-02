import { useEffect, useState } from "react";
import { imageUrl, loadWordEntry } from "../api";
import type { WordEntryDetail } from "../types";

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
  const [zoomed, setZoomed] = useState<string | null>(null);

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
      if (event.key === "Escape") {
        if (zoomed) setZoomed(null);
        else onClose();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, zoomed]);

  const meta = entry
    ? [entry.date, entry.folio, entry.register_id].filter(Boolean).join(" · ")
    : "";

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
              <h4>Manuscript {entry.images.length > 0 ? `(${entry.images.length})` : ""}</h4>
              {entry.images.length === 0 && (
                <p className="muted">No manuscript image is linked to this entry.</p>
              )}
              <div className="word-drawer-images">
                {entry.images.map((img) => (
                  <figure key={img.path} className="word-drawer-figure">
                    <button
                      type="button"
                      className="image-zoom-button"
                      onClick={() => setZoomed(img.path)}
                    >
                      <img src={imageUrl(img.path)} alt={img.file || "manuscript folio"} />
                    </button>
                    <figcaption>
                      {[img.folio, img.page_position].filter(Boolean).join(" · ")}
                      {img.needs_review ? " · needs review" : ""}
                    </figcaption>
                  </figure>
                ))}
              </div>
            </section>
          </div>
        )}
      </aside>

      {zoomed && (
        <div className="image-lightbox" onClick={() => setZoomed(null)}>
          <img src={imageUrl(zoomed)} alt="manuscript folio enlarged" />
        </div>
      )}
    </div>
  );
}
