import type { WordEntryImage } from "../types";

export function manuscriptImageCaption(image: WordEntryImage): string {
  const folioLabels = image.folios
    .map((folio) => [folio.folio, folio.page_position].filter(Boolean).join(" · "))
    .filter(Boolean);
  const parts = folioLabels.length > 0 ? [folioLabels.join(" · ")] : image.file ? [image.file] : [];
  if (image.needs_review) {
    parts.push("needs review");
  }
  return parts.join(" · ") || "manuscript folio";
}

export function manuscriptImageCountLabel(images: WordEntryImage[]): string {
  if (images.length === 0) {
    return "";
  }
  const folioLinks = images.reduce((total, image) => total + image.folios.length, 0);
  if (folioLinks > images.length) {
    return `${images.length} photo${images.length === 1 ? "" : "s"} · ${folioLinks} folio links`;
  }
  return String(images.length);
}
