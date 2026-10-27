import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { checkNumber, createDbRecord, findSimilar, loadDbRecord, loadRegisters } from "../api";
import { useLatest } from "../utils/latest";
import type { DbRecord, NumberCheck, RegisterOption, SimilarRow } from "../types";
import LookupCombobox, { lookupCheckBlock, type LookupCheck } from "./LookupCombobox";
import { LEAVE_UNSAVED, useUnsavedChange } from "../utils/unsavedChanges";
import { recordedDate } from "../utils/dates";

const SUB_TYPES = ["termination", "balance", "renewal", "variation"];

/* Creating a DB-native record (contract, or act on a contract).
 *
 * From the Word-corpus freeze onward, new rows are added directly to the
 * database with no Word summary — so the form is anchor-first (register, folio,
 * date pin the act archivally), requires a SOURCE line as the row's provenance,
 * and writes the regest straight into the `document` field. Everything else is
 * enrichment, done afterwards with the inline editors on the record page
 * ("create sparse, enrich in place").
 *
 * Guards, all grounded in the data:
 *  - the register act number doubles as the contract id when free; a TAKEN
 *    number almost always means "this is a later act ON that contract" → the
 *    form offers the add-act redirect instead of an error;
 *  - (folder, folio, date) is a near-unique key in this corpus, so possible
 *    duplicates are surfaced before saving;
 *  - 30% of acts sit in a DIFFERENT register than their parent contract, so
 *    the act form defaults to the parent's register but keeps the picker
 *    prominent.
 */
