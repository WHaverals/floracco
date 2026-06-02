import { useEffect, useMemo, useState } from "react";
import { loadContractPersons, searchPersons } from "../api";
import type { ContractPerson, DbSearchResult } from "../types";

export type PersonPick = { row_id: string; display_name: string; last_name: string };

/**
 * Resolve a name mention to an existing person. Names recur heavily in this corpus,
 * so a free-text field is unsafe — the reviewer must pick a specific person_id. We
 * default to the people already on the contract (the common, low-ambiguity case) and
 * offer an explicit cross-database search as an escape hatch. Creating a brand-new
 * person needs entity resolution we deliberately don't attempt here.
 */
export default function PersonPicker({
  contractId,
  onPick,
  onClose,
}: {
  contractId: string;
  onPick: (person: PersonPick) => void;
  onClose: () => void;
}) {
  const [persons, setPersons] = useState<ContractPerson[]>([]);
  const [contractTitle, setContractTitle] = useState("");
  const [filter, setFilter] = useState("");
  const [error, setError] = useState("");
  const [searchResults, setSearchResults] = useState<DbSearchResult[] | null>(null);
  const [searching, setSearching] = useState(false);

  useEffect(() => {
    loadContractPersons(contractId)
      .then((res) => {
        setPersons(res.persons);
        setContractTitle(res.contract_title);
      })
      .catch((err: Error) => setError(err.message));
  }, [contractId]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const filtered = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return persons;
    return persons.filter((p) => p.display_name.toLowerCase().includes(q));
  }, [persons, filter]);

  const runCrossSearch = () => {
    const q = filter.trim();
    if (q.length < 2) return;
    setSearching(true);
    searchPersons(q)
      .then((res) => setSearchResults(res.results))
      .catch((err: Error) => setError(err.message))
      .finally(() => setSearching(false));
  };

  return (
    <div className="drawer-scrim" onClick={onClose}>
      <aside className="propose-drawer" onClick={(event) => event.stopPropagation()}>
        <header className="word-drawer-head">
          <div>
            <p className="eyebrow">Resolve a name → person</p>
            <h3>Which person is this?</h3>
            <p className="muted word-drawer-meta">On {contractTitle || `contract ${contractId}`}</p>
          </div>
          <button type="button" className="drawer-close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </header>

        <div className="propose-body">
          {error && <p className="error-text">{error}</p>}
          <p className="muted">
            The same name can belong to many different people across the corpus — pick the exact person, don't
            free-type. These are the investors recorded on this contract.
          </p>
          <input
            className="db-search"
            placeholder="Filter by name…"
            value={filter}
            onChange={(event) => {
              setFilter(event.target.value);
              setSearchResults(null);
            }}
          />
          <ul className="picker-list">
            {filtered.map((p) => (
              <li key={p.person_id}>
                <button
                  type="button"
                  className="picker-person"
                  onClick={() => onPick({ row_id: p.row_id, display_name: p.display_name, last_name: p.last_name })}
                >
                  <span className="picker-name">{p.display_name}</span>
                  <span className="picker-detail">
                    {p.detail ? `${p.detail} · ` : ""}#{p.person_id} · on {p.appears_on_contracts} contract
                    {p.appears_on_contracts === 1 ? "" : "s"}
                  </span>
                </button>
              </li>
            ))}
            {filtered.length === 0 && <li className="db-empty muted">No investor on this contract matches.</li>}
          </ul>

          <div className="picker-cross">
            <button
              type="button"
              className="pill-button"
              onClick={runCrossSearch}
              disabled={filter.trim().length < 2 || searching}
            >
              {searching ? "Searching…" : "Search the whole database"}
            </button>
            {searchResults && (
              <ul className="picker-list">
                {searchResults.map((r) => (
                  <li key={r.row_id}>
                    <button
                      type="button"
                      className="picker-person"
                      onClick={() => onPick({ row_id: r.row_id, display_name: r.title, last_name: "" })}
                    >
                      <span className="picker-name">{r.title}</span>
                      <span className="picker-detail">{r.meta}</span>
                    </button>
                  </li>
                ))}
                {searchResults.length === 0 && (
                  <li className="db-empty muted">No people match across the database.</li>
                )}
              </ul>
            )}
          </div>

          <p className="muted propose-foot">
            Not listed, or a genuinely new person? That needs entity resolution we don't do yet — record it in the
            correction rationale and flag for review rather than guessing.
          </p>
        </div>
      </aside>
    </div>
  );
}
