import { useEffect, useState } from "react";
import { Route, Routes } from "react-router-dom";
import { loadMe } from "./api";
import TopNav from "./components/TopNav";
import ComingSoon from "./pages/ComingSoon";
import Corrections from "./pages/Corrections";
import Dashboard from "./pages/Dashboard";
import Database from "./pages/Database";
import Hub from "./pages/Hub";
import Reconcile from "./pages/Reconcile";

const REVIEWER_KEY = "floracco_reviewer";

export default function App() {
  // Resolve the signed-in identity (Cloudflare Access) BEFORE rendering the
  // routes, so the verified email is seeded into the reviewer field that the
  // decision/correction/create components read from localStorage on mount.
  // Locally (no Access) this returns unauthenticated instantly and we leave any
  // manually-typed initials untouched.
  const [identity, setIdentity] = useState<{ authenticated: boolean; email: string } | null>(null);

  useEffect(() => {
    loadMe()
      .then((me) => {
        if (me.authenticated && me.email) {
          localStorage.setItem(REVIEWER_KEY, me.email);
        }
        setIdentity(me);
      })
      .catch(() => setIdentity({ authenticated: false, email: "" }));
  }, []);

  if (identity === null) {
    return (
      <div className="app-root">
        <p className="muted" style={{ padding: 24 }}>Loading…</p>
      </div>
    );
  }

  return (
    <div className="app-root">
      <TopNav identityEmail={identity.authenticated ? identity.email : null} />
      <main className="route-area">
        <Routes>
          <Route path="/" element={<Hub />} />
          <Route path="/reconcile" element={<Reconcile />} />
          <Route path="/reconcile/:reviewId" element={<Reconcile />} />
          {/* /changes was deliberately killed (2026-06-11): tracked changes render
              inside the Word-summary drawer wherever a summary is shown. */}
          <Route path="/database" element={<Database />} />
          <Route path="/database/:table" element={<Database />} />
          <Route path="/database/:table/:id" element={<Database />} />
          <Route path="/corrections" element={<Corrections />} />
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="*" element={<ComingSoon title="Page not found" blurb="That route does not exist." />} />
        </Routes>
      </main>
    </div>
  );
}
