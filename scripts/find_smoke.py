"""Read-only smoke run of the Analysis "Find" pane on a real corpus copy.

Asks Francesca's request of 27 October 2026 and the design's other worked
examples (docs/analysis/review_2026-10-27.md, sections 11 and 12) through the
same engine the page uses (build_find_sql on the read-only executor), prints
the answers with their timings, and checks that the figures agree with each
other. The numbers move as the corpus is corrected, so the checks are
relations, never fixed values:

  - "either" = "stated" + "attributed" - the contracts with both, and the
    answer's split adds up to the count;
  - "with no partner who" + "with at least one partner who" = all the place's contracts;
  - "the same partner" never finds more than "the same or another";
  - the List holds as many rows as the answer counts;
  - every question answers in under half a second (review 12.5).

Step 2 adds:

  - "is" and "is not" split the corpus (a currency, a register, an open formula),
    and "lives in" + "no partner who lives in" = all the place's contracts;
  - a picker's count in the corpus is what the question finds on its own, for
    the first values of each picker, "not recorded" included;
  - How this was found: the steps never grow, the last is the answer, and each
    near miss counts what its own question finds; other spellings add >= 0;
  - the counts behind "the same partner" and "another" are the two questions';
  - the pickers' counts and How this was found each answer in under a second.

Step 3 adds:

  - Count: single-valued groupings add up to the set; provenance gives the
    answer's split; a two-way table's cells add up to its rows;
  - Over time: the decades and the undated add up to the set, the split bars to
    the bars, and a base is never smaller than the set it is the base of;
  - Compare: a split series counts what the question with that value finds; the
    stated overlap of two places is the question asking for both;
  - each view answers in under a second.

Step 4 adds:

  - two sets combined: "both" is the question asking for both, "either" is the
    two less "both", "but not" is the first less "both";
  - See their partners: those who matched are among every partner, and person
    entries never outnumber appearances;
  - a question saved now, asked again at once, reports no change (nothing is
    written: the saved question is held in memory).

Step 5 (the landing and the starting questions) adds:

  - each starting question that opens as a Find counts what its library query
    counts: the same contracts, or each place and currency with the same count;
  - every number on the landing that opens a question is what that question
    finds (contracts, or the matching partner appearances);
  - the later acts by type add up to the acts; a termination's contracts are
    among those with a later act; the woman figure's roles are among its
    contracts; proxy and joint appearances are among every appearance;
  - the landing and the starting questions each answer in under a second.

Run from the repo root, on a scratch copy (never data/ of the live site):

    FLORACCO_DATA_DIR=/path/to/scratch uv run python scripts/find_smoke.py

Exit status 1 when a check fails. Nothing is written: the database is opened
read-only.
"""

from __future__ import annotations

import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workflows import review_server  # noqa: E402

LIMIT_S = 0.5
failures: list[str] = []


