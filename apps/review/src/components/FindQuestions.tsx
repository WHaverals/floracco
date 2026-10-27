import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { listFindQuestions, loadFindRegistry, removeFindQuestion, renameFindQuestion, saveFindQuestion } from "../api";
import type { FindQuestion, FindRegistry } from "../types";
import type { FindSpec } from "../utils/findSpec";
import { questionPart } from "../utils/findView";

/* Find, step 4 (review 2026-10-27, 12.1 and 12.8): the left rail of the Find
 * pane. "My questions" are the questions she saved, kept on the server for the
 * signed-in person and each asked again today, marked "changed" when today's
 * contracts differ from the ones it held when saved. "This session" is the
 * numbered history of the questions asked in this tab; two of them can be
 * combined (in both, in either, in one and not in the other) into a new set. */

const QUESTIONS_EVENT = "floracco:find-questions";
const HISTORY_EVENT = "floracco:find-history";
const HISTORY_KEY = "floracco.find.history";
const HISTORY_LIMIT = 30;

export type HistoryEntry = { n: number; search: string; sentence: string; total: number };

/** Tell every listener that the saved questions changed. */
export function refreshFindQuestions() {
  window.dispatchEvent(new Event(QUESTIONS_EVENT));
}

/** The signed-in person's saved questions, reloaded whenever one is saved,
 * renamed or removed. */
export function useFindQuestions(): { questions: FindQuestion[] | null; failed: boolean } {
  const [questions, setQuestions] = useState<FindQuestion[] | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let live = true;
    const load = () =>
      listFindQuestions()
        .then((r) => live && (setQuestions(r.questions), setFailed(false)))
        .catch(() => live && setFailed(true));
    load();
    window.addEventListener(QUESTIONS_EVENT, load);
    return () => {
      live = false;
      window.removeEventListener(QUESTIONS_EVENT, load);
    };
  }, []);
  return { questions, failed };
}

let memoryHistory: HistoryEntry[] = [];

function readHistory(): HistoryEntry[] {
  try {
    const raw = sessionStorage.getItem(HISTORY_KEY);
    if (raw) return JSON.parse(raw) as HistoryEntry[];
  } catch {
    /* storage refused: keep the copy in memory */
  }
  return memoryHistory;
}

/** Number a question asked in this tab; asking the same one again keeps its number. */
export function recordFindHistory(search: string, sentence: string, total: number) {
  const history = readHistory();
  const known = history.find((h) => h.search === search);
  if (known) {
    if (known.total === total && known.sentence === sentence) return;
    known.total = total;
    known.sentence = sentence;
  } else {
    const n = (history[history.length - 1]?.n ?? 0) + 1;
    history.push({ n, search, sentence, total });
  }
  memoryHistory = history.slice(-HISTORY_LIMIT);
  try {
    sessionStorage.setItem(HISTORY_KEY, JSON.stringify(memoryHistory));
  } catch {
    /* storage refused: the history stays in memory for this page */
  }
  window.dispatchEvent(new Event(HISTORY_EVENT));
}

function useFindHistory(): HistoryEntry[] {
  const [history, setHistory] = useState<HistoryEntry[]>(readHistory);
  useEffect(() => {
    const load = () => setHistory([...readHistory()]);
    window.addEventListener(HISTORY_EVENT, load);
    return () => window.removeEventListener(HISTORY_EVENT, load);
  }, []);
  return history;
}

/** The address of two sets combined (findSpec.ts writes the same form). */
export function combinedSearch(op: "and" | "or" | "not", first: string, second: string): string {
  const query = new URLSearchParams();
  query.append("combine", op);
  query.append("set", first);
  query.append("set", second);
  return query.toString();
}

let registryOnce: Promise<FindRegistry> | null = null;

function useCombineWords(): FindRegistry["combine"] {
  const [words, setWords] = useState<FindRegistry["combine"]>([]);
  useEffect(() => {
    registryOnce ??= loadFindRegistry();
    registryOnce.then((r) => setWords(r.combine)).catch(() => {
      registryOnce = null;
    });
  }, []);
  return words;
}

