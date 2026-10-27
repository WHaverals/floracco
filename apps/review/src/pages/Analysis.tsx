import { useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import CopyLinkButton from "../components/CopyLinkButton";
import { loadAnalysisLibrary, runAnalysisBuild, runAnalysisLibrary } from "../api";
import { useLatest } from "../utils/latest";
import { roleLabel } from "../utils/roles";
import type { AnalysisChart, AnalysisQuery, AnalysisResult } from "../types";
import { isToolHidden } from "../features";

/* Analysis tab — the named query library + a visual guided builder. Both run on
 * the read-only executor (SELECT-only, capped, timed). The builder sends a
 * structured spec (subject · measure · filters · group-by); the backend composes
 * whitelisted, parameterised SQL — no user SQL, injection-proof. (docs/analysis/scope.md)
 */

const TABLE_CAP = 500;

/** Screen headers for result columns. The SQL aliases stay unchanged in the CSV
 * export and in "Show SQL", so exports keep stable column names for scripts. */
const COLUMN_LABELS: Record<string, string> = {
  item: "Item",
  n: "Count",
  count: "Count",
  year: "Year",
  decade: "Decade",
  date: "Registration date",
  registration_date: "Registration date",
  contract_id: "Contract",
  firm_name: "Firm name",
  contracts: "Contracts",
  currency: "Currency",
  total_capital: "Total capital",
  average_capital: "Average capital",
  avg_capital: "Average capital",
  place_name: "Place",
  investors: "Partners",
  total_investors: "Partners",
  sub_contracts: "Sub-contracts",
  later_acts: "Sub-contracts",
  sub_type: "Type",
  act_type: "Type",
  acts: "Sub-contracts",
  role: "Role",
  investments: "Stakes",
  min: "Fewest",
  avg: "Average",
  max: "Most",
  first_name: "First name",
  last_name: "Last name",
  father_mother: "Father / mother",
  grandfather: "Grandfather",
  pct_via_proxy: "Via proxy (%)",
  pct_jewish: "Jewish (%)",
  pct_stated_in_act: "Stated in the act (%)",
  pct_editorial_attribution: "Editorial attribution (%)",
  pct_joint: "Joint (%)",
  itemization: "Itemization",
  initial_term_months: "Initial term (months)",
  register: "Register",
  gender: "Gender",
  people: "People",
};

function columnLabel(alias: string): string {
  if (COLUMN_LABELS[alias]) return COLUMN_LABELS[alias];
  const words = alias.replaceAll("_", " ").trim();
  return words ? words[0].toUpperCase() + words.slice(1) : alias;
}

/** Cell text for the screen only (the CSV keeps stored values): role codes as
 * words; the export's 0000-00-00 placeholder, and the year 0 it yields, as "not recorded". */
function cellText(alias: string, cell: string | number): string {
  if (alias === "role" && typeof cell === "string") return roleLabel(cell);
  if (cell === "0000-00-00" || (alias === "year" && cell === 0)) return "not recorded";
  return String(cell);
}

// ---- builder config (mirrors the backend whitelist) ----
type Subject = "contracts" | "investors";
const SUBJECTS: { key: Subject; label: string }[] = [
  { key: "contracts", label: "contracts" },
  { key: "investors", label: "partners" },
];
const MEASURES: Record<Subject, { key: string; label: string }[]> = {
  contracts: [
    { key: "count", label: "Count" },
    { key: "list", label: "List the records" },
    { key: "sum_total", label: "Sum of total capital" },
    { key: "avg_total", label: "Average total capital" },
  ],
  investors: [
    { key: "count", label: "Count partner appearances" },
    { key: "distinct_people_reviewed", label: "Count people using reviewed identities" },
    { key: "distinct_people_entered", label: "Count person entries, as entered" },
    { key: "list", label: "List the appearances" },
  ],
};
type FilterDef = { key: string; label: string; value: "text" | "number" | null };
const FILTER_DEFS: Record<Subject, FilterDef[]> = {
  contracts: [
    { key: "reg_year_from", label: "Registration year ≥", value: "number" },
    { key: "reg_year_to", label: "Registration year ≤", value: "number" },
    { key: "place_is", label: "Place is", value: "text" },
    { key: "activity_contains", label: "Economic activity contains", value: "text" },
    { key: "currency_is", label: "Currency is", value: "text" },
    { key: "register_is", label: "Register is", value: "text" },
    { key: "ec_discretion_yes", label: "Economic activity at discretion", value: null },
    { key: "no_place", label: "Has no place", value: null },
    { key: "no_investors", label: "Has no partners", value: null },
  ],
  investors: [
    { key: "gender_women", label: "Women", value: null },
    { key: "gender_men", label: "Men", value: null },
    { key: "role_gp", label: "General partner", value: null },
    { key: "role_lp", label: "External investor (accomandante)", value: null },
    { key: "via_proxy", label: "Via proxy", value: null },
    { key: "jewish", label: "Jewish (recorded or attributed)", value: null },
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
    .map((r) => ({ label: r[0] == null ? "" : cellText(result.columns[0], r[0]), value: num(r[valIdx]) }))
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

const SERIES_COLORS = ["#b07a47", "#5f7a8e"];
const SERIES_DOT_COLORS = ["#9d6a38", "#4c6377"];

/** Time-series chart. Default: one series (the LAST column). With `allSeries`,
 * every column after the first becomes its own line (chart type "multiline") —
 * used where two measures must stay visually distinct, e.g. the Jewish trend
 * split by provenance (stated in the act vs editorial attribution). */
function LineChart({ result, allSeries = false }: { result: AnalysisResult; allSeries?: boolean }) {
  const seriesIdx = allSeries
    ? result.columns.slice(1).map((_, i) => i + 1)
    : [result.columns.length - 1];
  const rows = result.rows
    .map((r) => ({ x: num(r[0]), ys: seriesIdx.map((i) => num(r[i])) }))
    .filter((p) => Number.isFinite(p.x) && p.ys.every((y) => Number.isFinite(y)))
    .sort((a, b) => a.x - b.x);
  const [hover, setHover] = useState<number | null>(null);
  if (rows.length < 2 || seriesIdx.length === 0) return null;
  const W = 720;
  const H = 200;
  const padL = 18;
  const padTop = 10;
  const plotH = 150;
  const axisY = padTop + plotH;
  const xs = rows.map((p) => p.x);
  const xMin = Math.min(...xs);
  const xMax = Math.max(...xs);
  const yMax = Math.max(1, ...rows.flatMap((p) => p.ys));
  const px = (x: number) => padL + ((x - xMin) / (xMax - xMin || 1)) * (W - padL * 2);
  const py = (y: number) => axisY - (y / yMax) * plotH;
  const paths = seriesIdx.map((_, j) =>
    rows.map((p, i) => `${i === 0 ? "M" : "L"}${px(p.x).toFixed(1)},${py(p.ys[j]).toFixed(1)}`).join(" "),
  );
  const area = `${paths[0]} L${px(xMax).toFixed(1)},${axisY} L${px(xMin).toFixed(1)},${axisY} Z`;
  const ticks = [xMin, ...[1500, 1550, 1600, 1650, 1700, 1750].filter((t) => t > xMin && t < xMax), xMax];
  const seriesLabel = (j: number) => columnLabel(String(result.columns[seriesIdx[j]] ?? ""));
  return (
    <div className="an-line-wrap">
      {seriesIdx.length > 1 && (
        <div className="an-line-legend">
          {seriesIdx.map((_, j) => (
            <span key={j} className="legend-item">
              <span className="an-legend-swatch" style={{ background: SERIES_COLORS[j % SERIES_COLORS.length] }} />
              {seriesLabel(j)}
            </span>
          ))}
        </div>
      )}
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="Time series" preserveAspectRatio="xMidYMid meet">
        <line x1={padL} y1={axisY} x2={W - padL} y2={axisY} stroke="#e0d4c0" strokeWidth="1" />
        {seriesIdx.length === 1 && <path d={area} fill="#b07a47" opacity="0.12" />}
        {paths.map((d, j) => (
          <path key={j} d={d} fill="none" stroke={SERIES_COLORS[j % SERIES_COLORS.length]} strokeWidth="1.6" />
        ))}
        {rows.map((p, i) =>
          seriesIdx.map((_, j) => (
            <circle
              key={`${i}-${j}`}
              cx={px(p.x)}
              cy={py(p.ys[j])}
              r={hover === i ? 3.2 : 0}
              fill={SERIES_DOT_COLORS[j % SERIES_DOT_COLORS.length]}
            />
          )),
        )}
        {rows.map((p, i) => (
          <rect
            key={`h${i}`}
            x={px(p.x) - (W / rows.length) / 2}
            y={padTop}
            width={W / rows.length}
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
            fill="#6f6052"
            textAnchor={t === xMin ? "start" : t === xMax ? "end" : "middle"}
            fontFamily="Georgia, serif"
          >
            {t}
          </text>
        ))}
      </svg>
      {hover !== null && (
        <div className="ribbon-tip" style={{ left: `${Math.max(10, Math.min(90, (px(rows[hover].x) / W) * 100))}%` }}>
          <span className="ribbon-tip-decade">{rows[hover].x}</span>
          {seriesIdx.map((_, j) => (
            <span key={j} className="ribbon-tip-row">
              {seriesIdx.length > 1 ? `${seriesLabel(j)}: ` : ""}
              {rows[hover].ys[j].toLocaleString()}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function csvEscape(v: string | number | null): string {
  const s = v == null ? "" : String(v);
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}
// The CSV keeps the SQL aliases and stored values (0000-00-00, gp/lp): column
// names stay stable for scripts and match "Show SQL".
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
      {chart === "multiline" && <LineChart result={result} allSeries />}
      <div className="an-table-wrap">
        <table className="an-table">
          <thead>
            <tr>
              {result.columns.map((c) => (
                <th key={c}>{columnLabel(c)}</th>
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
                      cellText(result.columns[ci], cell)
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

/* What the page shows lives in the address (UI review 2026-10-14, #44), so a
 * question can be linked to and Back returns to it: /analysis/<query id>,
 * /analysis/build?subject=…&measure=…&group=…&<filter>=<value>…, and
 * /analysis/console?sql=<the SQL that last ran>. Picking a query or a tab adds a
 * history step; builder changes and console runs replace the current one. */
const RESERVED = ["subject", "measure", "group"];
const MAX_LINK_LENGTH = 6000; // longer console links are not offered (request-size limits)

function builderSearch(subject: Subject, measure: string, group: string, filters: SpecFilter[]): string {
  const query = new URLSearchParams();
  if (subject !== "contracts") query.set("subject", subject);
  if (measure !== "count") query.set("measure", measure);
  if (measure !== "list" && group) query.set("group", group);
  for (const f of filters) query.append(f.field, f.value);
  return query.toString();
}

/** A builder link read back against today's lists; anything unknown is left out and named. */
function parseBuilder(search: string) {
  const query = new URLSearchParams(search);
  const dropped: string[] = [];
  const subjectRaw = query.get("subject");
  const subject: Subject = SUBJECTS.some((s) => s.key === subjectRaw) ? (subjectRaw as Subject) : "contracts";
  if (subjectRaw && subjectRaw !== subject) dropped.push(`subject “${subjectRaw}”`);
  const measures = MEASURES[subject].filter((m) => m.key !== "distinct_people_reviewed" || !isToolHidden("people"));
  const measureRaw = query.get("measure");
  const measure = measures.some((m) => m.key === measureRaw) ? (measureRaw as string) : "count";
  if (measureRaw && measureRaw !== measure) dropped.push(`measure “${measureRaw}”`);
  const groupRaw = query.get("group");
  const group = GROUPS[subject].some((g) => g.key === groupRaw) ? (groupRaw as string) : "";
  if (groupRaw && groupRaw !== group) dropped.push(`grouping “${groupRaw}”`);
  const filters: SpecFilter[] = [];
  for (const [key, value] of query.entries()) {
    if (RESERVED.includes(key)) continue;
    const def = FILTER_DEFS[subject].find((d) => d.key === key);
    if (def) filters.push({ field: key, value: def.value ? value : "" });
    else dropped.push(`filter “${key}”`);
  }
  return { subject, measure, group, filters, dropped };
}

function consolePath(sql: string): string {
  return `/analysis/console?${new URLSearchParams({ sql }).toString()}`;
}

const SAMPLE_SQL = "SELECT cu.currency, COUNT(*) AS contracts, ROUND(AVG(CAST(c.total AS REAL))) AS avg_capital\nFROM contract c JOIN currency cu ON cu.currency_id = c.currency_id\nWHERE c.is_deleted = 0 AND c.total GLOB '[0-9]*'\nGROUP BY cu.currency_id\nORDER BY contracts DESC";

export default function Analysis() {
  const navigate = useNavigate();
  const location = useLocation();
  const { view } = useParams<{ view?: string }>();
  const mode: "library" | "builder" | "console" = view === "build" ? "builder" : view === "console" ? "console" : "library";
  const selectedId = mode === "library" ? (view ?? null) : null;

  // library state
  const [queries, setQueries] = useState<AnalysisQuery[]>([]);
  const [libraryFailed, setLibraryFailed] = useState(false);
  const [libraryAttempt, setLibraryAttempt] = useState(0);
  const selected = useMemo(
    () => (selectedId ? (queries.find((q) => q.id === selectedId) ?? null) : null),
    [selectedId, queries],
  );
  const [libResult, setLibResult] = useState<AnalysisResult | null>(null);
  const beginLib = useLatest();
  const beginBuild = useLatest();
  const [libRunning, setLibRunning] = useState(false);
  const [error, setError] = useState("");

  // builder state
  const [subject, setSubject] = useState<Subject>("contracts");
  const [measure, setMeasure] = useState("count");
  const [filters, setFilters] = useState<SpecFilter[]>([]);
  const [group, setGroup] = useState("");
  const [buildResult, setBuildResult] = useState<{ result: AnalysisResult; chart: AnalysisChart; sql: string } | null>(null);
  const [buildError, setBuildError] = useState("");

  const [dropped, setDropped] = useState<string[]>([]);

  // console state
  const [sqlText, setSqlText] = useState(SAMPLE_SQL);
  const [consoleResult, setConsoleResult] = useState<AnalysisResult | null>(null);
  const [consoleError, setConsoleError] = useState("");
  const [consoleRunning, setConsoleRunning] = useState(false);
  const [ranSql, setRanSql] = useState(""); // the SQL behind the result (and its link), not the edited text
  const beginConsole = useLatest();

  // Where each tab was, so switching tabs returns there (and the address follows).
  const builderWritten = useRef<string | null>(null);
  const consoleWritten = useRef<string | null>(null);
  const lastLibrary = useRef<string | null>(null);
  useEffect(() => {
    if (selectedId) lastLibrary.current = selectedId;
  }, [selectedId]);

  const runConsole = (sql: string, fromLink = false) => {
    const fresh = beginConsole();
    setConsoleRunning(true);
    setConsoleError("");
    if (!fromLink) {
      const path = consolePath(sql);
      if (path.length <= MAX_LINK_LENGTH) {
        consoleWritten.current = path.slice("/analysis/console?".length);
        navigate(path, { replace: true });
      } else {
        consoleWritten.current = ""; // too long to carry: the address must not keep an older query
        navigate("/analysis/console", { replace: true });
      }
    }
    runAnalysisLibrary(sql)
      .then((r) => {
        if (!fresh()) return;
        setConsoleResult(r);
        setRanSql(sql);
        setConsoleError("");
      })
      .catch((e: Error) => {
        if (!fresh()) return;
        setConsoleError(e.message);
        setConsoleResult(null);
      })
      .finally(() => fresh() && setConsoleRunning(false));
  };

  // Read a link (or Back/Forward) into the builder or the console. Our own
  // address updates are remembered, so they never re-read themselves.
  const search = location.search.replace(/^\?/, "");
  useEffect(() => {
    if (mode === "builder" && search !== builderWritten.current) {
      const spec = parseBuilder(search);
      setSubject(spec.subject);
      setMeasure(spec.measure);
      setGroup(spec.group);
      setFilters(spec.filters);
      setDropped(spec.dropped);
      builderWritten.current = builderSearch(spec.subject, spec.measure, spec.group, spec.filters);
    }
    if (mode === "console" && search !== consoleWritten.current) {
      consoleWritten.current = search;
      const sql = new URLSearchParams(search).get("sql");
      if (sql) {
        setSqlText(sql);
        runConsole(sql, true);
      }
    }
    // runConsole is recreated each render; the address is the only trigger here
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, search]);

  useEffect(() => {
    setLibraryFailed(false);
    loadAnalysisLibrary()
      .then((d) => setQueries(d.queries))
      .catch(() => setLibraryFailed(true)); // shown in the rail, where the library would be
  }, [libraryAttempt]);

  const ranFor = useRef<string | null>(null);
  useEffect(() => {
    if (!selected || ranFor.current === selected.id) return;
    ranFor.current = selected.id;
    setLibRunning(true);
    setError("");
    setLibResult(null);
    const fresh = beginLib();
    runAnalysisLibrary(selected.sql)
      .then((r) => fresh() && setLibResult(r))
      .catch((e: Error) => {
        if (!fresh()) return;
        setError(e.message);
        ranFor.current = null; // reopening the query tries again
      })
      .finally(() => fresh() && setLibRunning(false));
  }, [selected, beginLib]);

  const filterDefs = FILTER_DEFS[subject];
  const incomplete = filters.some((f) => {
    const def = filterDefs.find((d) => d.key === f.field);
    return def?.value && !f.value.trim();
  });

  // The builder's address follows its controls (replacing the current entry).
  const currentBuild = builderSearch(subject, measure, group, filters);
  useEffect(() => {
    if (mode !== "builder" || currentBuild === builderWritten.current) return;
    const handle = window.setTimeout(() => {
      builderWritten.current = currentBuild;
      setDropped([]);
      navigate(`/analysis/build${currentBuild ? `?${currentBuild}` : ""}`, { replace: true });
    }, 350);
    return () => window.clearTimeout(handle);
  }, [mode, currentBuild, navigate]);

  // auto-run the builder (debounced) whenever the spec is complete
  useEffect(() => {
    if (mode !== "builder" || incomplete) return;
    const handle = window.setTimeout(() => {
      setBuildError("");
      const fresh = beginBuild();
      runAnalysisBuild({ subject, measure, filters, group_by: measure === "list" ? "" : group })
        .then((r) => fresh() && setBuildResult({ result: { columns: r.columns, rows: r.rows, row_count: r.row_count, truncated: r.truncated }, chart: r.chart, sql: r.sql }))
        .catch((e: Error) => {
          if (!fresh()) return;
          setBuildError(e.message);
          setBuildResult(null);
        });
    }, 350);
    return () => window.clearTimeout(handle);
  }, [mode, subject, measure, filters, group, incomplete, beginBuild]);

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
            <button
              type="button"
              className={mode === "library" ? "db-tab is-active" : "db-tab"}
              onClick={() => mode !== "library" && navigate(lastLibrary.current ? `/analysis/${lastLibrary.current}` : "/analysis")}
            >
              Library
            </button>
            <button
              type="button"
              className={mode === "builder" ? "db-tab is-active" : "db-tab"}
              onClick={() =>
                mode !== "builder" && navigate(`/analysis/build${builderWritten.current ? `?${builderWritten.current}` : ""}`)
              }
            >
              Build a query
            </button>
            <button
              type="button"
              className={mode === "console" ? "db-tab is-active" : "db-tab"}
              onClick={() =>
                mode !== "console" &&
                navigate(`/analysis/console${consoleWritten.current ? `?${consoleWritten.current}` : ""}`)
              }
            >
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
                      <button
                        type="button"
                        className={selectedId === q.id ? "an-query is-active" : "an-query"}
                        onClick={() => selectedId !== q.id && navigate(`/analysis/${q.id}`)}
                      >
                        <span className="an-query-title">{q.title}</span>
                        {q.description && <span className="an-query-desc muted">{q.description}</span>}
                      </button>
                    </li>
                  ))}
                </ul>
              </section>
            ))}
            {queries.length === 0 &&
              (libraryFailed ? (
                <p className="db-empty error-text">
                  The query library could not be loaded.{" "}
                  <button type="button" className="link-like" onClick={() => setLibraryAttempt((n) => n + 1)}>
                    Try again
                  </button>
                </p>
              ) : (
                <p className="db-empty muted">Loading…</p>
              ))}
          </div>
        ) : mode === "builder" ? (
          <div className="an-library">
            <p className="db-empty muted" style={{ padding: "8px 10px" }}>
              Compose a question from the controls; it runs as you change it.
            </p>
          </div>
        ) : (
          <div className="an-library">
            <p className="db-empty muted" style={{ padding: "8px 10px" }}>
              Write your own SQL for anything the builder can't express.
            </p>
          </div>
        )}
      </aside>

      <section className="db-detail">
        {mode === "library" ? (
          selectedId && !selected ? (
            <div className="an-empty">
              <p className="eyebrow">Analysis</p>
              {queries.length === 0 ? (
                libraryFailed ? (
                  <p className="error-text an-note">
                    The query library could not be loaded. Use Try again in the list.
                  </p>
                ) : (
                  <p className="muted an-note">Loading…</p>
                )
              ) : (
                <>
                  <h2>This query is not in the library</h2>
                  <p className="muted an-note">
                    The link asks for “{selectedId}”, which may have been renamed or removed. Pick a query from the list.
                  </p>
                </>
              )}
            </div>
          ) : !selected ? (
            <div className="an-empty">
              <p className="eyebrow">Analysis</p>
              <h2>Ask a question of the corpus</h2>
              <p className="muted an-note">
                Pick a query from the library, or compose your own under <strong>Build a query</strong>.
              </p>
            </div>
          ) : (
            <div className="an-result">
              <div className="page-head">
                <div>
                  <p className="eyebrow">{selected.group}</p>
                  <h2>{selected.title}</h2>
                </div>
                <CopyLinkButton
                  path={`/analysis/${selected.id}`}
                  title="Copy a link to this query. It runs on the current data when opened."
                />
              </div>
              {selected.description && <p className="muted an-note">{selected.description}</p>}
              {selected.notes && <p className="an-notes">{selected.notes}</p>}
              {error && <p className="error-text">{error}</p>}
              {libRunning && <p className="muted">Running…</p>}
              {libResult && !libRunning && (
                <ResultView result={libResult} chart={selected.chart} sql={selected.sql} csvName={`${selected.id}.csv`} />
              )}
            </div>
          )
        ) : mode === "builder" ? (
          <div className="an-result">
            <div className="page-head">
              <p className="eyebrow">Build a query</p>
              <CopyLinkButton
                path={`/analysis/build${currentBuild ? `?${currentBuild}` : ""}`}
                title="Copy a link to this question. It runs on the current data when opened."
              />
            </div>
            {dropped.length > 0 && (
              <p className="muted an-note">
                Part of this link no longer matches the query builder and was left out: {dropped.join(", ")}.
              </p>
            )}
            <div className="an-builder">
              <div className="an-sentence">
                <span>Show</span>
                <select value={measure} onChange={(e) => setMeasure(e.target.value)}>
                  {MEASURES[subject]
                    .filter((m) => m.key !== "distinct_people_reviewed" || !isToolHidden("people"))
                    .map((m) => (
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
            <div className="page-head">
              <div>
                <p className="eyebrow">SQL console</p>
                <h2>Read-only SQL</h2>
              </div>
              {ranSql && consoleResult && !consoleRunning && (
                <CopyLinkButton
                  path={consolePath(ranSql)}
                  title="Copy a link that runs this SQL on the current data when opened."
                  disabledReason={consolePath(ranSql).length > MAX_LINK_LENGTH ? "Too long to share as a link" : undefined}
                />
              )}
            </div>
            <p className="muted an-note">
              <strong>SELECT</strong> or <strong>WITH</strong> only, one statement at a time. A query containing a
              write word (insert, update, delete, replace and the like) is refused, even inside quoted text or as the
              replace() function. Results are capped at 5,000 rows. Press <kbd>⌘/Ctrl</kbd>+<kbd>Enter</kbd> to run.
            </p>
            <textarea
              className="an-console"
              value={sqlText}
              spellCheck={false}
              onChange={(e) => setSqlText(e.target.value)}
              onKeyDown={(e) => {
                if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
                  e.preventDefault();
                  runConsole(sqlText);
                }
              }}
            />
            <div className="an-toolbar">
              <button type="button" className="pill-button is-primary" onClick={() => runConsole(sqlText)} disabled={consoleRunning || !sqlText.trim()}>
                {consoleRunning ? "Running…" : "Run query"}
              </button>
            </div>
            {consoleError && <p className="error-text">{consoleError}</p>}
            {consoleResult && !consoleRunning && (
              <ResultView result={consoleResult} chart="none" sql={ranSql} csvName="query.csv" />
            )}
          </div>
        )}
      </section>
    </div>
  );
}
