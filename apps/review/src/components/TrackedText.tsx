import { useEffect, useMemo, useRef, useState } from "react";
import type {
  EntryComment,
  EntryNote,
  HighlightValue,
  RevisionChange,
  RevisionChangeKind,
  RevisionToken,
  WordEntryRich,
} from "../types";
import { normalizedTerms, splitWithHighlights } from "../highlight";

const KIND_CLASS: Record<RevisionChangeKind, string> = {
  insertion: "rev-ins",
  deletion: "rev-del",
  move_from: "rev-movefrom",
  move_to: "rev-moveto",
};

const KIND_VERB: Record<RevisionChangeKind, string> = {
  insertion: "Inserted",
  deletion: "Deleted",
  move_from: "Moved away from here",
  move_to: "Moved here",
};

function shortDate(value: string | null | undefined): string {
  if (!value) {
    return "";
  }
  const match = /^(\d{4}-\d{2}-\d{2})/.exec(value);
  return match ? match[1] : value;
}

function changeTooltip(changes: RevisionChange[]): string {
  return changes
    .map((change) => {
      const who = change.author ? `by ${change.author}` : "(author unknown)";
      const when = shortDate(change.date);
      return `${KIND_VERB[change.kind]} ${who}${when ? `, ${when}` : ""}`;
    })
    .join("; ");
}

function moveId(changes: RevisionChange[]): string | null {
  const move = changes.find((change) => change.kind === "move_from" || change.kind === "move_to");
  return move?.id ?? null;
}

function isHiddenInClean(token: Extract<RevisionToken, { type: "text" }>): boolean {
  return token.changes.some((change) => change.kind === "deletion" || change.kind === "move_from");
}

type MarkerRef = { type: "comment" | "note"; id: string } | null;

