import { Route, Routes } from "react-router-dom";
import TopNav from "./components/TopNav";
import ComingSoon from "./pages/ComingSoon";
import Corrections from "./pages/Corrections";
import Dashboard from "./pages/Dashboard";
import Database from "./pages/Database";
import Hub from "./pages/Hub";
import Reconcile from "./pages/Reconcile";

export default function App() {
  return (
    <div className="app-root">
      <TopNav />
      <main className="route-area">
        <Routes>
          <Route path="/" element={<Hub />} />
          <Route path="/reconcile" element={<Reconcile />} />
          <Route path="/reconcile/:reviewId" element={<Reconcile />} />
          <Route
            path="/changes"
            element={
              <ComingSoon
                title="Tracked-changes review"
                blurb="Read each act with its editorial history and accept or reject insertions, deletions, and comments — independently of the database."
              />
            }
          />
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
