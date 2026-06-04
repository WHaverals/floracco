import type { ReviewCase } from "../types";

export default function WordActBadges({ reviewCase }: { reviewCase: ReviewCase }) {
  const components = reviewCase.act_components ?? [];
  if (components.length < 2) {
    return null;
  }
  return (
    <span className="word-act-badge-row" aria-label="Act labels in this Word entry">
      {components.map((component, index) => (
        <span className="act-chip-sm act-chip-word" key={`${component.label_display}-${index}`} title={component.label_display}>
          {component.label_display}
        </span>
      ))}
    </span>
  );
}