export default function TrackedText({
  rich,
  highlights,
  mode,
}: {
  rich: WordEntryRich;
  highlights: HighlightValue[];
  mode: "clean" | "tracked";
}) {
  const terms = useMemo(() => normalizedTerms(highlights), [highlights]);
  const commentsById = useMemo(
    () => new Map(rich.comments.map((comment) => [comment.id, comment])),
    [rich.comments],
  );
  const notesById = useMemo(() => new Map(rich.notes.map((note) => [note.id, note])), [rich.notes]);

  const [activeMarker, setActiveMarker] = useState<MarkerRef>(null);
  const [hoverMoveId, setHoverMoveId] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!activeMarker) {
      return;
    }
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setActiveMarker(null);
      }
    };
    const onClick = (event: MouseEvent) => {
      const target = event.target as Node;
      if (containerRef.current && !containerRef.current.contains(target)) {
        setActiveMarker(null);
      }
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onClick);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onClick);
    };
  }, [activeMarker]);

  const renderTextToken = (token: Extract<RevisionToken, { type: "text" }>, key: number) => {
    if (mode === "clean" && isHiddenInClean(token)) {
      return null;
    }
    const parts = splitWithHighlights(token.text, terms);
    const inner = parts.map((part, partIndex) =>
      part.match ? (
        <mark
          className={`text-highlight text-highlight-${part.match.status}`}
          key={partIndex}
          title={part.match.label}
        >
          {part.text}
        </mark>
      ) : (
        <span key={partIndex}>{part.text}</span>
      ),
    );

    const classes: string[] = [];
    let tooltip = "";
    const tokenMoveId = moveId(token.changes);
    if (mode === "tracked" && token.changes.length) {
      for (const change of token.changes) {
        classes.push(KIND_CLASS[change.kind]);
      }
      tooltip = changeTooltip(token.changes);
    }
    if (token.comment_ids.length) {
      classes.push("rev-commented");
    }
    if (tokenMoveId && tokenMoveId === hoverMoveId) {
      classes.push("rev-move-active");
    }

    if (!classes.length) {
      return <span key={key}>{inner}</span>;
    }
    return (
      <span
        key={key}
        className={classes.join(" ")}
        title={tooltip || undefined}
        aria-label={tooltip || undefined}
        data-move-id={tokenMoveId ?? undefined}
        onMouseEnter={tokenMoveId ? () => setHoverMoveId(tokenMoveId) : undefined}
        onMouseLeave={tokenMoveId ? () => setHoverMoveId(null) : undefined}
      >
        {inner}
      </span>
    );
  };

  const renderCommentMarker = (id: string | null, key: number) => {
    const comment = id ? commentsById.get(id) : undefined;
    const isOpen = activeMarker?.type === "comment" && activeMarker.id === id;
    const label = comment
      ? `Comment${comment.author ? ` by ${comment.author}` : ""}: ${comment.text ?? ""}`
      : "Comment (body not found)";
    return (
      <span className="rev-marker-wrap" key={key}>
        <button
          type="button"
          className={`rev-marker rev-marker-comment${isOpen ? " is-open" : ""}`}
          aria-label={label}
          aria-expanded={isOpen}
          title={label}
          onClick={() => setActiveMarker(isOpen || !id ? null : { type: "comment", id })}
        >
          <span aria-hidden="true">💬</span>
        </button>
        {isOpen && comment ? (
          <span className="rev-popover" role="dialog">
            <span className="rev-popover-head">
              {comment.author || "Unknown author"}
              {comment.date ? ` · ${shortDate(comment.date)}` : ""}
              {comment.initials ? ` · ${comment.initials}` : ""}
            </span>
            <span className="rev-popover-body">{comment.text || "(empty comment)"}</span>
          </span>
        ) : null}
      </span>
    );
  };

  const renderNoteMarker = (id: string | null, kind: "footnote" | "endnote", key: number) => {
    const note = id ? notesById.get(id) : undefined;
    const isOpen = activeMarker?.type === "note" && activeMarker.id === id;
    const symbol = kind === "endnote" ? `[${id ?? "?"}]` : id ?? "?";
    const label = note ? `${kind} ${id}: ${note.text ?? ""}` : `${kind} (body not found)`;
    return (
      <span className="rev-marker-wrap" key={key}>
        <sup>
          <button
            type="button"
            className={`rev-marker rev-marker-note${isOpen ? " is-open" : ""}`}
            aria-label={label}
            aria-expanded={isOpen}
            title={label}
            onClick={() => setActiveMarker(isOpen || !id ? null : { type: "note", id })}
          >
            {symbol}
          </button>
        </sup>
        {isOpen && note ? (
          <span className="rev-popover" role="dialog">
            <span className="rev-popover-head">{kind === "footnote" ? "Footnote" : "Endnote"} {id}</span>
            <span className="rev-popover-body">{note.text || "(empty note)"}</span>
          </span>
        ) : null}
      </span>
    );
  };

  const nodes = rich.tokens.map((token, index) => {
    switch (token.type) {
      case "text":
        return renderTextToken(token, index);
      case "break":
        return <br key={index} />;
      case "tab":
        return <span className="rev-tab" key={index} />;
      case "comment_ref":
        return renderCommentMarker(token.id, index);
      case "note_ref":
        return renderNoteMarker(token.id, token.kind, index);
      default:
        return null;
    }
  });

  return (
    <div className="tracked-text" ref={containerRef}>
      <article className="reading-text narrative">{nodes}</article>
      {rich.comments.length ? <CommentsDisclosure comments={rich.comments} /> : null}
      {rich.notes.length ? <NotesDisclosure notes={rich.notes} /> : null}
    </div>
  );
}

function CommentsDisclosure({ comments }: { comments: EntryComment[] }) {
  return (
    <details className="rev-disclosure">
      <summary>Comments ({comments.length})</summary>
      <ul className="rev-body-list">
        {comments.map((comment) => (
          <li key={comment.id}>
            <span className="rev-body-meta">
              💬 {comment.author || "Unknown"}
              {comment.date ? ` · ${shortDate(comment.date)}` : ""}
            </span>
            <span>{comment.text || "(empty comment)"}</span>
          </li>
        ))}
      </ul>
    </details>
  );
}

function NotesDisclosure({ notes }: { notes: EntryNote[] }) {
  return (
    <details className="rev-disclosure">
      <summary>Notes ({notes.length})</summary>
      <ul className="rev-body-list">
        {notes.map((note) => (
          <li key={`${note.kind}-${note.id}`}>
            <span className="rev-body-meta">
              {note.kind === "footnote" ? "Footnote" : "Endnote"} {note.id}
            </span>
            <span>{note.text || "(empty note)"}</span>
          </li>
        ))}
      </ul>
    </details>
  );
}
