import { useCallback, useEffect, useRef, useState } from "react";
import { createInvestor, loadContractInvestments, sameSurname, searchPersonsRich } from "../api";
import type { ContractInvestment, PersonHit } from "../types";
import LookupCombobox from "./LookupCombobox";

/* Add a person to a contract — person + role + capital, one audited save.
 *
 * Data-grounded behavior (audit 2026-06-12):
 *  - search-first across ALL persons (26% of appearing persons recur), each hit
 *    disambiguated by patronymic · residence · appearances;
 *  - a new person only after the same-surname wall (1,307 identical-name pairs
 *    already exist in the corpus);
 *  - role speaks both vocabularies (accomandatario/gp, accomandante/lp; the
 *    historical mapping is FT-review-pending per the glossary);
 *  - GP cash defaults to 0 — the norm (64%): the accomandatario contributes
 *    industria, not money;
 *  - joint tranches (11% of investments) join an existing investment instead
 *    of creating one; is_joint is derived, never asked;
 *  - the panel stays open after a save: every contract needs ≥2 investors.
 */
export default function AddInvestorPanel({
  contractId,
  contractTitle,
  onSaved,
  onClose,
}: {
  contractId: string;
  contractTitle: string;
  onSaved: (message: string) => void;
  onClose: () => void;
}) {
  // WHO
  const [personQuery, setPersonQuery] = useState("");
  const [personHits, setPersonHits] = useState<PersonHit[]>([]);
  const [pickedPerson, setPickedPerson] = useState<PersonHit | null>(null);
  const [creatingPerson, setCreatingPerson] = useState(false);
  const [npFirst, setNpFirst] = useState("");
  const [npPatronymic, setNpPatronymic] = useState("");
  const [npLast, setNpLast] = useState("");
  const [npWoman, setNpWoman] = useState(false);
  const [surnameHits, setSurnameHits] = useState<PersonHit[]>([]);
  const [confirmedNew, setConfirmedNew] = useState(false);
  // ROLE & CAPITAL
  const [mode, setMode] = useState<"own" | "join">("own");
  const [role, setRole] = useState<"gp" | "lp">("lp");
  const [cash, setCash] = useState("");
  const [cashUnspecified, setCashUnspecified] = useState(false);
  const [nonCash, setNonCash] = useState("");
  const [firmName, setFirmName] = useState("");
  const [investments, setInvestments] = useState<ContractInvestment[]>([]);
  const [joinId, setJoinId] = useState("");
  // DETAILS
  const [title, setTitle] = useState("");
  const [residence, setResidence] = useState("");
  const [origin, setOrigin] = useState("");
  const [profession, setProfession] = useState("");
  const [viaProxy, setViaProxy] = useState(false);
  const [flags, setFlags] = useState({
    citizen_florence: false,
    is_widow: false,
    is_guardian: false,
    is_jewish: false,
    is_convert: false,
    heirs: false,
    heirs_of: false,
    and_c: false,
  });
  const [note, setNote] = useState("");
  const [reviewer, setReviewer] = useState(() => localStorage.getItem("floracco_reviewer") ?? "");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const debounce = useRef<number | undefined>(undefined);

  const refreshInvestments = useCallback(() => {
    loadContractInvestments(contractId)
      .then((response) => setInvestments(response.investments))
      .catch(() => setInvestments([]));
  }, [contractId]);

  useEffect(() => {
    refreshInvestments();
  }, [refreshInvestments]);

  // Person search (whole DB).
  useEffect(() => {
    window.clearTimeout(debounce.current);
    if (pickedPerson || creatingPerson || personQuery.trim().length < 2) {
      setPersonHits([]);
      return;
    }
    debounce.current = window.setTimeout(() => {
      searchPersonsRich(personQuery.trim())
        .then((response) => setPersonHits(response.results))
        .catch(() => setPersonHits([]));
    }, 220);
    return () => window.clearTimeout(debounce.current);
  }, [personQuery, pickedPerson, creatingPerson]);

  // The same-surname wall while creating a new person.
  useEffect(() => {
    if (!creatingPerson || npLast.trim().length < 2) {
      setSurnameHits([]);
      setConfirmedNew(false);
      return;
    }
    const t = window.setTimeout(() => {
      sameSurname(npLast.trim())
        .then((response) => {
          setSurnameHits(response.results);
          setConfirmedNew(false);
        })
        .catch(() => setSurnameHits([]));
    }, 300);
    return () => window.clearTimeout(t);
  }, [creatingPerson, npLast]);

  const resetWho = () => {
    setPersonQuery("");
    setPersonHits([]);
    setPickedPerson(null);
    setCreatingPerson(false);
    setNpFirst("");
    setNpPatronymic("");
    setNpLast("");
    setNpWoman(false);
    setSurnameHits([]);
    setConfirmedNew(false);
  };

  const submit = async () => {
    setError("");
    if (!reviewer.trim()) return setError("Your initials are needed.");
    if (!pickedPerson && !creatingPerson) return setError("Pick a person (or create one after searching).");
    if (creatingPerson && !(npFirst.trim() || npLast.trim())) return setError("The new person needs a name.");
    if (creatingPerson && surnameHits.length > 0 && !confirmedNew)
      return setError(`Confirm that none of the ${surnameHits.length} existing ${npLast.trim()} is this person.`);
    if (mode === "join" && !joinId) return setError("Pick the tranche this person shares.");
    localStorage.setItem("floracco_reviewer", reviewer.trim());
    setSaving(true);
    try {
      const result = await createInvestor({
        reviewer: reviewer.trim(),
        contract_id: Number(contractId),
        person_id: pickedPerson ? Number(pickedPerson.person_id) : null,
        new_person: creatingPerson
          ? { first_name: npFirst, father_mother: npPatronymic, last_name: npLast, is_woman: npWoman }
          : null,
        role: mode === "own" ? role : "",
        join_investment_id: mode === "join" ? Number(joinId) : null,
        investment_cash: cash.trim() ? Number(cash.trim()) : null,
        cash_unspecified: cashUnspecified,
        investment_non_cash: nonCash,
        partnership_name: firmName,
        title,
        residence,
        origin,
        profession,
        via_proxy: viaProxy,
        ...flags,
        note,
      });
      const who = pickedPerson?.display_name || `${npFirst} ${npLast}`.trim();
      onSaved(
        `Added ${who}${result.person_created ? " (new person)" : ""} as ${
          mode === "join" ? "joint investor" : role
        }.`,
      );
      resetWho();
      setCash("");
      setCashUnspecified(false);
      setNonCash("");
      setFirmName("");
      // Person-specific details must not silently carry over to the next
      // investor (a sticky residence misattributes; caught in testing). The
      // title is deliberately kept — honorifics repeat run after run.
      setResidence("");
      setOrigin("");
      setProfession("");
      setViaProxy(false);
      setFlags({
        citizen_florence: false,
        is_widow: false,
        is_guardian: false,
        is_jewish: false,
        is_convert: false,
        heirs: false,
        heirs_of: false,
        and_c: false,
      });
      setNote("");
      setMode("own");
      setJoinId("");
      refreshInvestments();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const flagLabels: [keyof typeof flags, string][] = [
    ["citizen_florence", "citizen of Florence"],
    ["is_widow", "widow (vedova)"],
    ["is_guardian", "guardian / tutor"],
    ["is_jewish", "Jewish (as stated)"],
    ["is_convert", "convert"],
    ["heirs", "& heirs (ed eredi)"],
    ["heirs_of", "heirs of (eredi di)"],
    ["and_c", "& C. (e compagni)"],
  ];

  return (
    <div className="add-investor-panel">
      <div className="add-investor-head">
        <strong>Add an investor</strong>
        <span className="muted">to {contractTitle}</span>
        <button type="button" className="drawer-close" onClick={onClose} aria-label="Close">
          ×
        </button>
      </div>

      {/* WHO */}
      <div className="add-investor-step">
        <span className="create-label">Who</span>
        {pickedPerson ? (
          <div className="picked-person">
            <strong>{pickedPerson.display_name}</strong>
            {pickedPerson.father_mother && <span className="muted"> di {pickedPerson.father_mother}</span>}
            <span className="muted">
              {" "}
              · {pickedPerson.residences || "no residence recorded"} · appears on {pickedPerson.appearances}{" "}
              contract{pickedPerson.appearances === 1 ? "" : "s"}
            </span>
            <button type="button" className="field-fix" onClick={resetWho}>
              change
            </button>
          </div>
        ) : creatingPerson ? (
          <div className="new-person-form">
            <div className="create-grid">
              <label className="create-field">
                <span className="create-label">First name</span>
                <input value={npFirst} onChange={(e) => setNpFirst(e.target.value)} />
              </label>
              <label className="create-field">
                <span className="create-label">Patronymic (di …)</span>
                <input value={npPatronymic} onChange={(e) => setNpPatronymic(e.target.value)} placeholder="e.g. Giorgio di Niccolò" />
              </label>
              <label className="create-field">
                <span className="create-label">Last name</span>
                <input value={npLast} onChange={(e) => setNpLast(e.target.value)} />
              </label>
              <label className="create-field add-investor-checkbox">
                <span className="create-label">&nbsp;</span>
                <span>
                  <input type="checkbox" checked={npWoman} onChange={(e) => setNpWoman(e.target.checked)} /> woman
                </span>
              </label>
            </div>
            {surnameHits.length > 0 && (
              <div className="create-warning">
                <strong>
                  {surnameHits.length} existing person{surnameHits.length === 1 ? "" : "s"} named “{npLast.trim()}”
                </strong>{" "}
                — is yours one of these?
                <ul>
                  {surnameHits.slice(0, 6).map((hit) => (
                    <li key={hit.person_id}>
                      <button
                        type="button"
                        className="link-like"
                        onClick={() => {
                          setPickedPerson(hit);
                          setCreatingPerson(false);
                        }}
                      >
                        {hit.display_name}
                      </button>
                      {hit.father_mother ? ` di ${hit.father_mother}` : ""}{" "}
                      <span className="muted">
                        · {hit.residences || "—"} · {hit.appearances} contract{hit.appearances === 1 ? "" : "s"}
                      </span>
                    </li>
                  ))}
                </ul>
                <label className="confirm-new-person">
                  <input type="checkbox" checked={confirmedNew} onChange={(e) => setConfirmedNew(e.target.checked)} />{" "}
                  None of these — create a new person.
                </label>
              </div>
            )}
            <button type="button" className="field-fix" onClick={resetWho}>
              ← back to search
            </button>
          </div>
        ) : (
          <div className="person-search">
            <input
              value={personQuery}
              onChange={(e) => setPersonQuery(e.target.value)}
              placeholder="Search all people — name, patronymic, or id…"
              autoComplete="off"
            />
            {personHits.length > 0 && (
              <ul className="lookup-suggestions person-suggestions">
                {personHits.map((hit) => (
                  <li key={hit.person_id}>
                    <button type="button" onMouseDown={(e) => e.preventDefault()} onClick={() => setPickedPerson(hit)}>
                      <span className="lookup-value">
                        {hit.display_name}
                        {hit.father_mother ? <span className="muted"> di {hit.father_mother}</span> : null}
                      </span>
                      <span className="lookup-used muted">
                        {hit.residences ? `${hit.residences} · ` : ""}
                        {hit.appearances} contr.
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
            {personQuery.trim().length >= 2 && (
              <button type="button" className="field-fix" onClick={() => {
                setCreatingPerson(true);
                const words = personQuery.trim().split(/\s+/);
                setNpFirst(words[0] ?? "");
                setNpLast(words.slice(1).join(" "));
              }}>
                None of these — create a new person
              </button>
            )}
          </div>
        )}
      </div>

      {/* ROLE & CAPITAL */}
      <div className="add-investor-step">
        <span className="create-label">Role &amp; capital</span>
        {investments.length > 0 && (
          <div className="tranche-mode">
            <label>
              <input type="radio" checked={mode === "own"} onChange={() => setMode("own")} /> own capital tranche
            </label>
            <label>
              <input type="radio" checked={mode === "join"} onChange={() => setMode("join")} /> shares an existing
              tranche (joint)
            </label>
          </div>
        )}
        {mode === "own" ? (
          <div className="create-grid">
            <label className="create-field">
              <span className="create-label">Role</span>
              <select
                value={role}
                onChange={(e) => {
                  const next = e.target.value as "gp" | "lp";
                  setRole(next);
                  if (next === "gp" && !cash.trim()) setCash("0");
                }}
                title="Historical mapping accomandatario↔gp / accomandante↔lp is pending FT review (glossary)"
              >
                <option value="lp">accomandante (lp) — provides capital</option>
                <option value="gp">accomandatario (gp) — runs the firm</option>
              </select>
            </label>
            <label className="create-field">
              <span className="create-label">Cash amount</span>
              <input
                type="number"
                value={cash}
                onChange={(e) => setCash(e.target.value)}
                disabled={cashUnspecified}
                placeholder={role === "gp" ? "0 — contributes work (the norm)" : "e.g. 2000"}
              />
              <span className="lookup-status is-new">
                {role === "gp" ? "0 = contributes industria (64% of GPs)." : "Omit submultiples (input rule)."}
                {" "}
                <label className="inline-check">
                  <input type="checkbox" checked={cashUnspecified} onChange={(e) => setCashUnspecified(e.target.checked)} />{" "}
                  unspecified in the document
                </label>
              </span>
            </label>
            <label className="create-field">
              <span className="create-label">Non-cash (in kind, optional)</span>
              <input value={nonCash} onChange={(e) => setNonCash(e.target.value)} placeholder="merci, crediti…" />
            </label>
            <label className="create-field">
              <span className="create-label">Investing as a firm (optional)</span>
              <input value={firmName} onChange={(e) => setFirmName(e.target.value)} placeholder="ragione — e.g. X e compagni di banco" />
            </label>
          </div>
        ) : (
          <label className="create-field">
            <span className="create-label">Which tranche does this person share?</span>
            <select value={joinId} onChange={(e) => setJoinId(e.target.value)}>
              <option value="">— pick the tranche —</option>
              {investments.map((inv) => (
                <option key={inv.investment_id} value={inv.investment_id}>
                  {inv.type} · {inv.cash == null ? "unspecified" : inv.cash} ·{" "}
                  {inv.partnership_name || inv.members || `investment ${inv.investment_id}`}
                </option>
              ))}
            </select>
            <span className="lookup-status is-new">
              Role and capital come from the shared tranche; “joint” is recorded on every member automatically.
            </span>
          </label>
        )}
      </div>

      {/* DETAILS */}
      <div className="add-investor-step">
        <span className="create-label">Details (as stated in the document)</span>
        <div className="create-grid">
          <LookupCombobox kind="title" label="Title (signor, magnifico…)" value={title} onChange={setTitle} />
          <LookupCombobox kind="place" label="Residence" value={residence} onChange={setResidence} placeholder="e.g. Livorno" />
          <label className="create-field">
            <span className="create-label">Profession (only if explicit)</span>
            <input value={profession} onChange={(e) => setProfession(e.target.value)} placeholder="e.g. battiloro" />
          </label>
          <label className="create-field add-investor-checkbox">
            <span className="create-label">&nbsp;</span>
            <span>
              <input type="checkbox" checked={viaProxy} onChange={(e) => setViaProxy(e.target.checked)} /> acting via
              proxy
            </span>
          </label>
        </div>
        <details className="add-investor-more">
          <summary>more attributes</summary>
          <div className="create-grid">
            <LookupCombobox kind="place" label="Place of origin" value={origin} onChange={setOrigin} />
            <div className="create-field add-investor-flags">
              {flagLabels.map(([key, label]) => (
                <label key={key} className="inline-check">
                  <input
                    type="checkbox"
                    checked={flags[key]}
                    onChange={(e) => setFlags({ ...flags, [key]: e.target.checked })}
                  />{" "}
                  {label}
                </label>
              ))}
            </div>
          </div>
        </details>
      </div>

      <div className="inline-editor-row create-actions">
        <input
          className="inline-editor-initials"
          value={reviewer}
          onChange={(e) => setReviewer(e.target.value)}
          placeholder="initials"
        />
        <input
          className="actionbar-note add-investor-note"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="note (optional)"
        />
        <button type="button" className="pill-button is-active" onClick={submit} disabled={saving}>
          {saving ? "Adding…" : "Add investor"}
        </button>
      </div>
      {error && <p className="error-text">{error}</p>}
      <p className="inline-editor-foot muted">
        Saves person, role, capital, and the group link as one audited operation group (replay-safe). The
        panel stays open — every contract needs at least two investors.
      </p>
    </div>
  );
}
