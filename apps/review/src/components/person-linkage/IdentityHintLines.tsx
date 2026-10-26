import type { PersonIdentityHint } from "../../types";

/** Quiet identity lines for picker / add-investor — no scores, no tiers. */
export default function IdentityHintLines({ hint }: { hint?: PersonIdentityHint }) {
  if (!hint) return null;
  const lines: { text: string; split?: boolean }[] = [];
  if (hint.split_flagged) {
    lines.push({ text: "Flagged: possibly two people in one entry", split: true });
  }
  if (hint.linked_count > 1) {
    lines.push({ text: `Reviewed identity family · ${hint.linked_count} entries` });
  } else if (hint.open_count > 0) {
    lines.push({
      text: `${hint.open_count} possible related ${hint.open_count === 1 ? "entry" : "entries"} awaiting review`,
    });
  }
  if (lines.length === 0) return null;
  return (
    <>
      {lines.map((line) => (
        <span className={line.split ? "pl-picker-hint pl-picker-hint-split" : "pl-picker-hint"} key={line.text}>
          {line.text}
        </span>
      ))}
    </>
  );
}
