import { useEffect, useRef, useState } from "react";
import { NavLink, useLocation, useNavigate } from "react-router-dom";
import { isToolHidden } from "../features";

const LINKS = [
  { to: "/reconcile", label: "Reconcile", key: "reconcile" },
  { to: "/database", label: "Database", key: "database" },
  { to: "/reference", label: "Reference", key: "reference" },
];

/* Global search lives on the home page; every other page carries this compact
 * box that routes there (`/?q=…`). Pressing `/` anywhere (outside an input)
 * jumps to it. The killed `/changes` tool is gone from the nav: tracked
 * changes render inside the Word-summary drawer instead.
 */
export default function TopNav({ identityEmail }: { identityEmail?: string | null }) {
  const navigate = useNavigate();
  const location = useLocation();
  const [query, setQuery] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const onHome = location.pathname === "/";

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "/" || event.metaKey || event.ctrlKey || event.altKey) return;
      const target = event.target as HTMLElement | null;
      if (target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return;
      event.preventDefault();
      if (onHome) {
        (document.querySelector(".hub-search input") as HTMLInputElement | null)?.focus();
      } else {
        inputRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onHome]);

  return (
    <nav className="top-nav" aria-label="Platform sections">
      <NavLink to="/" className="top-nav-brand" end>
        FlorAcco
      </NavLink>
      {!onHome && (
        <form
          className="top-nav-search"
          onSubmit={(event) => {
            event.preventDefault();
            if (query.trim()) {
              navigate(`/explore?q=${encodeURIComponent(query.trim())}`);
              setQuery("");
            }
          }}
        >
          <input
            ref={inputRef}
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search the corpus…  ( / )"
            aria-label="Search the corpus"
          />
        </form>
      )}
      <ul>
        {LINKS.map((link) =>
          isToolHidden(link.key) ? (
            <li key={link.to}>
              <span className="top-nav-soon" title="In development — not in this pilot yet">
                {link.label}
                <em>soon</em>
              </span>
            </li>
          ) : (
            <li key={link.to}>
              <NavLink to={link.to} className={({ isActive }) => (isActive ? "is-active" : "")}>
                {link.label}
              </NavLink>
            </li>
          ),
        )}
      </ul>
      {identityEmail ? (
        <span
          className="top-nav-identity"
          title="Signed in via Cloudflare Access — your edits are attributed to this account"
        >
          {identityEmail}
        </span>
      ) : null}
    </nav>
  );
}
