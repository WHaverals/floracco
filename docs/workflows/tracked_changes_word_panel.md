# Design spec: tracked-changes Word panel

Status: **implemented (Phases 1–4)**, Clean-by-default. Owner-facing UX decisions
marked **FT review pending** (e.g. the default mode) can still be revisited.
Remaining: Phase 5 polish (keyboard pass, AA contrast audit) and §10 future work.

This specifies how the review platform's Word panel should surface the editorial
history of a Word source entry — insertions, deletions, moves, comments, and
footnotes — for a non-technical scholarly reviewer working interpretively. It
covers the data contract (pipeline → server → app), the render rules, the UX,
edge cases, and a phased build plan.

Related: [qa_packet_schema.md](qa_packet_schema.md) (the packet contract),
[README.md](README.md) (workflow + interpretive layers).

---

## 1. Goal and audience

The reviewer (FT) reads a contract narrative to decide what it says and whether
the database mirrors it. The Word narrative is **authoritative**, and its
tracked changes *are part of the evidence*: an inserted clause, a struck-out
name, a moved paragraph, or a "[unclear]" comment can change the historical
reading. Today the panel shows only the flattened clean text, so that evidence
is invisible.

Design priorities, in order:

1. **Faithful** to the Word editorial record (no silent loss; deletions never
   leak into the clean reading or into matched text).
2. **Readable** — a historian should be able to read the act without fighting
   markup; markup aids interpretation, it does not clutter it.
3. **Transparent about provenance** — who changed what, when — available on
   demand, never noisy by default.
4. **Transparent about the pipeline** — make clear that matching used the *clean
   current* text, so a reviewer understands what the algorithm "saw."
5. **Accessible** — never encode meaning in color alone.

---

## 2. Data available (today)

| Source | Field | Content |
|--------|-------|---------|
| `04_source_entries/source_entries.jsonl` | `current_text` | Clean reading text (deleted text already excluded). |
| | `revision_aware_text` | Inline-marker string (grammar below). |
| | `has_revisions`, `comment_ids`, `footnote_ids`, `endnote_ids` | Flags / id references. |
| `03_extracted_registers/comments.jsonl` | comment rows | `comment_id`, `author`, `date`, `initials`, `text`, `revision_aware_text`. |
| `03_extracted_registers/footnotes.jsonl` | note rows | `note_id`, `note_kind`, `text`, `revision_aware_text`. |
| `03_extracted_registers/revisions.jsonl` | revision rows | `revision_kind`, `revision_id`, `author`, `date`, `text`. |

### 2.1 `revision_aware_text` marker grammar

Produced by `revision_aware_text()` in `workflows/word_pipeline.py`. It is plain
text with these inline markers (ids/authors/dates are XML attribute values):

```
<INS id="12" author="FT" date="2024-03-01T..">inserted text</INS>
<DEL id="13" author="FT" date="..">deleted text</DEL>
<MOVEFROM id="14" author=".." date="..">moved-away text</MOVEFROM>
<MOVETO id="14" author=".." date="..">moved-here text</MOVETO>
<COMMENT_START id="3"> ... commented span ... <COMMENT_END id="3">
<COMMENT_REF id="3">
<FOOTNOTE_REF id="5">
<ENDNOTE_REF id="2">
```

Plus literal `\n` (paragraph/line break) and `\t` (tab). Notes:

- `INS`/`DEL`/`MOVEFROM`/`MOVETO` can **nest** (e.g. an insertion inside a
  deletion = a correction of a correction); the parser must preserve nesting and
  record every enclosing change on a token.
- A **comment** is a *range* (`COMMENT_START` … `COMMENT_END`) plus a reference
  point (`COMMENT_REF`). Ranges can span paragraph breaks and can overlap other
  ranges; the parser tracks a set of open comment ids.
- Move pairs share a `revision_id` across `MOVEFROM`/`MOVETO`.

---

## 3. Data contract (the recommended shape)

**Recommendation: parse once in Python, render dumb JSON in the browser.** Do
*not* re-parse pseudo-XML in TypeScript (fragile, duplicated, hard to test).
Concretely:

- **Pipeline (`qa-packet`)** embeds the compact raw string + the referenced
  bodies, so the packet stays self-contained and small:

  | New QA-packet field | Type | Meaning |
  |---------------------|------|---------|
  | `word_entry_revision_text` | string | The `revision_aware_text` for the entry (marker string above). |
  | `word_entry_has_revisions` | bool | From `has_revisions`. |
  | `word_entry_revision_summary` | object | `{insertions, deletions, moves, comments, footnotes}` counts. |
  | `word_entry_comments` | array | `[{id, author, date, initials, text}]` for the entry's `comment_ids`. |
  | `word_entry_notes` | array | `[{id, kind, text}]` for footnote/endnote ids. |

  (Keep the existing `word_entry_text` = clean text for back-compat; bump the QA
  packet `schema_version` to 2.)

