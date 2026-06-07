import { useEffect, useMemo, useState } from "react";
import { imageUrl } from "../api";
import type { ReviewCase, WordEntryImage } from "../types";
import { manuscriptImageCaption } from "../utils/manuscriptImages";
import ManuscriptLightbox from "./ManuscriptLightbox";

/** Grouped scans from the payload; fall back to raw paths if grouping is empty. */
function imagesFor(reviewCase: ReviewCase): WordEntryImage[] {
  if (reviewCase.image_candidates?.length) {
    return reviewCase.image_candidates;
  }
  return reviewCase.image_paths.map((path) => ({
    path,
    file: path.split("/").pop() ?? path,
    role: null,
    needs_review: false,
    folios: [],
  }));
}

/**
 * Compact manuscript affordance shown in the Word-segment header. A small
 * thumbnail of the folio that opens the full-screen zoom/pan lightbox — the
 * cramped third compare panel never let you actually inspect the scan, and a
 * folio image is only useful when you can zoom in. Prev/next steps through the
 * scans of an opening spread inside the lightbox.
 */
export default function ManuscriptThumb({ reviewCase }: { reviewCase: ReviewCase }) {
  const images = useMemo(() => imagesFor(reviewCase), [reviewCase]);
  const [open, setOpen] = useState(false);
  const [index, setIndex] = useState(0);

  useEffect(() => {
    setOpen(false);
    setIndex(0);
  }, [reviewCase.row.review_id]);

  if (images.length === 0) {
    return null;
  }

  const count = images.length;
  const active = images[Math.min(index, count - 1)];
  const first = images[0];
  const previous = () => setIndex((i) => Math.max(i - 1, 0));
  const next = () => setIndex((i) => Math.min(i + 1, count - 1));

  const nav =
    count > 1 ? (
      <>
        <button type="button" className="img-nav-btn" disabled={index === 0} onClick={previous} aria-label="Previous scan">
          ◀
        </button>
        <span className="img-counter">
          {index + 1} / {count}
        </span>
        <button type="button" className="img-nav-btn" disabled={index >= count - 1} onClick={next} aria-label="Next scan">
          ▶
        </button>
      </>
    ) : null;

  return (
    <>
      <button
        type="button"
        className="manuscript-thumb"
        onClick={() => {
          setIndex(0);
          setOpen(true);
        }}
        title="Open manuscript — zoom and pan"
      >
        <img src={imageUrl(first.path)} alt={manuscriptImageCaption(first)} loading="lazy" />
        <span className="manuscript-thumb-label">
          Manuscript{count > 1 ? ` · ${count}` : ""}
          <span className="manuscript-thumb-zoom">⤢ inspect</span>
        </span>
      </button>
      {open ? (
        <ManuscriptLightbox
          src={imageUrl(active.path)}
          alt={manuscriptImageCaption(active)}
          label={manuscriptImageCaption(active)}
          toolbarExtra={nav}
          onClose={() => setOpen(false)}
        />
      ) : null}
    </>
  );
}
