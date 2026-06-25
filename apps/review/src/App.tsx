import { useEffect, useState } from "react";
import { Route, Routes } from "react-router-dom";
import { loadMe } from "./api";
import { isToolHidden } from "./features";
import TopNav from "./components/TopNav";
import ComingSoon from "./pages/ComingSoon";
import Database from "./pages/Database";
import Explore from "./pages/Explore";
import Hub from "./pages/Hub";
import Reference from "./pages/Reference";
import Reconcile from "./pages/Reconcile";

const REVIEWER_KEY = "floracco_reviewer";

// A tool hidden from the pilot renders this placeholder on its route, so it's
// unreachable even by direct URL (the nav link is greyed out separately).
const NOT_IN_PILOT = (
  <ComingSoon
    title="Not in this pilot yet"
    blurb="This tool is still in development and isn't part of the current pilot. It'll appear here when it's ready."
  />
);

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
          <Route path="/explore" element={<Explore />} />
          <Route path="/reconcile" element={isToolHidden("reconcile") ? NOT_IN_PILOT : <Reconcile />} />
          <Route
            path="/reconcile/:reviewId"
            element={isToolHidden("reconcile") ? NOT_IN_PILOT : <Reconcile />}
          />
          {/* /changes was deliberately killed (2026-06-11): tracked changes render
              inside the Word-summary drawer wherever a summary is shown. */}
          <Route path="/database" element={<Database />} />
          <Route path="/database/:table" element={<Database />} />
          <Route path="/database/:table/:id" element={<Database />} />
          <Route path="/reference" element={isToolHidden("reference") ? NOT_IN_PILOT : <Reference />} />
          <Route path="*" element={<ComingSoon title="Page not found" blurb="That route does not exist." />} />
        </Routes>
      </main>
    </div>
  );
}
