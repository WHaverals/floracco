import { useEffect, useState } from "react";
import { imageUrl } from "../api";
import type { ReviewCase } from "../types";
import ManuscriptLightbox from "./ManuscriptLightbox";

function value(row: ReviewCase["row"], key: string): string {
  return String(row[key] ?? "");
}

export default function ImagePanel({ reviewCase }: { reviewCase: ReviewCase }) {
  const [activeIndex, setActiveIndex] = useState(0);
  const [isZoomed, setIsZoomed] = useState(false);
  const imageCount = reviewCase.image_paths.length;
  const path = reviewCase.image_paths[activeIndex];

  useEffect(() => {
    setActiveIndex(0);
    setIsZoomed(false);
  }, [reviewCase.row.review_id]);

  const previousImage = () => setActiveIndex((index) => Math.max(index - 1, 0));
  const nextImage = () => setActiveIndex((index) => Math.min(index + 1, imageCount - 1));

  const imageNav =
    imageCount > 1 ? (
      <>
        <button
          type="button"
          className="manuscript-lightbox-btn"
          disabled={activeIndex === 0}
          onClick={previousImage}
        >
          Previous
        </button>
        <button
          type="button"
          className="manuscript-lightbox-btn"
          disabled={activeIndex >= imageCount - 1}
          onClick={nextImage}
        >
          Next
        </button>
      </>
    ) : null;

  return (
    <section className="panel image-panel">
      <div className="panel-heading">
        <p className="eyebrow">Manuscript image</p>
        <h2>{path ? `Candidate page ${activeIndex + 1} of ${imageCount}` : "No image candidate"}</h2>
      </div>
      {path ? (
        <>
          <button className="image-button" onClick={() => setIsZoomed(true)} type="button">
            <img alt="Manuscript page candidate" src={imageUrl(path)} />
            <span>Click image to zoom</span>
          </button>
          {imageCount > 1 ? (
            <div className="image-controls">
              <button disabled={activeIndex === 0} onClick={previousImage} type="button">
                Previous image
              </button>
              <button disabled={activeIndex >= imageCount - 1} onClick={nextImage} type="button">
                Next image
              </button>
            </div>
          ) : null}
          <p className="muted">{value(reviewCase.row, "image_candidates_plain_language")}</p>
          {isZoomed ? (
            <ManuscriptLightbox
              src={imageUrl(path)}
              alt="Zoomed manuscript page candidate"
              label={`Image ${activeIndex + 1} of ${imageCount}`}
              toolbarExtra={imageNav}
              onClose={() => setIsZoomed(false)}
            />
          ) : null}
        </>
      ) : (
        <p className="empty-state">No image candidate is attached to this review row.</p>
      )}
    </section>
  );
}
