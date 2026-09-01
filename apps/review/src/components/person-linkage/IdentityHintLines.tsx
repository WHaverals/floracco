import type { PersonIdentityHint } from "../../types";

/** Quiet identity lines for picker / add-investor — no scores, no tiers. */
export default function IdentityHintLines({ hint }: { hint?: PersonIdentityHint }) {
  if (!hint) return null;
  const lines: { text: string; split?: boolean }[] = [];
  if (hint.split_flagged) {
    lines.push({ text: "Flagged as possibly containing several people", split: true });
  }
  if (hint.linked_count > 1) {
    lines.push({ text: `Reviewed identity family · ${hint.linked_count} entered records` });
  } else if (hint.open_count > 0) {
    lines.push({
      text: `${hint.open_count} possible related record${hint.open_count === 1 ? "" : "s"} awaiting review`,
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
