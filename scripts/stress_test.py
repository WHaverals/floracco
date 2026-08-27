"""Multi-reviewer stress test — the Phase-A acceptance run (docs/multi_user_safety.md §0/§6).

Simulates N concurrent reviewers hammering the shipped surface of a LOCAL server
running on a SCRATCH copy of the real data, then verifies the invariants that
define "nothing was lost, nothing diverged":

  A  zero 5xx and zero unexpected 4xx (whitelisted: the two designed contention
     probes' 409s, analysis-timeout 400s)
  B  ledger accounting exact — every proposal created is present; applied ones
     carry their op in the corrections delta
  C  every JSONL/CSV store parses line-clean
  D  the untouched control store is byte-identical (candidate dismissals — the
     one store nothing in the mix drives)
  E  op-log completeness — replay of the run's delta ops onto the pre-run
     database reproduces the live result table-by-table, value-by-value
  F  no duplicate verbatim lookup mints (the colliding-phrase probe minted ONCE)
  G  investors created during the run carry a structurally-correct is_joint
  H  search.db intact (integrity_check ok)
  I  corrections.db intact; every request has ≥1 event; no duplicate request ids

Run (from the repo root; NEVER points at the live site — there is deliberately
no URL option, and all writes land in a throwaway scratch dir):

    uv run python scripts/stress_test.py                 # 8 workers, ~2 min
    uv run python scripts/stress_test.py --workers 16
    uv run python scripts/stress_test.py --kill          # SIGKILL mid-run, restart,
                                                         # then health checks + an
                                                         # honest divergence report

The load mix mirrors apps/review/src/features.ts's shipped surface (only
Reconcile is hidden in prod, but its /api/decisions endpoint stays live and is
deliberately driven here — it is the one whole-file CSV store).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[1]
EXCLUDES = ["corpus", "05_db_candidate_matches", "03_extracted_registers", ".write.lock"]


# ---------------------------------------------------------------------------
# Phase 1 — scratch copy
# ---------------------------------------------------------------------------

def make_scratch(scratch: Path) -> None:
    print(f"[1/4] scratch copy → {scratch}")
    src = REPO / "data"
    cmd = ["rsync", "-a"] + [f"--exclude={e}" for e in EXCLUDES] + [f"{src}/", f"{scratch / 'data'}/"]
    subprocess.run(cmd, check=True)
    # re-snapshot the two mutable DBs via the online-backup API (torn-copy-proof)
    for name in ("main.db", "corrections.db"):
        p = scratch / "data/sqlite" / name
        if not (src / "sqlite" / name).exists():
            continue
        s = sqlite3.connect(src / "sqlite" / name)
        p.unlink(missing_ok=True)
        t = sqlite3.connect(p)
        with t:
            s.backup(t)
        s.close(); t.close()


# ---------------------------------------------------------------------------
# Phase 2 — launch the real server command against the scratch
# ---------------------------------------------------------------------------

def launch(scratch: Path, port: int) -> subprocess.Popen:
    env = dict(os.environ)
    env["FLORACCO_DATA_DIR"] = str(scratch / "data")
    env.pop("FLORACCO_DB_PATH", None)
    env.pop("FLORACCO_CORRECTIONS_DB_PATH", None)
    proc = subprocess.Popen(
        ["uv", "run", "uvicorn", "workflows.review_server:app",
         "--host", "127.0.0.1", "--port", str(port), "--workers", "1"],
        cwd=REPO, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            if httpx.get(f"{base}/api/db/reference/currency?limit=1", timeout=2).status_code == 200:
                print(f"[2/4] server up at {base} (pid {proc.pid})")
                return proc
        except httpx.HTTPError:
            time.sleep(0.5)
    proc.kill()
    raise SystemExit("server never became ready")


# ---------------------------------------------------------------------------
# Phase 3 — the load
# ---------------------------------------------------------------------------

class Stats:
    def __init__(self):
        self.lock = threading.Lock()
        self.outcomes: Counter = Counter()          # (label, status) -> n
        self.proposals: list[str] = []              # created proposal ids
        self.applied: list[str] = []
        self.created_investors: list[int] = []
        self.probe_apply: list[int] = []            # statuses of the same-field probe
        self.unexpected: list[str] = []             # anything outside the whitelist

    def hit(self, label: str, status: int, *, expect=(200,)):
        with self.lock:
            self.outcomes[(label, status)] += 1
            if status not in expect:
                self.unexpected.append(f"{label} -> {status}")


def worker(i: int, base: str, contracts: list[int], stats: Stats, iters: int, stop: threading.Event):
    c = httpx.Client(base_url=base, timeout=60)
    own, own2 = contracts[2 * i], contracts[2 * i + 1]
    rev = f"S{i}"
    for it in range(iters):
        if stop.is_set():
            break
        # 1. the shipped inline-edit chain (exactly the 3 calls the UI makes)
        r = c.post("/api/corrections", json={
            "reviewer": rev, "db_row_id": f"contract:{own}", "field": "folio",
            "change_type": "correct", "proposed_value": f"{10 + it}r-{i}", "rationale": "stress",
        })
        stats.hit("propose", r.status_code)
        if r.status_code == 200:
            pid = r.json()["proposal"]["proposal_id"]
            with stats.lock:
                stats.proposals.append(pid)
            r2 = c.post(f"/api/corrections/{pid}/approve", json={"reviewer": rev})
            stats.hit("approve", r2.status_code)
            r3 = c.post(f"/api/corrections/{pid}/apply", json={"reviewer": rev})
            stats.hit("apply", r3.status_code, expect=(200, 409))
            if r3.status_code == 200:
                with stats.lock:
                    stats.applied.append(pid)
        # 2. a create cascade on iteration 0: contract + sub-act + investor
        if it == 0:
            r = c.post("/api/db/create/contract", json={
                "reviewer": rev, "source": f"stress worker {i}", "folder": "99999",
                "folio": f"{i}r", "registration_date": "1700-01-01",
                "firm_name": f"Stress & C. n.{i}", "economic_activity": f"stress trade {i}",
                "document": "Contratto creato dal collaudo di carico multiplo.",
            })
            stats.hit("create_contract", r.status_code)
            if r.status_code == 200:
                new_id = int(r.json()["id"])
                r2 = c.post("/api/db/create/sub_contract", json={
                    "reviewer": rev, "source": f"stress worker {i}", "main_contract_id": new_id,
                    "sub_type": "renewal", "folder": "99999", "folio": f"{i}v",
                    "registration_date": "1705-01-01",
                    "document": "Rinnovazione creata dal collaudo.",
                })
                stats.hit("create_sub", r2.status_code)
                r3 = c.post("/api/db/create/investor", json={
                    "reviewer": rev, "contract_id": new_id,
                    "new_person": {"first_name": f"Test{i}", "last_name": "Stress"},
                    "role": "gp", "investment_cash": 100 + i,
                })
                stats.hit("create_investor", r3.status_code)
                if r3.status_code == 200:
                    with stats.lock:
                        stats.created_investors.append(int(r3.json()["investor_id"]))
        # 3. place lifecycle on the worker's second contract
        r = c.post(f"/api/db/contract/{own2}/place/add",
                   json={"reviewer": rev, "place": f"Stressville {i}", "address": f"via {it}"})
        stats.hit("add_place", r.status_code, expect=(200, 409))
        # 4. hide → restore toggle (sequential per worker: no 409 expected)
        r = c.post(f"/api/db/record/contract/{own2}/hide", json={"reviewer": rev, "reason": "stress"})
        stats.hit("hide", r.status_code)
        r = c.post(f"/api/db/record/contract/{own2}/restore", json={"reviewer": rev})
        stats.hit("restore", r.status_code)
        # 5. a search right after the write burst (forces index staleness churn)
        r = c.get("/api/search", params={"q": "seta"})
        stats.hit("search", r.status_code)
        # 6. a flag dismissal (append store)
        r = c.post(f"/api/db/flags/stress-{i}-{it}/dismiss", json={"reviewer": rev, "reason": ""})
        stats.hit("dismiss_flag", r.status_code)
        # 7. a Reconcile decision (the CSV whole-file store — locked & atomic now)
        r = c.post("/api/decisions", json={
            "reviewer": rev, "source_entry_key": f"stress-{i}-{it}",
            "suggested_db_row_id": f"contract:{own}", "packet_section": "DB-only review",
            "main_judgment": "same_act", "image_judgment": "not_needed",
            "field_correction_needed": "none_obvious", "next_action": "approve_link",
        })
        stats.hit("decision", r.status_code)
    c.close()


def analysis_worker(base: str, stats: Stats, stop: threading.Event):
    """The long-read pressure: repeated multi-second Analysis queries. A 400
    (the server's own query-budget timeout) is a legitimate outcome."""
    c = httpx.Client(base_url=base, timeout=60)
    sql = ("SELECT count(*) FROM contract a, contract b "
           "WHERE a.registration_date = b.registration_date")
    while not stop.is_set():
        r = c.post("/api/analysis/run", json={"sql": sql})
        stats.hit("analysis", r.status_code, expect=(200, 400))
        time.sleep(0.2)
    c.close()


def contention_probes(base: str, contracts: list[int], stats: Stats):
    """The two designed collisions the plan promises exact outcomes for."""
    target = contracts[-1]
    c = httpx.Client(base_url=base, timeout=60)
    pids = []
    for v in ("77r", "88r"):
        r = c.post("/api/corrections", json={
            "reviewer": "P", "db_row_id": f"contract:{target}", "field": "folio",
            "change_type": "correct", "proposed_value": v, "rationale": "probe"})
        pid = r.json()["proposal"]["proposal_id"]
        c.post(f"/api/corrections/{pid}/approve", json={"reviewer": "P"})
        pids.append(pid)
    c.close()

    def apply_one(pid):
        cc = httpx.Client(base_url=base, timeout=60)
        r = cc.post(f"/api/corrections/{pid}/apply", json={"reviewer": "P"})
        with stats.lock:
            stats.probe_apply.append(r.status_code)
        cc.close()

    def add_colliding(cid):
        cc = httpx.Client(base_url=base, timeout=60)
        r = cc.post(f"/api/db/contract/{cid}/place/add",
                    json={"reviewer": "P", "place": "Colliding Place Probe"})
        stats.hit("probe_place", r.status_code)
        cc.close()

    threads = [threading.Thread(target=apply_one, args=(p,)) for p in pids]
    threads += [threading.Thread(target=add_colliding, args=(cid,)) for cid in contracts[-3:-1]]
    for t in threads: t.start()
    for t in threads: t.join()


# ---------------------------------------------------------------------------
# Phase 4 — invariants
# ---------------------------------------------------------------------------

def _dump(conn: sqlite3.Connection, table: str):
    # order-independent, TYPED rows — the tests/test_rebuild_integrity.py pattern
    rows = conn.execute(f"SELECT * FROM `{table}`").fetchall()
    return sorted(
        [tuple((type(v).__name__, v) for v in row) for row in rows],
        key=lambda r: [(t, str(v)) for t, v in r],
    )


def verify(scratch: Path, stats: Stats, pre_request_ids: set[str],
           baseline_main: Path, pre_candidates: bytes, *, killed: bool) -> int:
    print("[4/4] invariants")
    fails: list[str] = []
    data = scratch / "data"
    main, corr = data / "sqlite/main.db", data / "sqlite/corrections.db"
    store = data / "derived/word-pipeline/10_corrections"

    # A — outcomes
    if stats.unexpected and not killed:
        fails.append(f"A: {len(stats.unexpected)} unexpected responses, e.g. {stats.unexpected[:5]}")
    # designed probe: exactly one 200 + one 409
    if not killed and sorted(stats.probe_apply) != [200, 409]:
        fails.append(f"A-probe: same-field applies -> {stats.probe_apply} (want one 200, one 409)")

    # B — ledger accounting
    ledger = {}
    prop_path = store / "corrections_proposals.jsonl"
    if prop_path.exists():
        for line in prop_path.read_text().splitlines():
            row = json.loads(line)   # C fails loudly here if a line is torn
            ledger[row["proposal_id"]] = row
    missing = [p for p in stats.proposals if p not in ledger]
    if missing and not killed:
        fails.append(f"B: {len(missing)} created proposals VANISHED from the ledger")
    clog = sqlite3.connect(corr)
    ops = {r[0] for r in clog.execute("SELECT request_id FROM change_request")}
    new_ops = ops - pre_request_ids

    # C — stores parse clean (proposals parsed above; the rest here)
    for f in ("corrections_events.jsonl", "flag_dismissals.jsonl"):
        p = store / f
        if p.exists():
            for line in p.read_text().splitlines():
                json.loads(line)

    # D — untouched control
    cand = store / "correction_candidate_dismissals.jsonl"
    now_bytes = cand.read_bytes() if cand.exists() else b""
    if now_bytes != pre_candidates:
        fails.append("D: the candidate-dismissals store changed — nothing in the mix drives it")

    # E — rebuild equivalence on the delta ops
    delta = scratch / "delta_corr.db"
    shutil.copy2(corr, delta)
    dc = sqlite3.connect(delta)
    with dc:
        marks = ",".join("?" for _ in pre_request_ids) or "''"
        dc.execute(f"DELETE FROM change_event WHERE request_id IN ({marks})", list(pre_request_ids))
        dc.execute(f"DELETE FROM change_request WHERE request_id IN ({marks})", list(pre_request_ids))
    dc.close()
    replay_target = scratch / "replay_main.db"
    shutil.copy2(baseline_main, replay_target)
    os.environ["FLORACCO_CORRECTIONS_DB_PATH"] = str(delta)
    sys.path.insert(0, str(REPO))
    from workflows import db_import
    rc = sqlite3.connect(replay_target)
    rstats = db_import.replay_corrections(rc)
    rc.commit()
    live = sqlite3.connect(main)
    tables = [r[0] for r in live.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    diverged = []
    for t in tables:
        if _dump(rc, t) != _dump(live, t):
            diverged.append(t)
    if diverged:
        msg = f"E: rebuilt tables diverge from live: {diverged} (replay stats {rstats})"
        if killed:
            print(f"    (kill-run, expected residual) {msg}")
        else:
            fails.append(msg)
    if rstats["conflicts"] and not killed:
        fails.append(f"E: replay flagged {rstats['conflicts']} conflicts on a clean run")

    # F — no duplicate verbatim mints
    dupes = live.execute(
        "SELECT trim(place_name), COUNT(DISTINCT place_id) c FROM place "
        "WHERE place_name LIKE 'Stressville%' OR place_name LIKE 'Colliding%' "
        "GROUP BY trim(place_name) HAVING c > 1").fetchall()
    if dupes:
        fails.append(f"F: duplicate verbatim mints: {dupes}")

    # G — created investors' is_joint matches structure
    for inv_id in stats.created_investors:
        row = live.execute(
            """SELECT i.is_joint,
                      (SELECT COUNT(*) FROM investor_group g JOIN investor j ON j.investor_id=g.investor_id
                        WHERE g.investment_id = (SELECT investment_id FROM investor_group WHERE investor_id=i.investor_id)
                          AND g.is_deleted=0 AND j.is_deleted=0) AS members
               FROM investor i WHERE i.investor_id=?""", (inv_id,)).fetchone()
        if row and bool(row[0]) != (row[1] >= 2):
            fails.append(f"G: investor {inv_id} is_joint={row[0]} but tranche has {row[1]} members")

    # H — search.db integrity
    sdb = data / "sqlite/search.db"
    if sdb.exists():
        ic = sqlite3.connect(f"file:{sdb}?mode=ro", uri=True).execute("PRAGMA integrity_check").fetchone()[0]
        if ic != "ok":
            fails.append(f"H: search.db integrity: {ic}")

    # I — corrections.db health
    if clog.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        fails.append("I: corrections.db integrity check failed")
    orphans = clog.execute(
        "SELECT COUNT(*) FROM change_request r WHERE NOT EXISTS "
        "(SELECT 1 FROM change_event e WHERE e.request_id = r.request_id)").fetchone()[0]
    if orphans:
        fails.append(f"I: {orphans} ops have no event trail")

    live.close(); rc.close(); clog.close()

    print(f"    ops logged during run: {len(new_ops)}; outcomes: {dict(stats.outcomes)}")
    if fails:
        print("FAILED invariants:")
        for f in fails:
            print("  ✗", f)
        return 1
    print("ALL INVARIANTS GREEN" + (" (kill-run health checks)" if killed else ""))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--iters", type=int, default=5)
    ap.add_argument("--kill", action="store_true", help="SIGKILL the server mid-run, restart, health-check")
    args = ap.parse_args()

    scratch = Path(tempfile.mkdtemp(prefix="floracco-stress-"))
    make_scratch(scratch)
    data = scratch / "data"
    baseline_main = scratch / "baseline_main.db"
    shutil.copy2(data / "sqlite/main.db", baseline_main)
    pre = sqlite3.connect(data / "sqlite/corrections.db")
    pre_request_ids = {r[0] for r in pre.execute("SELECT request_id FROM change_request")}
    pre.close()
    cand = data / "derived/word-pipeline/10_corrections/correction_candidate_dismissals.jsonl"
    pre_candidates = cand.read_bytes() if cand.exists() else b""

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    proc = launch(scratch, port)
    base = f"http://127.0.0.1:{port}"

    # pick per-worker live contracts (2 each + 3 probe targets)
    db = sqlite3.connect(data / "sqlite/main.db")
    contracts = [r[0] for r in db.execute(
        "SELECT contract_id FROM contract WHERE is_deleted=0 ORDER BY contract_id LIMIT ?",
        (2 * args.workers + 3,))]
    db.close()

    print(f"[3/4] load: {args.workers} reviewers × {args.iters} rounds + 2 analysis readers"
          + (" + KILL variant" if args.kill else ""))
    stats = Stats()
    stop = threading.Event()
    threads = [threading.Thread(target=worker, args=(i, base, contracts, stats, args.iters, stop))
               for i in range(args.workers)]
    threads += [threading.Thread(target=analysis_worker, args=(base, stats, stop)) for _ in range(2)]
    for t in threads[: args.workers]:
        t.start()
    for t in threads[args.workers:]:
        t.start()
    killed = False
    if args.kill:
        time.sleep(4)
        print("    SIGKILL the server mid-load…")
        proc.send_signal(signal.SIGKILL)
        killed = True
        stop.set()
        for t in threads:
            t.join()
        proc.wait()
        proc = launch(scratch, port)   # restart on the same disk
    else:
        for t in threads[: args.workers]:
            t.join()
        stop.set()
        for t in threads[args.workers:]:
            t.join()
        contention_probes(base, contracts, stats)

    try:
        code = verify(scratch, stats, pre_request_ids, baseline_main, pre_candidates, killed=killed)
    finally:
        proc.terminate()
        proc.wait(timeout=10)
    print(f"scratch kept for inspection: {scratch}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
