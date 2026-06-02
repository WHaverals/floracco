import { NavLink } from "react-router-dom";

const LINKS = [
  { to: "/", label: "Hub", end: true },
  { to: "/reconcile", label: "Reconcile", end: false },
  { to: "/changes", label: "Changes", end: false },
  { to: "/database", label: "Database", end: false },
  { to: "/corrections", label: "Corrections", end: false },
  { to: "/dashboard", label: "Dashboard", end: false },
];

export default function TopNav() {
  return (
    <nav className="top-nav" aria-label="Platform sections">
      <NavLink to="/" className="top-nav-brand" end>
        FlorAcco
      </NavLink>
      <ul>
        {LINKS.filter((link) => link.to !== "/").map((link) => (
          <li key={link.to}>
            <NavLink to={link.to} end={link.end} className={({ isActive }) => (isActive ? "is-active" : "")}>
              {link.label}
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  );
}