def _open_read_only() -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{review_server.db_path()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def check(ok: bool, what: str) -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {what}")
    if not ok:
        failures.append(what)


def ask(spec: dict) -> dict:
    started = time.monotonic()
    query = review_server.find_prepare(spec)
    counts, undated = review_server._find_counts(query)
    listing = review_server.run_readonly_sql(query["list_sql"], tuple(query["list_params"]))
    seconds = time.monotonic() - started
    answer = review_server.find_answer(query, counts, undated)
    total = int(counts["total"])
    print(f"\n{query['sentence']}\n  {' '.join(answer)}\n  ({seconds:.3f} s)")
    check(seconds < LIMIT_S, f"answered in under {LIMIT_S} s")
    shown = listing["total_rows"] if listing["truncated"] else listing["row_count"]
    check(shown == total, f"the List holds {shown:,} rows for {total:,} counted")
    return {"total": total, "counts": counts, "undated": undated}


def place_id(name: str) -> int:
    connection = _open_read_only()
    try:
        row = connection.execute("SELECT place_id FROM place WHERE place_name = ?", (name,)).fetchone()
    finally:
        connection.close()
    if row is None:
        sys.exit(f"No place named {name!r} in {review_server.db_path()}.")
    return int(row[0])


def partner(quantifier: str, *conditions: tuple[str, str]) -> dict:
    return {"quantifier": quantifier, "conditions": [{"kind": k, "choice": c} for k, c in conditions]}


def main() -> int:
    review_server.open_db = _open_read_only  # find_prepare reads place names and non-person rows
    print(f"Corpus: {review_server.db_path()}")
    livorno = {"kind": "place", "values": [place_id("Livorno")]}

    place_only = ask({"contract": [livorno]})
    either = ask({"contract": [livorno], "partners": [partner("any", ("jewish", "either"))]})
    stated = ask({"contract": [livorno], "partners": [partner("any", ("jewish", "stated"))]})
    attributed = ask({"contract": [livorno], "partners": [partner("any", ("jewish", "attributed"))]})
    both = int(attributed["counts"].get("split_1") or 0)
    check(either["total"] == stated["total"] + attributed["total"] - both,
          f"either {either['total']} = stated {stated['total']} + attributed {attributed['total']} - both {both}")
    split = int(either["counts"].get("split_1") or 0)
    check(split == stated["total"], f"the answer's stated share ({split}) is the stated count ({stated['total']})")
    none = ask({"contract": [livorno], "partners": [partner("no", ("jewish", "either"))]})
    check(none["total"] + either["total"] == place_only["total"],
          f"no partner who ({none['total']}) + at least one ({either['total']}) = Livorno ({place_only['total']})")
    ask({"contract": [livorno, {"kind": "years", "from": 1750, "to": 1799}],
         "partners": [partner("any", ("jewish", "either"))]})
    ask({"contract": [livorno], "partners": [partner("every", ("jewish", "either"))]})

    same = ask({"partners": [partner("any", ("woman", "yes"), ("role", "gp"))]})
    another = ask({"partners": [partner("any", ("woman", "yes")), partner("any", ("role", "gp"))]})
    check(same["total"] <= another["total"], f"the same partner ({same['total']}) <= the same or another ({another['total']})")
    ask({"partners": [partner("any", ("woman", "yes"), ("role", "lp"), ("proxy", "yes"))]})

    step_two(livorno, either["total"], place_only["total"])
    step_three(livorno)
    step_four(livorno)
    step_five()

    print(f"\n{len(failures)} check(s) failed." if failures else "\nAll checks passed.")
    return 1 if failures else 0


def values(kind: str, *chosen, negated: bool = False) -> dict:
    return {"kind": kind, "not": negated, "values": list(chosen)}


def step_two(livorno: dict, request_total: int, livorno_total: int) -> None:
    corpus = ask({})["total"]
    lives = ask({"contract": [livorno], "partners": [{"quantifier": "any", "conditions": [values("residence", *livorno["values"])]}]})
    elsewhere = ask({"contract": [livorno], "partners": [{"quantifier": "no", "conditions": [values("residence", *livorno["values"])]}]})
    check(lives["total"] + elsewhere["total"] == livorno_total,
          f"lives in ({lives['total']}) + no partner who lives in ({elsewhere['total']}) = Livorno ({livorno_total})")
    connection = _open_read_only()
    try:
        currency = review_server.find_values(connection, "currency", "")[0]["value"]
        register = review_server.find_values(connection, "register", "")[0]["value"]
        firsts = {kind: review_server.find_values(connection, kind, "")[:4] + review_server.find_values(connection, kind, "")[-1:]
                  for kind in ("place", "residence", "origin", "activity", "currency", "register")}
    finally:
        connection.close()
    for condition in (values("currency", currency), values("register", register, None),
                      {"kind": "discretion", "choice": "activity"}):
        yes = ask({"contract": [condition]})["total"]
        no = ask({"contract": [{**condition, "not": True}]})["total"]
        check(yes + no == corpus, f"{condition['kind']}: is ({yes}) + is not ({no}) = all ({corpus})")
    ask({"partners": [{"quantifier": "any", "conditions": [values("word", "cavalier")]}]})

    specs, expected = [], []
    for kind, offered in firsts.items():
        for value in offered:
            condition = values(kind, value["value"])
            partner_kind = kind in ("residence", "origin")
            specs.append({"partners": [{"quantifier": "any", "conditions": [condition]}]} if partner_kind else {"contract": [condition]})
            expected.append((kind, value["name"], value["contracts"]))
    started = time.monotonic()
    counted = []
    for i in range(0, len(specs), review_server.FIND_MAX_COUNTS):
        counted += review_server.analysis_find_counts(review_server.FindCounts(specs=specs[i: i + review_server.FIND_MAX_COUNTS]))["counts"]
    seconds = time.monotonic() - started
    print(f"\nPickers: {len(specs)} values counted on their own ({seconds:.3f} s)")
    check(seconds < 4 * LIMIT_S, f"the pickers' counts in under {4 * LIMIT_S} s")
    for (kind, name, contracts), count in zip(expected, counted):
        check(count is not None and count["total"] == contracts, f"{kind} {name}: picker {contracts} = question {count and count['total']}")

    request = {"contract": [livorno], "partners": [partner("any", ("jewish", "either"))]}
    started = time.monotonic()
    how = review_server.find_how(request)
    seconds = time.monotonic() - started
    print(f"\nHow this was found ({seconds:.3f} s)")
    for step in how["steps"]:
        print(f"  {step['reading']}: {step['count']:,}" + (f" (on its own {step['alone']:,})" if "alone" in step else ""))
    for line in how["inference"]:
        print(f"  {line}")
    for near in how["near_misses"]:
        print(f"  near miss: {near['sentence']}: {near['count']:,}")
    for left in how["left_out"]:
        print(f"  left out: {left['name']} ({left['contracts']:,}), adds {left['adds']:,}")
    check(seconds < 2 * LIMIT_S, f"How this was found in under {2 * LIMIT_S} s")
    counts = [step["count"] for step in how["steps"]]
    check(counts == sorted(counts, reverse=True) and counts[-1] == request_total,
          f"the steps never grow and end at the answer ({counts})")
    for near in how["near_misses"]:
        check(ask(near["spec"])["total"] == near["count"], f"the near miss counts what its own question finds ({near['count']})")
    check(all(left["adds"] >= 0 for left in how["left_out"]), "other spellings add none or more")

    woman = {"kind": "woman"}
    gp = {"kind": "role", "choice": "gp"}
    placed = review_server.analysis_find_counts(review_server.FindCounts(specs=[
        {"partners": [{"quantifier": "any", "conditions": [woman, gp]}]},
        {"partners": [{"quantifier": "any", "conditions": [woman]}, {"quantifier": "any", "conditions": [gp]}]},
    ]))["counts"]
    same = ask({"partners": [partner("any", ("woman", "yes"), ("role", "gp"))]})["total"]
    check([c["total"] for c in placed] == [same, ask({"partners": [partner("any", ("woman", "yes")), partner("any", ("role", "gp"))]})["total"]],
          f"the same partner / another, as counted for the menu: {[c['total'] for c in placed]}")


def view(run, what: str):
    started = time.monotonic()
    out = run()
    seconds = time.monotonic() - started
    check(seconds < 2 * LIMIT_S, f"{what} in under {2 * LIMIT_S} s ({seconds:.3f} s)")
    return out


def step_three(livorno: dict) -> None:
    request = {"contract": [livorno], "partners": [partner("any", ("jewish", "either"))]}
    total = ask(request)["total"]
    print("\nViews")
    half = view(lambda: review_server.analysis_find_count(review_server.FindCountRequest(spec=request, by="half")), "Count by half-century")
    check(sum(len(r["ids"]) for r in half["rows"]) == total, f"the half-centuries add up to the set ({total})")
    for row in half["rows"]:
        print(f"  {row['name']}: {len(row['ids'])}")
    split = view(lambda: review_server.analysis_find_count(review_server.FindCountRequest(spec=request, by="provenance", by2="half")),
                 "Count by provenance and half-century")
    sizes = {r["name"]: len(r["ids"]) for r in split["rows"]}
    print(f"  {sizes}")
    stated = ask({"contract": [livorno], "partners": [partner("any", ("jewish", "stated"))]})["total"]
    check(sizes.get("a matching partner stated Jewish in the act") == stated and sum(sizes.values()) == total,
          f"provenance gives the answer's split ({stated} stated)")
    check(all(sum(len(c["ids"]) for c in r["cells"]) == len(r["ids"]) for r in split["rows"]), "the cells add up to their rows")
    timed = view(lambda: review_server.analysis_find_time(review_server.FindTimeRequest(spec=request, period="half")), "Over time")
    (series,) = timed["series"]
    print(f"  {dict(zip(timed['periods'], series['counts']))}, undated {series['undated']}, partial {timed['partial']}")
    check(sum(series["counts"]) + series["undated"] == total, "the periods and the undated add up to the set")
    check(all(a + b == n for a, b, n in zip(series["parts"][0]["counts"], series["parts"][1]["counts"], series["counts"])),
          "the split bars add up to the bars")
    check(all(all(b >= n for b, n in zip(base["counts"], series["counts"])) for base in series["bases"]),
          "no base is smaller than the set it is the base of")
    firenze = {"kind": "place", "values": [place_id("Firenze")]}
    beside = view(lambda: review_server.analysis_find_time(review_server.FindTimeRequest(
        spec={"contract": [livorno]}, period="half", beside=[{"contract": [firenze]}])), "Compare: Livorno beside Firenze")
    both = ask({"contract": [livorno, firenze]})["total"]
    print(f"  {beside['notes']}")
    check(beside["notes"][:1] == [f"{both:,} contracts are in more than one of these and drawn in each."],
          f"the overlap stated is the question asking for both ({both})")
    by_place = view(lambda: review_server.analysis_find_time(review_server.FindTimeRequest(
        spec={"contract": [{"kind": "years", "from": 1600, "to": 1699}]}, split="place")), "Compare: split by place")
    first = by_place["series"][0]
    named = place_id(first["label"])
    alone = ask({"contract": [{"kind": "years", "from": 1600, "to": 1699}, {"kind": "place", "values": [named]}]})["total"]
    check(first["total"] == alone, f"the series {first['label']} ({first['total']}) is the question with it ({alone})")


def step_four(livorno: dict) -> None:
    print("\nSets, partners and a saved question")
    years = {"contract": [{"kind": "years", "from": 1750, "to": 1799}]}
    first = {"contract": [livorno]}
    a, b = ask(first)["total"], ask(years)["total"]
    both = ask({"contract": [livorno, years["contract"][0]]})["total"]
    combined = {op: view(lambda op=op: review_server.analysis_find(review_server.FindRequest(
        spec={"combine": op, "sets": [first, years]})), f"two sets combined ({op})")["total"] for op in ("and", "or", "not")}
    print(f"  {combined}")
    check(combined["and"] == both, f"both ({combined['and']}) is the question asking for both ({both})")
    check(combined["or"] == a + b - both, f"either ({combined['or']}) = {a} + {b} - {both}")
    check(combined["not"] == a - both, f"but not ({combined['not']}) = {a} - {both}")
    request = {"contract": [livorno], "partners": [partner("any", ("jewish", "either"))]}
    out = view(lambda: review_server.analysis_find_partners(review_server.FindPartnersRequest(spec=request, which="matched")),
               "See their partners")
    every, matched = out["counts"]["every"], out["counts"]["matched"]
    print(f"  every {every['appearances']} ({every['people']} person entries), matched {matched['appearances']} ({matched['people']})")
    check(matched["appearances"] <= every["appearances"] and every["people"] <= every["appearances"],
          "the matching partners are among every partner; person entries never outnumber appearances")
    query = review_server.find_prepare(request)
    ids = review_server._find_members(query)
    now = datetime.now(timezone.utc).isoformat()
    today = view(lambda: review_server.find_question_today(
        {"spec": query["spec"], "sentence": query["sentence"], "count": len(ids), "ids": ids, "at": now}),
        "a saved question asked again")
    print(f"  {' '.join(today['lines'])}")
    check(not today["changed"] and today["today"] == len(ids), "asked again at once, it reports no change")


def _number(text: str) -> int:
    return int("".join(ch for ch in text.rsplit(" ", 1)[-1] if ch.isdigit()) or 0)


def step_five() -> None:
    print("\nThe landing and the starting questions")
    connection = sqlite3.connect(f"file:{review_server.db_path()}?mode=ro", uri=True)
    try:
        for group in review_server.FIND_STARTING:
            for item in group["items"]:
                if "same" not in item:
                    continue
                library, found = review_server.find_starting_check(connection, item)
                size = len(library) if item["same"] == "contracts" else sum(n for _name, n in library)
                check(library == found, f"{item['library']}: the Find counts what the library query counts ({size:,})")
    finally:
        connection.close()
    landing = view(review_server.analysis_find_landing, "the landing")
    starting = view(review_server.analysis_find_starting, "the starting questions")
    print(f"  {' '.join(landing['answer'])}")
    figures = {f["key"]: f for f in [landing["chart"], *landing["figures"]]}
    for figure in figures.values():
        words = "".join(s["text"] for s in figure["value"]) + " | " + "".join(s["text"] for s in figure["detail"])
        print(f"  {figure['label']}: {words} ({figure['as_of']})")
    for figure in figures.values():
        for segment in figure["value"] + figure["detail"]:
            if "spec" not in segment or segment["view"].get("view") == "time":
                continue
            query = review_server.find_prepare(segment["spec"])
            if segment["view"].get("view") == "partners":
                built = review_server.find_partners_sql(query, "matched")
                found = review_server.run_readonly_sql(built["counts_sql"], tuple(built["params"]))["rows"][0][0]
            else:
                found = len(review_server._find_members(query))
            check(found == _number(segment["text"]), f"{figure['key']} “{segment['text']}” opens a question finding {found:,}")
    acts = review_server.find_prepare({"contract": [{"kind": "act", "choice": "any"}]})
    ended = review_server.find_prepare({"contract": [{"kind": "act", "choice": "termination"}]})
    check(set(review_server._find_members(ended)) <= set(review_server._find_members(acts)),
          "a termination's contracts are among those with a later act")
    woman = figures["woman"]["detail"]
    roles = [_number(s["text"]) for s in woman if "spec" in s]
    check(all(n <= _number(figures["woman"]["value"][0]["text"]) for n in roles), f"the woman figure's roles {roles} are among its contracts")
    every = review_server.find_partners_sql(review_server.find_prepare({}), "every")
    appearances = review_server.run_readonly_sql(every["counts_sql"], tuple(every["params"]))["rows"][0][0]
    for key in ("proxy", "joint"):
        check(_number(figures[key]["value"][0]["text"]) <= appearances, f"{key} appearances are among every appearance ({appearances:,})")
    counted = [i for g in starting["groups"] for i in g["items"] if i["kind"] == "find"]
    print(f"  {len(counted)} starting questions open as Find, "
          f"{sum(1 for g in starting['groups'] for i in g['items'] if i['kind'] == 'library')} stay library queries, "
          f"{sum(1 for g in starting['groups'] for i in g['items'] if i['kind'] == 'open')} wait for a decision")


if __name__ == "__main__":
    raise SystemExit(main())
