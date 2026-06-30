import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { loadAnalysisLibrary, runAnalysisBuild, runAnalysisLibrary } from "../api";
import type { AnalysisChart, AnalysisQuery, AnalysisResult } from "../types";

/* Analysis tab — the named query library + a visual guided builder. Both run on
 * the read-only executor (SELECT-only, capped, timed). The builder sends a
 * structured spec (subject · measure · filters · group-by); the backend composes
 * whitelisted, parameterised SQL — no user SQL, injection-proof. (docs/analysis/scope.md)
 */

const TABLE_CAP = 500;

// ---- builder config (mirrors the backend whitelist) ----
type Subject = "contracts" | "investors";
const SUBJECTS: { key: Subject; label: string }[] = [
  { key: "contracts", label: "contracts" },
  { key: "investors", label: "investors (people)" },
];
const MEASURES: Record<Subject, { key: string; label: string }[]> = {
  contracts: [
    { key: "count", label: "Count" },
    { key: "list", label: "List the records" },
    { key: "sum_total", label: "Sum of total capital" },
    { key: "avg_total", label: "Average total capital" },
  ],
  investors: [
    { key: "count", label: "Count" },
    { key: "list", label: "List the records" },
  ],
};
type FilterDef = { key: string; label: string; value: "text" | "number" | null };
const FILTER_DEFS: Record<Subject, FilterDef[]> = {
  contracts: [
    { key: "reg_year_from", label: "Registration year ≥", value: "number" },
    { key: "reg_year_to", label: "Registration year ≤", value: "number" },
    { key: "place_is", label: "Place is", value: "text" },
    { key: "activity_contains", label: "Activity contains", value: "text" },
    { key: "currency_is", label: "Currency is", value: "text" },
    { key: "register_is", label: "Register is", value: "text" },
    { key: "ec_discretion_yes", label: "Activity at discretion", value: null },
    { key: "no_place", label: "Has no place", value: null },
    { key: "no_investors", label: "Has no investors", value: null },
  ],
  investors: [
    { key: "gender_women", label: "Women", value: null },
    { key: "gender_men", label: "Men", value: null },
    { key: "role_gp", label: "General partner (gp)", value: null },
    { key: "role_lp", label: "Limited partner (lp)", value: null },
    { key: "via_proxy", label: "Via proxy", value: null },
    { key: "jewish", label: "Recorded as Jewish", value: null },
    { key: "title_contains", label: "Title contains", value: "text" },
    { key: "reg_year_from", label: "Registration year ≥", value: "number" },
    { key: "reg_year_to", label: "Registration year ≤", value: "number" },
  ],
};
const GROUPS: Record<Subject, { key: string; label: string }[]> = {
  contracts: [
    { key: "", label: "— no grouping —" },
    { key: "reg_year", label: "Registration year" },
    { key: "decade", label: "Decade" },
    { key: "currency", label: "Currency" },
    { key: "register", label: "Register" },
  ],
  investors: [
    { key: "", label: "— no grouping —" },
    { key: "gender", label: "Gender" },
    { key: "reg_year", label: "Registration year" },
  ],
};

function num(v: string | number | null): number {
  return typeof v === "number" ? v : Number(v);
}

function BarChart({ result }: { result: AnalysisResult }) {
  const valIdx = result.columns.length - 1;
  const data = result.rows
    .map((r) => ({ label: String(r[0] ?? ""), value: num(r[valIdx]) }))
    .filter((d) => Number.isFinite(d.value))
    .slice(0, 20);
  if (data.length === 0) return null;
  const max = Math.max(1, ...data.map((d) => d.value));
  return (
    <div className="an-bars">
      {data.map((d) => (
        <div className="an-bar-row" key={d.label}>
          <span className="an-bar-label" title={d.label}>
            {d.label}
          </span>
          <span className="an-bar-track">
            <span className="an-bar-fill" style={{ width: `${Math.round((d.value / max) * 100)}%` }} />
          </span>
          <span className="an-bar-value">{d.value.toLocaleString()}</span>
        </div>
      ))}
    </div>
  );
}