- **Server (`review_server.case_payload`)** parses `word_entry_revision_text`
  into a **token stream** and returns clean JSON to the app. Parsing lives in one
  testable Python function (`parse_revision_segments`). The token model:

```jsonc
// word_entry_rich: served by the API, not stored in the packet
{
  "has_revisions": true,
  "summary": { "insertions": 3, "deletions": 1, "moves": 0, "comments": 2, "footnotes": 1 },
  "tokens": [
    { "type": "text", "text": "Compagnia di ", "changes": [], "comment_ids": [], "note_ref": null },
    { "type": "text", "text": "Bartolomeo",
      "changes": [{ "kind": "insertion", "id": "12", "author": "FT", "date": "2024-03-01" }],
      "comment_ids": ["3"], "note_ref": null },
    { "type": "text", "text": "Giovanni",
      "changes": [{ "kind": "deletion", "id": "13", "author": "FT", "date": "2024-03-01" }],
      "comment_ids": [], "note_ref": null },
    { "type": "note_ref", "note": { "kind": "footnote", "id": "5" } },
    { "type": "break" }
  ],
  "comments": [{ "id": "3", "author": "FT", "date": "2024-03-01", "initials": "FT",
                 "text": "name unclear in MS" }],
  "notes": [{ "id": "5", "kind": "footnote", "text": "ASF, Mercanzia 10845, c. 67r." }]
}
```

Token rules:

- `type`: `text` | `break` (paragraph/line) | `tab` | `note_ref` | `comment_ref`.
- `changes`: ordered outer→inner list of enclosing revisions; empty = unchanged.
- `comment_ids`: comment ranges currently open over this token.
- The renderer derives everything from this; it never sees markers.

> **Why server-side parse, not pipeline-side tokens in the packet:** keeps the
> JSONL compact and human-diffable, puts the one tricky parser in Python (unit
> tested with a fixture), and lets us evolve render needs without re-running the
> pipeline.

---

## 4. Render rules

### 4.1 Two reading modes (one source of truth)

A segmented control at the top of the panel: **`Clean` | `Tracked changes`**.

- **Default = `Clean`**, because readability comes first. **But** when
  `has_revisions` is true, show a persistent, non-dismissable banner:
  *"This entry has 3 insertions, 1 deletion, 2 comments — View tracked changes"*
  (one-click switch). Remember the reviewer's last choice in `localStorage`.
  **FT review pending:** confirm default mode (Clean vs Tracked).
- `Clean` mode: render text tokens; **omit deletions and move-from**; show
  insertions and move-to as normal text (they are part of the current reading);
  comment/footnote markers shown as subtle superscripts (optional, see 4.4).
- `Tracked changes` mode: render the full editorial markup (below).

### 4.2 Change styling (Tracked mode)

Never color-only — each pairs a **decoration + icon + tooltip**:

| Change | Visual | Tooltip |
|--------|--------|---------|
| Insertion | green text, underline, leading `+` chip on hover | "Inserted by {author}, {date}" |
| Deletion | red/muted text, strike-through | "Deleted by {author}, {date}" |
| Move-from | blue, strike-through, badge "moved →" | "Moved from here by {author}" |
| Move-to | blue, underline, badge "← moved" | "Moved here by {author}" |
| Nested (e.g. ins-in-del) | stacked decorations; tooltip lists both authors | both revisions |

Move pairs (shared `id`) get a hover affordance that highlights both ends.

### 4.3 Comments

- The commented **span** gets a subtle yellow underline and a superscript marker
  `💬1` at the range start.
- Clicking the marker (or the span) opens the comment **body** in a right-margin
  popover/rail: author, date/initials, text. Keyboard-focusable; Esc closes.
- A "Comments (2)" disclosure at the panel foot lists all comment bodies for
  printing/scanning.

### 4.4 Footnotes / endnotes

- Superscript reference (`⁵`) at the ref point; hover/click reveals the note
  text; full list under a "Notes" disclosure at the foot of the panel.

### 4.5 Provenance & transparency affordances

- **Editorial-history chip row** under the panel title:
  `✚3 inserted · ✕1 deleted · 💬2 comments · ⁵1 footnote` — each chip scrolls to
  / filters that change type.
- **Legend**: a small, collapsible key explaining the colors/decorations.
- **"What the match used"** one-liner (muted): *"Database matching compared the
  clean current text."* — closes the transparency gap about what the algorithm
  saw vs the full editorial reality.

### 4.6 Layering with existing field-overlap highlighting

The current `HighlightedText` background-highlights DB field values found in the
Word text. This must **coexist** with change/comment markup. Layering precedence
(bottom→top, all composable on one run of text):

1. **Background** = field-overlap highlight (existing).
2. **Text color** = change kind (ins/del/move).
3. **Underline/strike** = change decoration **and/or** comment underline.
4. **Superscript markers** = comment/footnote refs (inline, after the token).