export default function FindRail({ current }: { current: string }) {
  const navigate = useNavigate();
  const words = useCombineWords();
  const { questions, failed } = useFindQuestions();
  const history = useFindHistory();
  const [picked, setPicked] = useState<number[]>([]);
  const open = (search: string) => navigate(`/analysis/find${search ? `?${search}` : ""}`);
  const chosen = picked.map((n) => history.find((h) => h.n === n)).filter((h): h is HistoryEntry => Boolean(h));
  const combine = (op: "and" | "or" | "not", a: HistoryEntry, b: HistoryEntry) => {
    setPicked([]);
    open(combinedSearch(op, a.search, b.search));
  };
  return (
    <div className="an-library find-rail">
      <section>
        <p className="find-rail-title">My questions</p>
        {failed ? (
          <p className="muted find-rail-note">Your saved questions could not be loaded.</p>
        ) : questions === null ? (
          <p className="muted find-rail-note">Loading…</p>
        ) : questions.length === 0 ? (
          <p className="muted find-rail-note">Save a question with “Save as my question” under its answer to find it here.</p>
        ) : (
          <ul className="find-rail-list">
            {questions.map((q) => (
              <li key={q.id}>
                <button
                  type="button"
                  className={current.includes(`saved=${q.id}`) ? "find-rail-item is-active" : "find-rail-item"}
                  title={q.lines.join(" ")}
                  onClick={() => open(q.search.includes("saved=") ? q.search : [q.search, `saved=${q.id}`].filter(Boolean).join("&"))}
                >
                  <span className="find-rail-name">{q.name}</span>
                  <span className="find-rail-count">
                    {q.today === null ? "–" : q.today.toLocaleString()}
                    {q.changed && <span className="find-changed">changed</span>}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
      <section>
        <p className="find-rail-title">This session</p>
        {history.length === 0 ? (
          <p className="muted find-rail-note">Each question you ask here is numbered, so two can be combined.</p>
        ) : (
          <ul className="find-rail-list">
            {[...history].reverse().map((h) => (
              <li key={h.n} className="find-history">
                <input
                  type="checkbox"
                  aria-label={`Choose set ${h.n} to combine`}
                  checked={picked.includes(h.n)}
                  onChange={(e) =>
                    setPicked((p) => (e.target.checked ? [...p.filter((x) => x !== h.n), h.n].slice(-2) : p.filter((x) => x !== h.n)))
                  }
                />
                <button type="button" className="find-rail-item" title={h.sentence} onClick={() => open(h.search)}>
                  <span className="find-rail-name">
                    #{h.n} {h.sentence}
                  </span>
                  <span className="find-rail-count">{h.total.toLocaleString()}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
        {chosen.length === 2 && words.length ? (
          <div className="find-combine">
            <span className="find-rail-note">Contracts</span>
            {words.flatMap((w) =>
              (w.key === "not" ? [chosen, [chosen[1], chosen[0]]] : [chosen]).map(([a, b]) => (
                <button key={`${w.key}-${a.n}`} type="button" className="find-combine-option" onClick={() => combine(w.key, a, b)}>
                  {w.opening} #{a.n} {w.between} #{b.n}
                </button>
              )),
            )}
          </div>
        ) : history.length > 1 ? (
          <p className="muted find-rail-note">Tick two to combine them.</p>
        ) : null}
      </section>
    </div>
  );
}

/** Under the answer: the saved question this one was opened from, with what
 * changed since and Rename / Remove; or "Save as my question". */
export function SavedQuestionBar({
  questionSearch,
  saveSearch,
  spec,
  savedId,
  onSaved,
}: {
  /** the current question's address, without its view */
  questionSearch: string;
  /** what a save stores: the question and its view */
  saveSearch: string;
  spec: FindSpec;
  savedId: string;
  onSaved: (id: string) => void;
}) {
  const { questions } = useFindQuestions();
  const saved = questions?.find((q) => q.id === savedId) ?? null;
  const matches = saved !== null && questionPart(saved.search) === questionSearch;
  const [mode, setMode] = useState<"idle" | "save" | "rename" | "remove">("idle");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    setMode("idle");
    setError("");
  }, [savedId, questionSearch]);
  const act = (run: () => Promise<unknown>) => {
    setBusy(true);
    setError("");
    run()
      .then(() => {
        setMode("idle");
        refreshFindQuestions();
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setBusy(false));
  };
  const form = (label: string, submit: () => void) => (
    <form
      className="find-save-form"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      <input
        type="text"
        value={name}
        maxLength={120}
        placeholder="a name for this question"
        autoFocus
        onChange={(e) => setName(e.target.value)}
      />
      <button type="submit" className="pill-button is-primary" disabled={busy || !name.trim()}>
        {label}
      </button>
      <button type="button" className="link-like" onClick={() => setMode("idle")}>
        Cancel
      </button>
    </form>
  );
  return (
    <div className="find-saved">
      {matches && saved ? (
        <div className={saved.changed ? "find-saved-banner is-changed" : "find-saved-banner"}>
          <strong>{saved.name}</strong>
          {saved.changed && <span className="find-changed">changed</span>}
          <span className="find-saved-lines">{saved.lines.join(" ")}</span>
          {mode === "rename" ? (
            form("Rename", () => act(() => renameFindQuestion(saved.id, name)))
          ) : mode === "remove" ? (
            <span className="find-save-form">
              Remove “{saved.name}” from your questions?{" "}
              <button type="button" className="pill-button" disabled={busy} onClick={() => act(() => removeFindQuestion(saved.id))}>
                Remove
              </button>
              <button type="button" className="link-like" onClick={() => setMode("idle")}>
                Keep it
              </button>
            </span>
          ) : (
            <span className="find-saved-actions">
              <button
                type="button"
                className="link-like"
                onClick={() => {
                  setName(saved.name);
                  setMode("rename");
                }}
              >
                Rename
              </button>
              <button type="button" className="link-like" onClick={() => setMode("remove")}>
                Remove
              </button>
            </span>
          )}
        </div>
      ) : mode === "save" ? (
        form("Save", () =>
          act(() =>
            saveFindQuestion(name, saveSearch, spec).then((r) => {
              onSaved(r.id);
            }),
          ),
        )
      ) : (
        <span className="find-saved-actions">
          {saved && <span className="muted">Changed from your saved question “{saved.name}”. </span>}
          <button
            type="button"
            className="pill-button"
            onClick={() => {
              setName("");
              setMode("save");
            }}
          >
            Save as my question
          </button>
        </span>
      )}
      {error && <p className="error-text">{error}</p>}
    </div>
  );
}