function LineChart({ result }: { result: AnalysisResult }) {
  const valIdx = result.columns.length - 1;
  const pts = result.rows
    .map((r) => [num(r[0]), num(r[valIdx])] as [number, number])
    .filter((p) => Number.isFinite(p[0]) && Number.isFinite(p[1]))
    .sort((a, b) => a[0] - b[0]);
  const [hover, setHover] = useState<number | null>(null);
  if (pts.length < 2) return null;
  const W = 720;
  const H = 200;
  const padL = 18;
  const padTop = 10;
  const plotH = 150;
  const axisY = padTop + plotH;
  const xs = pts.map((p) => p[0]);
  const xMin = Math.min(...xs);
  const xMax = Math.max(...xs);
  const yMax = Math.max(1, ...pts.map((p) => p[1]));
  const px = (x: number) => padL + ((x - xMin) / (xMax - xMin || 1)) * (W - padL * 2);
  const py = (y: number) => axisY - (y / yMax) * plotH;
  const line = pts.map((p, i) => `${i === 0 ? "M" : "L"}${px(p[0]).toFixed(1)},${py(p[1]).toFixed(1)}`).join(" ");
  const area = `${line} L${px(xMax).toFixed(1)},${axisY} L${px(xMin).toFixed(1)},${axisY} Z`;
  const ticks = [xMin, ...[1500, 1550, 1600, 1650, 1700, 1750].filter((t) => t > xMin && t < xMax), xMax];
  return (
    <div className="an-line-wrap">
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="Time series" preserveAspectRatio="xMidYMid meet">
        <line x1={padL} y1={axisY} x2={W - padL} y2={axisY} stroke="#e0d4c0" strokeWidth="1" />
        <path d={area} fill="#b07a47" opacity="0.12" />
        <path d={line} fill="none" stroke="#b07a47" strokeWidth="1.6" />
        {pts.map((p, i) => (
          <circle key={i} cx={px(p[0])} cy={py(p[1])} r={hover === i ? 3.2 : 0} fill="#9d6a38" />
        ))}
        {pts.map((p, i) => (
          <rect
            key={`h${i}`}
            x={px(p[0]) - (W / pts.length) / 2}
            y={padTop}
            width={W / pts.length}
            height={plotH}
            fill="transparent"
            onMouseOver={() => setHover(i)}
            onMouseLeave={() => setHover(null)}
          />
        ))}
        {ticks.map((t) => (
          <text
            key={t}
            x={px(t)}
            y={H - 4}
            fontSize="10"
            fill="#9a8a78"
            textAnchor={t === xMin ? "start" : t === xMax ? "end" : "middle"}
            fontFamily="Georgia, serif"
          >
            {t}
          </text>
        ))}
      </svg>
      {hover !== null && (
        <div className="ribbon-tip" style={{ left: `${Math.max(10, Math.min(90, (px(pts[hover][0]) / W) * 100))}%` }}>
          <span className="ribbon-tip-decade">{pts[hover][0]}</span>
          <span className="ribbon-tip-row">{pts[hover][1].toLocaleString()}</span>
        </div>
      )}
    </div>
  );
}

