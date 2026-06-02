import type { HighlightValue } from "../types";
import { normalizedTerms, splitWithHighlights } from "../highlight";

export default function HighlightedText({ text, highlights }: { text: string; highlights: HighlightValue[] }) {
  const terms = normalizedTerms(highlights);
  if (!text || !terms.length) {
    return <>{text}</>;
  }

  const parts = splitWithHighlights(text, terms);
  return (
    <>
      {parts.map((part, index) => {
        if (!part.match) {
          return <span key={`${part.text}-${index}`}>{part.text}</span>;
        }
        return (
          <mark
            className={`text-highlight text-highlight-${part.match.status}`}
            key={`${part.text}-${index}`}
            title={part.match.label}
          >
            {part.text}
          </mark>
        );
      })}
    </>
  );
}
