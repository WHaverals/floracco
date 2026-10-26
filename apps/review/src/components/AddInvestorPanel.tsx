import { useCallback, useEffect, useRef, useState } from "react";
import { createInvestor, loadContractInvestments, sameSurname, searchPersonsRich } from "../api";
import { useLatest } from "../utils/latest";
import { aRole, roleLabel } from "../utils/roles";
import { useUnsavedChange } from "../utils/unsavedChanges";
import type { ContractInvestment, PersonHit } from "../types";
import LookupCombobox, { lookupCheckBlock, type LookupCheck } from "./LookupCombobox";
import IdentityHintLines from "./person-linkage/IdentityHintLines";

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
 *  - searches FAIL CLOSED (UI review 2026-10-14, item 5): "create a new person"
 *    is offered only once the search for the current text has come back, and a
 *    phrase box still searching (or failed) holds the save.
 */
// The person search's page size: the server default in review_server.person_search.
const PERSON_SEARCH_LIMIT = 12;

export default function AddInvestorPanel({
  contractId,
  contractTitle,
  onSaved,
  onClose,
  presetInvestmentId = null,
}: {
  contractId: string;
  contractTitle: string;
  onSaved: (message: string) => void;
  onClose: () => void;
  /** Opened from "Attach a person" on a stake nobody holds: start in join mode on that stake. */
  presetInvestmentId?: string | null;
}) {
  // WHO
  const [personQuery, setPersonQuery] = useState("");
  const [personHits, setPersonHits] = useState<PersonHit[]>([]);
  const [pickedPerson, setPickedPerson] = useState<PersonHit | null>(null);
  const [creatingPerson, setCreatingPerson] = useState(false);
  // The person search's answer, tagged with the text it answers, so a slow or
  // failed search never reads as "nobody matches".
  const [personAnswer, setPersonAnswer] = useState<{ q: string; ok: boolean } | null>(null);
  const [personRetry, setPersonRetry] = useState(0);
  const [npFirst, setNpFirst] = useState("");
  const [npPatronymic, setNpPatronymic] = useState("");
  const [npLast, setNpLast] = useState("");
  const [npWoman, setNpWoman] = useState(false);
  const [surnameHits, setSurnameHits] = useState<PersonHit[]>([]);
  const [surnameTotal, setSurnameTotal] = useState(0);
  const [surnameRetry, setSurnameRetry] = useState(0);
  const [confirmedNew, setConfirmedNew] = useState(false);
  // ROLE & CAPITAL
  const [mode, setMode] = useState<"own" | "join">(presetInvestmentId ? "join" : "own");
  const [role, setRole] = useState<"gp" | "lp">("lp");
  const [cash, setCash] = useState("");
  const [cashUnspecified, setCashUnspecified] = useState(false);
  const [nonCash, setNonCash] = useState("");
  const [firmName, setFirmName] = useState("");
  const [investments, setInvestments] = useState<ContractInvestment[]>([]);
  const [investmentsFailed, setInvestmentsFailed] = useState(false);
  const [joinId, setJoinId] = useState(presetInvestmentId ?? "");
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
  const beginPersons = useLatest();
  const beginSurname = useLatest();
  // The same-surname wall must FAIL CLOSED (finding D4): a failed or still-
  // pending check blocks person creation instead of silently allowing it.
  const [surnameCheck, setSurnameCheck] = useState<"idle" | "pending" | "ok" | "failed">("idle");
  const [titleCheck, setTitleCheck] = useState<LookupCheck>("idle");
  const [residenceCheck, setResidenceCheck] = useState<LookupCheck>("idle");
  const [originCheck, setOriginCheck] = useState<LookupCheck>("idle");

  // Typed, unsaved input (the title is left out: it is kept after a save on purpose).
  const dirty =
    Boolean(pickedPerson) ||
    creatingPerson ||
    personQuery.trim() !== "" ||
    cash.trim() !== "" ||
    nonCash.trim() !== "" ||
    firmName.trim() !== "" ||
    residence.trim() !== "" ||
    origin.trim() !== "" ||
    profession.trim() !== "" ||
    note.trim() !== "" ||
    viaProxy ||
    Object.values(flags).some(Boolean);
  useUnsavedChange(dirty && !saving, "the new partner", ["add-partner"]);

  const refreshInvestments = useCallback(() => {
    loadContractInvestments(contractId)
      .then((response) => {
        setInvestments(response.investments);
        setInvestmentsFailed(false);
      })
      .catch(() => {
        setInvestments([]);
        setInvestmentsFailed(true); // otherwise "shares an existing stake" silently disappears
      });
  }, [contractId]);

  useEffect(() => {
    refreshInvestments();
  }, [refreshInvestments]);

  // Person search (whole DB).
  useEffect(() => {
    window.clearTimeout(debounce.current);
    beginPersons(); // a reply still in flight answers an earlier text: drop it
    const q = personQuery.trim();
    if (pickedPerson || creatingPerson || q.length < 2) {
      setPersonHits([]);
      setPersonAnswer(null);
      return;
    }
    debounce.current = window.setTimeout(() => {
      const fresh = beginPersons();
      searchPersonsRich(q, PERSON_SEARCH_LIMIT)
        .then((response) => {
          if (!fresh()) return;
          setPersonHits(response.results);
          setPersonAnswer({ q, ok: true });
        })
        .catch(() => {
          if (!fresh()) return;
          setPersonHits([]);
          setPersonAnswer({ q, ok: false }); // failed is not "nobody matches"
        });
    }, 220);
    return () => window.clearTimeout(debounce.current);
  }, [personQuery, pickedPerson, creatingPerson, personRetry, beginPersons]);

  // The same-surname wall while creating a new person.
  useEffect(() => {
    beginSurname(); // drop any reply for an earlier surname BEFORE the debounce
    if (!creatingPerson || npLast.trim().length < 2) {
      setSurnameHits([]);
      setSurnameTotal(0);
      setConfirmedNew(false);
      setSurnameCheck("idle");
      return;
    }
    setSurnameCheck("pending");
    const t = window.setTimeout(() => {
      const fresh = beginSurname();
      sameSurname(npLast.trim(), npFirst.trim())
        .then((response) => {
          if (!fresh()) return;
          setSurnameHits(response.results);
          setSurnameTotal(response.total ?? response.results.length);
          setConfirmedNew(false);
          setSurnameCheck("ok");
        })
        .catch(() => {
          if (!fresh()) return;
          setSurnameHits([]);
          setSurnameTotal(0);
          setSurnameCheck("failed");
        });
    }, 300);
    return () => window.clearTimeout(t);
  }, [creatingPerson, npLast, npFirst, surnameRetry, beginSurname]);

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
    if (creatingPerson && npLast.trim().length >= 2 && surnameCheck === "pending")
      return setError("Still checking for existing people with this surname — one moment.");
    if (creatingPerson && npLast.trim().length >= 2 && surnameCheck === "failed")
      return setError("The check for existing people with this surname did not run. Click “Try again” next to it before creating a new person.");
    if (creatingPerson && surnameHits.length > 0 && !confirmedNew)
      return setError(`Confirm that none of the existing people named “${npLast.trim()}” is this person.`);
    if (mode === "join" && !joinId) return setError("Pick the stake this person shares.");
    const phraseWait =
      lookupCheckBlock(titleCheck, "the title") ??
      lookupCheckBlock(residenceCheck, "the residence") ??
      lookupCheckBlock(originCheck, "the place of origin (under “more attributes”)");
    if (phraseWait) return setError(phraseWait);
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
      const how =
        mode !== "join"
          ? `as ${aRole(role)}`
          : takesOver
            ? joined?.type
              ? `as the ${roleLabel(joined.type).toLowerCase()} holding this stake`
              : "as the holder of this stake"
            : "as a joint holder of an existing stake";
      onSaved(`Added ${who}${result.person_created ? " (new person entry)" : ""} ${how}.`);
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
    ["is_jewish", "Jewish — stated in the act"],
    ["is_convert", "convert"],
    ["heirs", "& heirs (ed eredi)"],
    ["heirs_of", "heirs of (eredi di)"],
    ["and_c", "& C. (e compagni)"],
  ];

  // In join mode, a tranche with no members is a stake nobody holds: the person
  // added takes it over (role and sum stay as recorded) rather than sharing it.
  const joined = mode === "join" ? (investments.find((inv) => inv.investment_id === joinId) ?? null) : null;
  const takesOver = Boolean(joined && !joined.members);
  // Deriving the search state from the tagged answer makes the debounce window
  // and the request both count as "searching".
  const personQ = personQuery.trim();
  const personSearch: "idle" | "pending" | "ok" | "failed" =
    personQ.length < 2 ? "idle" : personAnswer?.q !== personQ ? "pending" : personAnswer.ok ? "ok" : "failed";

  return (
    <div className="add-investor-panel">
      <div className="add-investor-head">
        <strong>{takesOver ? "Attach a person to this stake" : "Add a partner"}</strong>
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
            {pickedPerson.identity_hint?.split_flagged ? (
              <div className="pl-identity-split">
                <p>
                  This entry is flagged as possibly two people. Adding another contract to it would make that
                  worse; read the flag first.
                </p>
              </div>
            ) : (
              <IdentityHintLines hint={pickedPerson.identity_hint} />
            )}
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
            {surnameCheck === "pending" && (
              <span className="lookup-status muted">Checking for existing people named “{npLast.trim()}”…</span>
            )}
            {surnameCheck === "failed" && (
              <p className="error-text">
                The check for existing people named “{npLast.trim()}” did not run. A new person can be created once
                the check has run.{" "}
                <button type="button" className="link-like" onClick={() => setSurnameRetry((n) => n + 1)}>
                  Try again
                </button>
              </p>
            )}
            {surnameCheck === "ok" && surnameHits.length === 0 && (
              <span className="lookup-status muted">No existing person has the surname “{npLast.trim()}”.</span>
            )}
            {surnameHits.length > 0 && (
              <div className="create-warning">
                <strong>
                  {surnameTotal} existing {surnameTotal === 1 ? "person" : "people"} named “{npLast.trim()}”
                </strong>{" "}
                — is yours one of these?
                {surnameTotal > surnameHits.length && (
                  <p className="muted">
                    {surnameHits.length} of them are listed:{" "}
                    {npFirst.trim()
                      ? `first those with the first name “${npFirst.trim()}”, then those on the most contracts.`
                      : "those on the most contracts."}{" "}
                    The others can be found from the search, with a first name or patronymic.
                  </p>
                )}
                <ul>
                  {surnameHits.map((hit) => (
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
                      <IdentityHintLines hint={hit.identity_hint} />
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
              placeholder="Search all people by name, patronymic or entry number…"
              autoComplete="off"
            />
            {personSearch === "pending" && <span className="lookup-status muted">Searching…</span>}
            {personSearch === "failed" && (
              <p className="error-text">
                The search for existing people did not run, so it is not known whether this person is already in
                the database. A new person can be created once the search has run.{" "}
                <button
                  type="button"
                  className="link-like"
                  onClick={() => {
                    setPersonAnswer(null);
                    setPersonRetry((n) => n + 1);
                  }}
                >
                  Try again
                </button>
              </p>
            )}
            {personHits.length > 0 && (
              <ul className="lookup-suggestions person-suggestions">
                {personHits.map((hit) => (
                  <li key={hit.person_id}>
                    <button type="button" onMouseDown={(e) => e.preventDefault()} onClick={() => setPickedPerson(hit)}>
                      <span className="lookup-hit-main">
                        <span className="lookup-value">
                          {hit.display_name}
                          {hit.father_mother ? <span className="muted"> di {hit.father_mother}</span> : null}
                        </span>
                        <span className="lookup-used muted">
                          {`Entry #${hit.person_id} · `}
                          {hit.residences ? `${hit.residences} · ` : ""}
                          {hit.appearances} contr.
                        </span>
                      </span>
                      <IdentityHintLines hint={hit.identity_hint} />
                    </button>
                  </li>
                ))}
              </ul>
            )}
            {personSearch === "ok" && personHits.length === 0 && (
              <span className="lookup-status muted">No existing person matches “{personQ}”.</span>
            )}
            {personSearch === "ok" && personHits.length >= PERSON_SEARCH_LIMIT && (
              <span className="lookup-status muted">
                Only the first {PERSON_SEARCH_LIMIT} matches are listed, those on the most contracts first. A first
                name or patronymic narrows the search.
              </span>
            )}
            {personSearch === "ok" && (
              <button type="button" className="field-fix" onClick={() => {
                setCreatingPerson(true);
                const words = personQuery.trim().split(/\s+/);
                setNpFirst(words[0] ?? "");
                setNpLast(words.slice(1).join(" "));
              }}>
                {personHits.length === 0 ? "Create a new person" : "None of these — create a new person"}
              </button>
            )}
          </div>
        )}
      </div>

      {/* ROLE & CAPITAL */}
      <div className="add-investor-step">
        <span className="create-label">Role &amp; capital</span>
        {investmentsFailed && (
          <p className="error-text">
            The existing stakes on this contract could not be loaded, so sharing one is not offered.{" "}
            <button type="button" className="link-like" onClick={refreshInvestments}>
              Try again
            </button>
          </p>
        )}
        {investments.length > 0 && (
          <div className="tranche-mode">
            <label>
              <input type="radio" checked={mode === "own"} onChange={() => setMode("own")} /> own stake
            </label>
            <label>
              <input type="radio" checked={mode === "join"} onChange={() => setMode("join")} />{" "}
              {takesOver ? "takes over a stake with no partner" : "shares an existing stake (joint)"}
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
              >
                <option value="lp">External investor (accomandante): provides capital</option>
                <option value="gp">General partner (accomandatario): runs the firm</option>
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
                {role === "gp"
                  ? "0 when the partner contributes work (industria), as in 64% of general partners' stakes."
                  : "Whole units only, as in the original data entry: leave out the smaller units of the currency."}
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
            <span className="create-label">Which stake does this person share?</span>
            <select value={joinId} onChange={(e) => setJoinId(e.target.value)}>
              <option value="">— pick the stake —</option>
              {investments.map((inv) => (
                <option key={inv.investment_id} value={inv.investment_id}>
                  {roleLabel(inv.type)} · {inv.cash == null ? "unspecified" : inv.cash} ·{" "}
                  {inv.partnership_name || inv.members || "no partner attached"}
                </option>
              ))}
            </select>
            <span className="lookup-status is-new">
              {takesOver
                ? "This stake has no partner yet. The person you add takes it over; role and capital stay as recorded."
                : "Role and capital come from the shared stake; it is marked joint for every partner who holds it."}
            </span>
          </label>
        )}
      </div>

      {/* DETAILS */}
      <div className="add-investor-step">
        <span className="create-label">Details (as stated in the document)</span>
        <div className="create-grid">
          <LookupCombobox
            kind="title"
            label="Title (signor, magnifico…)"
            value={title}
            onChange={setTitle}
            onCheckChange={setTitleCheck}
          />
          <LookupCombobox
            kind="place"
            label="Residence"
            value={residence}
            onChange={setResidence}
            placeholder="e.g. Livorno"
            onCheckChange={setResidenceCheck}
          />
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
            <LookupCombobox
              kind="place"
              label="Place of origin"
              value={origin}
              onChange={setOrigin}
              onCheckChange={setOriginCheck}
            />
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
          {saving ? "Adding…" : "Add partner"}
        </button>
      </div>
      {error && <p className="error-text">{error}</p>}
      <p className="inline-editor-foot muted">
        Saves the person, the role and the capital together, under your initials. The panel stays open, since
        every contract needs at least two partners.
      </p>
    </div>
  );
}