function csvEscape(v: string | number | null): string {
  const s = v == null ? "" : String(v);
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}
function downloadCsv(name: string, result: AnalysisResult) {
  const lines = [result.columns.map(csvEscape).join(",")];
  for (const row of result.rows) lines.push(row.map(csvEscape).join(","));
  const blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

/** Shared result display: toolbar (CSV · SQL · rowcount) + chart + table. */
function ResultView({
  result,
  chart,
  sql,
  csvName,
}: {
  result: AnalysisResult;
  chart: AnalysisChart;
  sql: string;
  csvName: string;
}) {
  const navigate = useNavigate();
  const [showSql, setShowSql] = useState(false);
  const idCol = result.columns.findIndex((c) => c === "contract_id");
  return (
    <>
      <div className="an-toolbar">
        <button type="button" className="pill-button" onClick={() => downloadCsv(csvName, result)} disabled={result.rows.length === 0}>
          ⤓ Export CSV
        </button>
        <button type="button" className="pill-button" onClick={() => setShowSql((s) => !s)}>
          {showSql ? "Hide SQL" : "Show SQL"}
        </button>
        <span className="muted an-rowcount">
          {result.row_count.toLocaleString()} row{result.row_count === 1 ? "" : "s"}
          {result.truncated ? " (capped)" : ""}
        </span>
      </div>
      {showSql && <pre className="an-sql">{sql}</pre>}
      {chart === "bar" && <BarChart result={result} />}
      {chart === "line" && <LineChart result={result} />}
      <div className="an-table-wrap">
        <table className="an-table">
          <thead>
            <tr>
              {result.columns.map((c) => (
                <th key={c}>{c}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {result.rows.slice(0, TABLE_CAP).map((row, ri) => (
              <tr key={ri}>
                {row.map((cell, ci) => (
                  <td key={ci}>
                    {ci === idCol && cell != null ? (
                      <button type="button" className="link-like" onClick={() => navigate(`/database/contract/${cell}`)}>
                        {String(cell)}
                      </button>
                    ) : cell == null ? (
                      <span className="muted">—</span>
                    ) : (
                      String(cell)
                    )}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
        {result.rows.length > TABLE_CAP && (
          <p className="muted an-table-note">
            Showing the first {TABLE_CAP.toLocaleString()} of {result.row_count.toLocaleString()} rows — export CSV for all.
          </p>
        )}
      </div>
    </>
  );
}

type SpecFilter = { field: string; value: string };

const SAMPLE_SQL = "SELECT cu.currency, COUNT(*) AS contracts, ROUND(AVG(CAST(c.total AS REAL))) AS avg_capital\nFROM contract c JOIN currency cu ON cu.currency_id = c.currency_id\nWHERE c.is_deleted = 0 AND c.total GLOB '[0-9]*'\nGROUP BY cu.currency_id\nORDER BY contracts DESC";

export default function Analysis() {
  const [mode, setMode] = useState<"library" | "builder" | "console">("library");

  // library state
  const [queries, setQueries] = useState<AnalysisQuery[]>([]);
  const [selected, setSelected] = useState<AnalysisQuery | null>(null);
  const [libResult, setLibResult] = useState<AnalysisResult | null>(null);
  const [libRunning, setLibRunning] = useState(false);
  const [error, setError] = useState("");

  // builder state
  const [subject, setSubject] = useState<Subject>("contracts");
  const [measure, setMeasure] = useState("count");
  const [filters, setFilters] = useState<SpecFilter[]>([]);
  const [group, setGroup] = useState("");
  const [buildResult, setBuildResult] = useState<{ result: AnalysisResult; chart: AnalysisChart; sql: string } | null>(null);
  const [buildError, setBuildError] = useState("");

  // console state
  const [sqlText, setSqlText] = useState(SAMPLE_SQL);
  const [consoleResult, setConsoleResult] = useState<AnalysisResult | null>(null);
  const [consoleError, setConsoleError] = useState("");
  const [consoleRunning, setConsoleRunning] = useState(false);
  const runConsole = () => {
    setConsoleRunning(true);
    setConsoleError("");
    runAnalysisLibrary(sqlText)
      .then((r) => {
        setConsoleResult(r);
        setConsoleError("");
      })
      .catch((e: Error) => {
        setConsoleError(e.message);
        setConsoleResult(null);
      })
      .finally(() => setConsoleRunning(false));
  };

  useEffect(() => {
    loadAnalysisLibrary()
      .then((d) => setQueries(d.queries))
      .catch((e: Error) => setError(e.message));
  }, []);

  useEffect(() => {
    if (!selected) return;
    setLibRunning(true);
    setError("");
    setLibResult(null);
    runAnalysisLibrary(selected.sql)
      .then(setLibResult)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLibRunning(false));
  }, [selected]);

  const filterDefs = FILTER_DEFS[subject];
  const incomplete = filters.some((f) => {
    const def = filterDefs.find((d) => d.key === f.field);
    return def?.value && !f.value.trim();
  });

  // auto-run the builder (debounced) whenever the spec is complete
  useEffect(() => {
    if (mode !== "builder" || incomplete) return;
    const handle = window.setTimeout(() => {
      setBuildError("");
      runAnalysisBuild({ subject, measure, filters, group_by: measure === "list" ? "" : group })
        .then((r) => setBuildResult({ result: { columns: r.columns, rows: r.rows, row_count: r.row_count, truncated: r.truncated }, chart: r.chart, sql: r.sql }))
        .catch((e: Error) => {
          setBuildError(e.message);
          setBuildResult(null);
        });
    }, 350);
    return () => window.clearTimeout(handle);
  }, [mode, subject, measure, filters, group, incomplete]);

  const groups = useMemo(() => {
    const map = new Map<string, AnalysisQuery[]>();
    for (const q of queries) {
      if (!map.has(q.group)) map.set(q.group, []);
      map.get(q.group)!.push(q);
    }
    return [...map.entries()];
  }, [queries]);

  const switchSubject = (s: Subject) => {
    setSubject(s);
    setMeasure("count");
    setFilters([]);
    setGroup("");
  };
  const addFilter = () => setFilters((f) => [...f, { field: filterDefs[0].key, value: "" }]);
  const setFilter = (i: number, patch: Partial<SpecFilter>) =>
    setFilters((f) => f.map((x, j) => (j === i ? { ...x, ...patch } : x)));
  const removeFilter = (i: number) => setFilters((f) => f.filter((_, j) => j !== i));

  return (
    <div className="db-browser">
      <aside className="db-rail">
        <div className="db-rail-head">
          <p className="eyebrow">Analysis</p>
          <div className="db-tabs an-mode">
            <button type="button" className={mode === "library" ? "db-tab is-active" : "db-tab"} onClick={() => setMode("library")}>
              Library
            </button>
            <button type="button" className={mode === "builder" ? "db-tab is-active" : "db-tab"} onClick={() => setMode("builder")}>
              Build a query
            </button>
            <button type="button" className={mode === "console" ? "db-tab is-active" : "db-tab"} onClick={() => setMode("console")}>
              SQL console
            </button>
          </div>
        </div>
        {mode === "library" ? (
          <div className="an-library">
            {groups.map(([groupName, items]) => (
              <section key={groupName} className="an-group">
                <h4 className="an-group-head">{groupName}</h4>
                <ul>
                  {items.map((q) => (
                    <li key={q.id}>
                      <button type="button" className={selected?.id === q.id ? "an-query is-active" : "an-query"} onClick={() => setSelected(q)}>
                        <span className="an-query-title">{q.title}</span>
                        <span className="an-query-desc muted">{q.description}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              </section>
            ))}
            {queries.length === 0 && !error && <p className="db-empty muted">Loading…</p>}
          </div>
        ) : mode === "builder" ? (
          <div className="an-library">
            <p className="db-empty muted" style={{ padding: "8px 10px" }}>
              Compose a question from the controls — it runs as you change it. Nothing here can modify the data.
            </p>
          </div>
        ) : (
          <div className="an-library">
            <p className="db-empty muted" style={{ padding: "8px 10px" }}>
              Write your own read-only SQL for anything the builder can't express. SELECT only —
              writes are refused, results are capped.
            </p>
          </div>
        )}
      </aside>

      <section className="db-detail">
        {mode === "library" ? (
          !selected ? (
            <div className="an-empty">
              <p className="eyebrow">Analysis</p>
              <h2>Ask a question of the corpus</h2>
              <p className="muted an-note">
                Pick a query from the library to see its result as a table and a chart, and export it to CSV — or switch
                to <strong>Build a query</strong> to compose your own. Every query is read-only.
              </p>
            </div>
          ) : (
            <div className="an-result">
              <p className="eyebrow">{selected.group}</p>
              <h2>{selected.title}</h2>
              <p className="muted an-note">{selected.description}</p>
              {error && <p className="error-text">{error}</p>}
              {libRunning && <p className="muted">Running…</p>}
              {libResult && !libRunning && (
                <ResultView result={libResult} chart={selected.chart} sql={selected.sql} csvName={`${selected.id}.csv`} />
              )}
            </div>
          )
        ) : mode === "builder" ? (
          <div className="an-result">
            <p className="eyebrow">Build a query</p>
            <div className="an-builder">
              <div className="an-sentence">
                <span>Show</span>
                <select value={measure} onChange={(e) => setMeasure(e.target.value)}>
                  {MEASURES[subject].map((m) => (
                    <option key={m.key} value={m.key}>
                      {m.label}
                    </option>
                  ))}
                </select>
                <span>of</span>
                <select value={subject} onChange={(e) => switchSubject(e.target.value as Subject)}>
                  {SUBJECTS.map((s) => (
                    <option key={s.key} value={s.key}>
                      {s.label}
                    </option>
                  ))}
                </select>
                {measure !== "list" && (
                  <>
                    <span>grouped by</span>
                    <select value={group} onChange={(e) => setGroup(e.target.value)}>
                      {GROUPS[subject].map((g) => (
                        <option key={g.key} value={g.key}>
                          {g.label}
                        </option>
                      ))}
                    </select>
                  </>
                )}
              </div>

              <div className="an-filters">
                <span className="an-filters-label">where</span>
                {filters.length === 0 && <span className="muted an-filters-empty">no filters — the whole corpus</span>}
                {filters.map((f, i) => {
                  const def = filterDefs.find((d) => d.key === f.field) ?? filterDefs[0];
                  return (
                    <span className="an-filter" key={i}>
                      <select value={f.field} onChange={(e) => setFilter(i, { field: e.target.value, value: "" })}>
                        {filterDefs.map((d) => (
                          <option key={d.key} value={d.key}>
                            {d.label}
                          </option>
                        ))}
                      </select>
                      {def.value && (
                        <input
                          type={def.value === "number" ? "number" : "text"}
                          value={f.value}
                          placeholder={def.value === "number" ? "year" : "value"}
                          onChange={(e) => setFilter(i, { value: e.target.value })}
                        />
                      )}
                      <button type="button" className="an-filter-x" onClick={() => removeFilter(i)} aria-label="Remove filter">
                        ✕
                      </button>
                    </span>
                  );
                })}
                <button type="button" className="an-add-filter" onClick={addFilter}>
                  + Add filter
                </button>
              </div>
            </div>

            {incomplete && <p className="muted an-note">Fill in the filter value(s) to run.</p>}
            {buildError && <p className="error-text">{buildError}</p>}
            {buildResult && !incomplete && (
              <ResultView result={buildResult.result} chart={buildResult.chart} sql={buildResult.sql} csvName="query.csv" />
            )}
          </div>
        ) : (
          <div className="an-result">
            <p className="eyebrow">SQL console</p>
            <h2>Read-only SQL</h2>
            <p className="muted an-note">
              For anything the builder can't express. <strong>SELECT</strong> / <strong>WITH</strong> only —
              the connection is opened read-only, so writes are impossible; multiple statements and any
              write keyword are refused, results are capped at 5,000 rows. Press <kbd>⌘/Ctrl</kbd>+<kbd>Enter</kbd> to run.
            </p>
            <textarea
              className="an-console"
              value={sqlText}
              spellCheck={false}
              onChange={(e) => setSqlText(e.target.value)}
              onKeyDown={(e) => {
                if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
                  e.preventDefault();
                  runConsole();
                }
              }}
            />
            <div className="an-toolbar">
              <button type="button" className="pill-button is-primary" onClick={runConsole} disabled={consoleRunning || !sqlText.trim()}>
                {consoleRunning ? "Running…" : "Run query"}
              </button>
            </div>
            {consoleError && <p className="error-text">{consoleError}</p>}
            {consoleResult && !consoleRunning && (
              <ResultView result={consoleResult} chart="none" sql={sqlText} csvName="query.csv" />
            )}
          </div>
        )}
      </section>
    </div>
  );
}
