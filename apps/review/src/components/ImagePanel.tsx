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

export default function ImagePanel({ reviewCase }: { reviewCase: ReviewCase }) {
  const images = useMemo(() => imagesFor(reviewCase), [reviewCase]);
  const [activeIndex, setActiveIndex] = useState(0);
  const [isZoomed, setIsZoomed] = useState(false);
  const count = images.length;

  useEffect(() => {
    setActiveIndex(0);
    setIsZoomed(false);
  }, [reviewCase.row.review_id]);

  if (count === 0) {
    return (
      <section className="panel image-panel">
        <div className="image-panel-head">
          <p className="eyebrow">Manuscript</p>
        </div>
        <p className="empty-state">No image candidate is attached to this review row.</p>
      </section>
    );
  }

  const active = images[Math.min(activeIndex, count - 1)];
  const previous = () => setActiveIndex((index) => Math.max(index - 1, 0));
  const next = () => setActiveIndex((index) => Math.min(index + 1, count - 1));

  const nav =
    count > 1 ? (
      <>
        <button type="button" className="img-nav-btn" disabled={activeIndex === 0} onClick={previous} aria-label="Previous scan">
          ◀
        </button>
        <span className="img-counter">
          {activeIndex + 1} / {count}
        </span>
        <button type="button" className="img-nav-btn" disabled={activeIndex >= count - 1} onClick={next} aria-label="Next scan">
          ▶
        </button>
      </>
    ) : null;

  return (
    <section className="panel image-panel">
      <div className="image-panel-head">
        <p className="eyebrow">Manuscript</p>
        {nav}
      </div>
      <button className="image-button" onClick={() => setIsZoomed(true)} type="button" title="Click to zoom">
        <img alt={manuscriptImageCaption(active)} src={imageUrl(active.path)} />
      </button>
      <p className="image-caption">{manuscriptImageCaption(active)}</p>
      {isZoomed ? (
        <ManuscriptLightbox
          src={imageUrl(active.path)}
          alt={manuscriptImageCaption(active)}
          label={count > 1 ? `Scan ${activeIndex + 1} of ${count}` : undefined}
          toolbarExtra={nav}
          onClose={() => setIsZoomed(false)}
        />
      ) : null}
    </section>
  );
}