Implement as a single renderer that walks the token stream and, per token,
composes (a) change classes, (b) comment-underline class, (c) the overlap
background match. Don't run two independent regex passes.

---

## 5. Edge cases & fidelity rules

- **Deleted text never leaks** into Clean mode or into any matched/searchable
  text (already true: clean = `current_text`, which excludes `delText`).
- **Nested revisions**: render nested, attribute every enclosing change.
- **Moves**: pair `MOVEFROM`/`MOVETO` by `revision_id`; if only one side falls
  inside this entry's span, render what's present and note "(other end outside
  this entry)".
- **Comment spanning paragraph breaks / overlapping comments**: parser keeps a
  set of open comment ids; a token can belong to several.
- **Orphan refs**: a `COMMENT_REF`/`FOOTNOTE_REF` whose body is missing renders
  the marker with a muted "(body not found)" tooltip rather than crashing.
- **No revisions**: hide the mode toggle; render clean text; show "No tracked
  changes in this entry."
- **Author/date absent**: tooltip degrades to "Inserted (author unknown)".
- **Very long entries**: keep the scroll container; make the mode toggle and
  chip row sticky.

---

## 6. Component shape (frontend)

- `WordPanel` gains a mode toggle and renders either clean text or `<TrackedText>`.
- New `TrackedText` component: input = the served `word_entry_rich` token stream
  + the existing `highlight_values`; output = composed runs (per 4.6) + comment
  rail + notes list.
- New `CommentRail` / popover; `NotesList` disclosure.
- `types.ts`: add `WordEntryRich`, `RevisionToken`, `EntryComment`, `EntryNote`.
- Typography: render narrative in a **serif** reading face (distinct from the
  Inter UI chrome).

---

## 7. Accessibility

- Every change/comment/footnote conveys meaning via **text + shape**, not color:
  decoration (underline/strike), an icon/chip, and a tooltip/ARIA label.
- Markers are real `<button>`/focusable elements with `aria-label`
  ("Comment by FT: name unclear in MS"); popovers are dialog-role, Esc-closable.
- Check the change colors and the muted secondary text against **WCAG AA**.

---

## 8. Phased build plan

1. **Backend data** ✅ — `qa-packet` emits the five new fields (schema v2,
   [qa_packet_schema.md](qa_packet_schema.md)); the server loads comment/note
   bodies and adds `parse_revision_segments` + `word_entry_rich` to
   `case_payload`.
2. **Parser tests** ✅ — `tests/test_revision_parser.py` covers
   plain/ins/del/move/nested, comment range, overlapping/point markers,
   newlines/tabs, and orphan/unbalanced markers.
3. **Frontend render** ✅ — `TrackedText`, mode toggle (+ `localStorage`), change
   styling, comment popovers + foot disclosure, notes list, chip row, legend,
   "what the match used" line.
4. **Layering** ✅ — field-overlap highlighting composes inside the token
   renderer (shared `highlight.ts`): background = overlap mark, color/decoration
   = change kind, dotted underline = comment, superscripts = refs.
5. **Polish** ✅ (mostly) — serif narrative; sticky case bar + persistent
   decision dock; `Save & next`; ◀/▶ and `j`/`k` keyboard navigation; toasts.
   Remaining: a formal WCAG AA contrast audit of the change colours.

> **Layout note (implemented):** the panel now lives in a viewport-filling
> workspace rather than a long scroll — a slim sticky case bar, a Word|Database
> comparison as the hero (manuscript as an opt-in third pane), a compact
> evidence bar, and a persistent right-hand decision dock that can later grow to
> host field-level correction proposals (Stage 5). See `apps/review/src/App.tsx`,
> `CaseBar`, `EvidenceBar`, and the workspace CSS in `styles.css`.

---

## 9. Acceptance criteria

- An entry with `has_revisions` shows the banner + working Clean/Tracked toggle;
  an entry without hides the toggle and says so.
- In Tracked mode, insertions/deletions/moves render with decoration + color +
  icon + author/date tooltip; deletions are visibly struck and never appear in
  Clean mode.
- Commented spans are marked; their bodies (author/date/text) are reachable and
  Esc-dismissable; footnotes resolve to their text.
- Field-overlap highlighting still works and visibly composes with change markup
  on the same text.
- No raw `<INS …>`/`<COMMENT_START …>` markers ever reach the screen.
- Color is never the sole carrier of meaning (AA pass; decoration+icon present).

---

## 10. Future (out of scope here)

- Word-vs-DB **narrative diff** in the same view (align the two texts).
- Accepting a specific tracked change as the basis for a DB field correction
  (feeds the field-level proposal workflow / Stage 5).
- Surfacing the same tracked-changes view in entity-centric, full-corpus
  navigation once the platform expands beyond the QA subset.