export default function CreateRecordForm({
  table,
  parentId,
}: {
  table: "contract" | "sub_contract";
  parentId: string | null;
}) {
  const navigate = useNavigate();
  const [registers, setRegisters] = useState<RegisterOption[]>([]);
  const [parent, setParent] = useState<DbRecord | null>(null);

  const [folder, setFolder] = useState("");
  const [customRegister, setCustomRegister] = useState(false);
  const [archive, setArchive] = useState("ASF");
  const [series, setSeries] = useState("");
  const [folio, setFolio] = useState("");
  const [date, setDate] = useState("");
  const [registerNumber, setRegisterNumber] = useState("");
  const [numberCheck, setNumberCheck] = useState<NumberCheck | null>(null);
  const [subType, setSubType] = useState("termination");
  const [endDate, setEndDate] = useState("");
  const [renewalMonths, setRenewalMonths] = useState("");
  const [firmName, setFirmName] = useState("");
  const [activity, setActivity] = useState("");
  const [total, setTotal] = useState("");
  const [docText, setDocText] = useState("");
  const [source, setSource] = useState("");
  const [reviewer, setReviewer] = useState(() => localStorage.getItem("floracco_reviewer") ?? "");
  const [similar, setSimilar] = useState<SimilarRow[]>([]);
  const [similarFailed, setSimilarFailed] = useState(false);
  const beginNumber = useLatest();
  const beginSimilar = useLatest();
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [activityCheck, setActivityCheck] = useState<LookupCheck>("idle");
  const debounce = useRef<number | undefined>(undefined);
  // Typed, unsaved input (the register select is left out: the sub-contract
  // form pre-fills it from the parent).
  const dirty =
    [folio, date, registerNumber, endDate, renewalMonths, firmName, activity, total, docText, source].some(
      (v) => v.trim() !== "",
    ) ||
    (customRegister && (series.trim() !== "" || folder.trim() !== ""));
  useUnsavedChange(dirty && !saving, table === "contract" ? "the new contract" : "the new sub-contract");

  const [registersFailed, setRegistersFailed] = useState(false);
  useEffect(() => {
    loadRegisters()
      .then((response) => setRegisters(response.registers))
      .catch(() => {
        setRegisters([]);
        setRegistersFailed(true); // otherwise the dropdown is just empty
      });
  }, []);

  // The act form anchors on its parent: summary header + register default.
  useEffect(() => {
    if (table !== "sub_contract" || !parentId) return;
    loadDbRecord("contract", parentId)
      .then((record) => {
        setParent(record);
        const parentFolder = record.fields
          .find((f) => f.label.startsWith("Archive"))
          ?.value.split("/")
          .pop()
          ?.trim();
        if (parentFolder) setFolder((current) => current || parentFolder);
      })
      .catch((err: Error) => setError(err.message));
  }, [table, parentId]);

  const selectedRegister = useMemo(
    () => registers.find((r) => r.folder === folder) ?? null,
    [registers, folder],
  );
  const effectiveArchive = customRegister ? archive : selectedRegister?.archive ?? "ASF";
  const effectiveSeries = customRegister ? series : selectedRegister?.series ?? "";

  // Live register-number check (contract mode): free → becomes the id;
  // taken → "add as act on that contract instead".
  useEffect(() => {
    if (table !== "contract" || !registerNumber.trim() || !/^\d+$/.test(registerNumber.trim())) {
      setNumberCheck(null);
      return;
    }
    const n = Number(registerNumber.trim());
    const t = window.setTimeout(() => {
      const fresh = beginNumber();
      checkNumber(n)
        .then((r) => fresh() && setNumberCheck(r))
        .catch(() => fresh() && setNumberCheck(null));
    }, 250);
    return () => window.clearTimeout(t);
  }, [table, registerNumber]);

  // Live duplicate warning on the (folder, folio, date) anchor.
  useEffect(() => {
    window.clearTimeout(debounce.current);
    if (!folder.trim() || (!folio.trim() && !date.trim())) {
      beginSimilar();
      setSimilar([]);
      setSimilarFailed(false);
      return;
    }
    debounce.current = window.setTimeout(() => {
      const fresh = beginSimilar();
      findSimilar(folder.trim(), folio.trim(), date.trim())
        .then((response) => {
          if (!fresh()) return;
          setSimilar(response.rows);
          setSimilarFailed(false);
        })
        .catch(() => {
          if (!fresh()) return;
          setSimilar([]);
          setSimilarFailed(true); // a failed check must be VISIBLE, not "no duplicates"
        });
    }, 300);
    return () => window.clearTimeout(debounce.current);
  }, [folder, folio, date]);

  const submit = useCallback(async () => {
    setError("");
    if (!reviewer.trim()) return setError("Your initials are needed.");
    if (!folder.trim()) return setError("Pick the register.");
    if (!folio.trim()) return setError("The folio is required.");
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) return setError("Registration date must be YYYY-MM-DD.");
    if (docText.trim().length < 10) return setError("The narrative is required (at least 10 characters).");
    if (source.trim().length < 3)
      return setError("A source line is required (at least 3 characters).");
    if (table === "contract") {
      const wait = lookupCheckBlock(activityCheck, "the economic activity");
      if (wait) return setError(wait);
    }
    localStorage.setItem("floracco_reviewer", reviewer.trim());
    setSaving(true);
    try {
      let result;
      if (table === "contract") {
        result = await createDbRecord("contract", {
          reviewer: reviewer.trim(),
          source: source.trim(),
          archive: effectiveArchive,
          series: effectiveSeries,
          folder: folder.trim(),
          folio: folio.trim(),
          registration_date: date,
          register_number: registerNumber.trim() ? Number(registerNumber.trim()) : null,
          firm_name: firmName,
          economic_activity: activity,
          total: total.trim() ? Number(total.trim()) : null,
          document: docText,
        });
      } else {
        result = await createDbRecord("sub_contract", {
          reviewer: reviewer.trim(),
          source: source.trim(),
          main_contract_id: Number(parentId),
          sub_type: subType,
          archive: effectiveArchive,
          series: effectiveSeries,
          folder: folder.trim(),
          folio: folio.trim(),
          registration_date: date,
          end_date: subType === "termination" ? endDate : "",
          renewal_months:
            subType === "renewal" && renewalMonths.trim() ? Number(renewalMonths.trim()) : null,
          sub_firm_name: firmName,
          document: docText,
        });
      }
      navigate(`/database/${table}/${result.id}`, LEAVE_UNSAVED); // saved: nothing to warn about
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSaving(false);
    }
  }, [
    table, parentId, reviewer, folder, folio, date, docText, source, registerNumber,
    firmName, activity, total, subType, endDate, renewalMonths,
    effectiveArchive, effectiveSeries, navigate, activityCheck,
  ]);

  const numberTaken = numberCheck && !numberCheck.free ? numberCheck.existing : null;

  return (
    <article className="db-record create-form">
      <header className="db-record-head">
        <div className="db-record-titles">
          <p className="eyebrow">
            {table === "contract" ? "New contract" : "New sub-contract"}
          </p>
          <h2>{table === "contract" ? "Add a contract" : `Add a sub-contract${parent ? ` · ${parent.title}` : ""}`}</h2>
          {table === "sub_contract" && parent && (
            <p className="muted create-parent-line">
              Parent: <strong>{parent.title}</strong> ({parent.subtitle}) — sub-contracts often sit in a{" "}
              <em>later</em> register than their contract; check the register below.
            </p>
          )}
        </div>
      </header>

      <div className="create-grid">
        <label className="create-field">
          <span className="create-label">Register</span>
          {!customRegister ? (
            <select
              value={folder}
              onChange={(e) => {
                if (e.target.value === "__other__") {
                  setCustomRegister(true);
                  setFolder("");
                } else {
                  setFolder(e.target.value);
                }
              }}
            >
              <option value="">— choose the register —</option>
              {registers.map((r) => (
                <option key={r.folder} value={r.folder}>
                  {r.series || r.archive} {r.folder} ({r.contracts} contracts)
                </option>
              ))}
              <option value="__other__">other register…</option>
            </select>
          ) : (
            <div className="create-register-custom">
              <input value={archive} onChange={(e) => setArchive(e.target.value)} placeholder="archive (ASF)" />
              <input value={series} onChange={(e) => setSeries(e.target.value)} placeholder="series" />
              <input value={folder} onChange={(e) => setFolder(e.target.value)} placeholder="folder" />
              <button type="button" className="field-fix" onClick={() => setCustomRegister(false)}>
                back to the list
              </button>
            </div>
          )}
          {registersFailed && !customRegister && (
            <span className="lookup-status error-text">
              The list of registers could not be loaded. Choose “other register…” to type it in.
            </span>
          )}
        </label>

        <label className="create-field">
          <span className="create-label">Folio</span>
          <input value={folio} onChange={(e) => setFolio(e.target.value)} placeholder="e.g. 26v or 9r-v" />
        </label>

        <label className="create-field">
          <span className="create-label">Registration date</span>
          <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
        </label>

        {table === "contract" ? (
          <label className="create-field">
            <span className="create-label">Contract number in the register (optional)</span>
            <input
              value={registerNumber}
              onChange={(e) => setRegisterNumber(e.target.value)}
              placeholder="the [Nuova] number in the register"
            />
            {numberCheck?.free && registerNumber.trim() && (
              <span className="lookup-status is-reuse">Free: it will become the contract number.</span>
            )}
          </label>
        ) : (
          <label className="create-field">
            <span className="create-label">Sub-contract type</span>
            <select value={subType} onChange={(e) => setSubType(e.target.value)}>
              {SUB_TYPES.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </label>
        )}

        {table === "sub_contract" && subType === "termination" && (
          <label className="create-field">
            <span className="create-label">End date (if stated)</span>
            <input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} />
          </label>
        )}
        {table === "sub_contract" && subType === "renewal" && (
          <label className="create-field">
            <span className="create-label">Renewal (months)</span>
            <input type="number" value={renewalMonths} onChange={(e) => setRenewalMonths(e.target.value)} />
          </label>
        )}

        <label className="create-field">
          <span className="create-label">{table === "contract" ? "Firm name (optional)" : "Firm name (only if it differs from the parent)"}</span>
          <input value={firmName} onChange={(e) => setFirmName(e.target.value)} placeholder="sotto nome di…" />
        </label>

        {table === "contract" && (
          <>
            <LookupCombobox
              kind="economic_activity"
              label="Economic activity (optional — as stated in the document)"
              value={activity}
              onChange={setActivity}
              placeholder="e.g. negozio di arte di seta"
              onCheckChange={setActivityCheck}
            />
            <label className="create-field">
              <span className="create-label">Total capital (optional, number only)</span>
              <input type="number" value={total} onChange={(e) => setTotal(e.target.value)} />
            </label>
          </>
        )}
      </div>

      {numberTaken && (
        <div className="create-warning">
          <strong>Contract {numberTaken.id} already exists</strong> — {numberTaken.title} ·{" "}
          {recordedDate(numberTaken.date) || "no date"} · c. {numberTaken.folio} · reg. {numberTaken.folder}. If what you are
          adding is a sub-contract <em>of</em> it (modifica, disdetta, …):
          <button
            type="button"
            className="pill-button"
            onClick={() => navigate(`/database/sub_contract/new?parent=${numberTaken.id}`, LEAVE_UNSAVED)}
          >
            Add as sub-contract of contract {numberTaken.id} →
          </button>
        </div>
      )}

      {similarFailed && (
        <p className="error-text">
          Could not check for an existing contract at this register and folio. Check your connection before
          creating.
        </p>
      )}
      {similar.length > 0 && (
        <div className="create-warning">
          <strong>Possible duplicates in this register</strong> (check before creating):
          <ul>
            {similar.map((row) => (
              <li key={row.row_id}>
                <button type="button" className="link-like" onClick={() => navigate(`/database/${row.table}/${row.id}`)}>
                  {row.title}
                </button>{" "}
                · {recordedDate(row.date) || "no date"} · c. {row.folio} <span className="muted">(same {row.match})</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <label className="create-field create-narrative">
        <span className="create-label">Narrative (regest)</span>
        <textarea
          rows={10}
          value={docText}
          onChange={(e) => setDocText(e.target.value)}
          placeholder="Summarize the document as the Word regesti do: parties, capital, activity, terms…"
        />
      </label>

      <label className="create-field">
        <span className="create-label">Source (required, since there is no Word summary)</span>
        <input
          value={source}
          onChange={(e) => setSource(e.target.value)}
          placeholder="e.g. ASF Mercanzia 10859, c. 12r, examined July 2026"
        />
      </label>

      <div className="inline-editor-row create-actions">
        <input
          className="inline-editor-initials"
          value={reviewer}
          onChange={(e) => setReviewer(e.target.value)}
          placeholder="initials"
        />
        <button
          type="button"
          className="pill-button"
          // Cancel is a deliberate discard: back to where the form is opened from, without asking.
          onClick={() =>
            navigate(
              table === "sub_contract" && parentId ? `/database/contract/${parentId}` : "/database/contract",
              LEAVE_UNSAVED,
            )
          }
          disabled={saving}
        >
          Cancel
        </button>
        <button type="button" className="pill-button is-active" onClick={submit} disabled={saving}>
          {saving ? "Creating…" : table === "contract" ? "Create contract" : "Create sub-contract"}
        </button>
      </div>
      {error && <p className="error-text">{error}</p>}
      <p className="inline-editor-foot muted">
        Add partners, places and further details afterwards on the record page.
      </p>
    </article>
  );
}
