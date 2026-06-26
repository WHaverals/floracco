"""Deterministic duplicate matchers for the controlled vocabularies.

Each vocabulary (currency, place, title, economic_activity) is structurally
different, so each gets its own matcher — built one at a time. A matcher never
*decides* anything: it reduces every term to a normalized **signature** and
surfaces terms that share a signature as **candidates for a human to review**.
The verbatim term is never altered. Two guarantees, by construction:

* it never proposes a same-as across a **different number** (an exchange rate is
  a historical fact — ``lire 7`` and ``lire 4`` must stay separate), a different
  head **coin**, or a different **place/moneta** qualifier;
* what it folds is purely orthographic — apostrophe style (``d'oro`` / ``di
  oro``), connector words (``di`` / ``a`` / ``e``), spelling variants
  (``pauli`` / ``paoli``, ``maravilis`` / ``maravedis``), and word order.

Currency is the first matcher (the safest: its grammar is mechanical).
Place/title/activity matchers will be added here later.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from typing import Any

# --- Currency ---------------------------------------------------------------
# Head-coin spellings -> a canonical coin token.
_COIN = {
    "scudi": "scudo", "scudo": "scudo", "ducati": "ducato", "ducato": "ducato",
    "fiorini": "fiorino", "fiorino": "fiorino", "lire": "lira", "lira": "lira",
    "pezze": "pezza", "pezza": "pezza", "onze": "onza", "onza": "onza",
    "oncie": "onza", "once": "onza", "piastre": "piastra", "piastra": "piastra",
    "carlini": "carlino", "carlino": "carlino", "reali": "reale", "reale": "reale",
    "zecchini": "zecchino", "ruspi": "ruspo", "franchi": "franco",
    "cruzados": "cruzado", "crusadi": "cruzado", "cruciati": "cruzado", "cruzado": "cruzado",
    "sterline": "sterlina", "sterlina": "sterlina", "starlini": "sterlina", "sterlini": "sterlina",
}
# Sub-denomination unit words (carry an exchange number) -> canonical spelling.
_UNIT = {
    "lire": "lira", "lira": "lira", "soldi": "soldo", "soldo": "soldo", "denari": "denaro",
    "carlini": "carlino", "carlino": "carlino", "tari": "taro", "taro": "taro",
    "giuli": "giulo", "giulio": "giulo", "pauli": "paolo", "paoli": "paolo", "paolo": "paolo",
    "apuli": "paolo", "bolognini": "bolognino", "grossi": "grosso", "grosso": "grosso",
    "maravedis": "maravedi", "maravilis": "maravedi", "maravidis": "maravedi", "maravedi": "maravedi",
    "reis": "reis", "reali": "reale", "reale": "reale", "marchi": "marco", "marco": "marco",
    "tornesi": "tornese", "para": "para", "sterline": "sterlina", "sterlini": "sterlina",
    "fiorini": "fiorino",
}
# Geographic adjectives -> a canonical adjective (spelling only — we never assert
# "fiorentina" == "di Firenze"; that would be a human's interpretive call).
_PLACE_ADJ = {
    "fiorentina": "fiorentina", "fiorentine": "fiorentina", "fiorentini": "fiorentina",
    "romana": "romana", "romano": "romana", "anconetana": "anconetana",
    "veneziani": "veneziana", "veneziana": "veneziana", "piemontesi": "piemontese",
    "spagnuola": "spagnola", "spagnola": "spagnola",
}
_QUALITY = {
    "oro", "larghi", "largo", "larghe", "suggello", "suggelo", "sole", "soli", "correnti",
    "corrente", "camera", "stampa", "stampe", "vecchia", "vecchio", "antico", "antica",
    "banco", "argento", "platta", "moneta", "nuova", "nuovo", "lunga", "lungo",
}
_STOP = {
    "di", "d", "de", "del", "della", "dello", "dei", "delle", "degli", "a", "e", "et", "per",
    "l", "lo", "la", "il", "in", "ragione", "ciascuno", "ciascun", "luno", "luna", "uno",
    "una", "da", "con", "valuta",
}
_FRAC = {"½": "1/2", "¼": "1/4", "¾": "3/4", "⅓": "1/3", "⅔": "2/3"}
_NUM = re.compile(r"\d+(?:/\d+)?")


def _strip_accents(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def _currency_normalize(s: str) -> str:
    s = _strip_accents(s.lower())
    for k, v in _FRAC.items():
        s = s.replace(k, v)
    s = s.replace("’", "'").replace("`", "'").replace("´", "'")
    s = re.sub(r"\bd'", " ", s)          # elided d'oro -> oro
    s = s.replace("'", " ")
    # compound lira:soldi notations (7:10, 7.10) -> "lira 7 soldo 10"
    s = re.sub(r"\blire?\s*(\d+)\s*[:.]\s*(\d+)", r" lira \1 soldo \2 ", s)
    s = re.sub(r"[^a-z0-9/ ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _quality_stem(t: str) -> str:
    t = re.sub(r"(sol)[ei]$", r"\1", t)
    t = re.sub(r"larg.*", "largo", t)
    t = re.sub(r"corrent.*", "corrente", t)
    t = re.sub(r"stamp.*", "stampa", t)
    t = re.sub(r"vecchi.*", "vecchio", t)
    t = re.sub(r"antic.*", "antico", t)
    t = re.sub(r"suggel+o", "suggello", t)
    return t


def currency_signature(raw: str) -> tuple | None:
    """Reduce a currency phrase to (coin, numbers, place/qualifiers, quality).

    Numbers are kept as a sorted multiset of (unit, value) so any rate difference
    keeps two terms apart. Bare sub-unit words (``di grossi``, ``di carlini``
    with no number) are preserved as qualifiers — they distinguish real
    currencies and must never be dropped.
    """
    toks = _currency_normalize(raw).split()
    if not toks:
        return None
    coin = _COIN.get(toks[0], toks[0])
    body = toks[1:]
    used = [False] * len(body)
    numbers: list[tuple[str, str]] = []
    for j, t in enumerate(body):
        if _NUM.fullmatch(t):
            unit = None
            uj = None
            if j > 0 and body[j - 1] in _UNIT and not used[j - 1]:
                unit, uj = _UNIT[body[j - 1]], j - 1
            elif j + 1 < len(body) and body[j + 1] in _UNIT:
                unit, uj = _UNIT[body[j + 1]], j + 1
            numbers.append((unit or "?", t))
            used[j] = True
            if uj is not None:
                used[uj] = True
    places: set[str] = set()
    quals: set[str] = set()
    for j, t in enumerate(body):
        if used[j]:
            continue
        if t in _PLACE_ADJ:
            places.add(_PLACE_ADJ[t])
        elif t in _QUALITY:
            quals.add(_quality_stem(t))
        elif t in _STOP:
            continue
        elif t in _UNIT:
            places.add("§" + _UNIT[t])     # bare sub-unit = qualifier
        elif t in _COIN:
            places.add("per:" + _COIN[t])        # denominator coin
        else:
            places.add(t)                         # place name, etc.
    return (coin, tuple(sorted(numbers)), tuple(sorted(places)), tuple(sorted(quals)))


def _currency_skeleton(sig: tuple) -> tuple:
    """Same as a signature but with number *values* blanked — groups terms that
    are identical except for their exchange figures (the 'looks alike but is a
    different rate' caution)."""
    coin, numbers, places, quals = sig
    units = tuple(sorted(u for u, _ in numbers))
    return (coin, units, places, quals)


def find_currency_duplicates(terms: list[dict[str, Any]]) -> dict[str, Any]:
    """Group ``[{id, value, count}]`` into same-as families and caution pairs.

    Returns ``{"families": [...], "cautions": [...]}``:
    * **families** — terms sharing a signature (orthographic variants of the
      same money). Each: ``{signature, terms:[{id,value,count}]}``.
    * **cautions** — same coin/place but a *different number* (look-alikes that
      must NOT be merged). Each: ``{terms:[...]}``, flagged for the UI.
    """
    by_sig: dict[tuple, list[dict]] = defaultdict(list)
    by_skel: dict[tuple, set[tuple]] = defaultdict(set)
    for t in terms:
        sig = currency_signature(t["value"])
        if sig is None:
            continue
        by_sig[sig].append(t)
        by_skel[_currency_skeleton(sig)].add(sig)

    families = [
        {"signature": _sig_key(sig), "terms": sorted(grp, key=lambda x: -x["count"])}
        for sig, grp in by_sig.items()
        if len(grp) > 1
    ]
    families.sort(key=lambda f: -sum(t["count"] for t in f["terms"]))

    cautions = []
    for skel, sigs in by_skel.items():
        # >1 distinct signature under one skeleton, where the difference is the
        # numbers (not just a same-as family already captured above).
        rate_variants = [s for s in sigs if s[1]]   # has at least one number
        if len(rate_variants) > 1:
            reps = []
            for s in rate_variants:
                grp = by_sig[s]
                reps.append(max(grp, key=lambda x: x["count"]))
            cautions.append({"terms": sorted(reps, key=lambda x: -x["count"])})
    cautions.sort(key=lambda c: -sum(t["count"] for t in c["terms"]))
    return {"families": families, "cautions": cautions}


def _sig_key(sig: tuple) -> str:
    """A short human-readable signature label (for debugging / UI tooltip)."""
    coin, numbers, places, quals = sig
    parts = [coin]
    if numbers:
        parts.append(" ".join(f"{u}={v}" for u, v in numbers))
    if places:
        parts.append(" ".join(places))
    if quals:
        parts.append(" ".join(quals))
    return " · ".join(parts)


# --- Economic activity ------------------------------------------------------
# Activities are compositional free text (frame + trade + commodity + conjoined
# lists), not a rigid grammar. The matcher folds ONLY orthography and word order;
# it never folds singular/plural (the Italian agent-suffix -aio/-aiolo that a
# stemmer would strip is exactly what distinguishes a *trade* from its
# *commodity*: saponaio≠saponi, fornaio≠forno) and never a different content
# word (lanaiolo≠linaiolo). Two terms match only when their normalized token
# *multisets* are identical — same words, any order, spelling folded.
_ACT_STOP = {
    "di", "d", "de", "del", "della", "dello", "dei", "delle", "degli", "e", "et",
    "ed", "al", "la", "il", "lo", "con", "in",
}


def activity_signature(raw: str) -> tuple:
    """Sorted multiset of orthographically-normalized content tokens.

    Folds: ``x→s`` (exercitio→esercitio), ``qu→cu`` (quoiaio→cuoiaio),
    ``ti→zi`` before a vowel (negotio→negozio, spetieria→spezieria), doubled
    letters (negozzio→negozio), apostrophe/``d'``, connector words and word
    order. Preserves every trade/commodity/frame word verbatim (only spelled
    consistently), so distinct activities never collapse.
    """
    s = _strip_accents(raw.lower())
    s = s.replace("’", "'").replace("`", "'").replace("´", "'")
    s = re.sub(r"\bd'", " ", s)
    s = s.replace("'", " ")
    s = re.sub(r"[^a-z ]", " ", s)
    s = s.replace("x", "s")
    s = s.replace("qu", "cu")
    s = re.sub(r"ti(?=[aeou])", "zi", s)
    s = re.sub(r"(.)\1", r"\1", s)
    toks = [t for t in s.split() if t and t not in _ACT_STOP]
    return tuple(sorted(toks))


def find_activity_duplicates(terms: list[dict[str, Any]]) -> dict[str, Any]:
    """Group ``[{id, value, count}]`` into same-as families (orthographic +
    reordered). No plural folding, no false-friend lane — every match is a
    suggestion a human confirms. Returns ``{"families": [...], "cautions": []}``."""
    by_sig: dict[tuple, list[dict]] = defaultdict(list)
    for t in terms:
        sig = activity_signature(t["value"])
        if sig:
            by_sig[sig].append(t)
    families = [
        {"signature": " ".join(sig), "terms": sorted(grp, key=lambda x: -x["count"])}
        for sig, grp in by_sig.items()
        if len({t["id"] for t in grp}) > 1
    ]
    families.sort(key=lambda f: -sum(t["count"] for t in f["terms"]))
    return {"families": families, "cautions": []}


# --- Title ------------------------------------------------------------------
# Titles are honorific stacks: intensifier(s) (illustrissimo, clarissimo…) +
# base (signor / signora / messer / ser) + office/rank (senatore, marchese,
# cavaliere…) + status (quondam, don). The matcher folds ONLY orthography and
# stack order: **apocope** (the dropped final -e: signore→signor, senatore→
# senator, barone→baron) and the clarissimo/chiarissimo spelling. It must never
# fold away gender/number (signor≠signora≠signori), intensity (illustre≠
# illustrissimo), or status (signor≠quondam signor) — those are real
# distinctions, preserved because every honorific word is kept verbatim.
_TITLE_STOP = {"e", "et", "ed", "il", "lo", "la", "i", "di", "de", "del", "l"}


def title_signature(raw: str) -> tuple:
    """Sorted multiset of normalized honorific tokens.

    Folds: ``chiar→clar`` (chiarissimo→clarissimo), ``huomo→uomo``, connector/
    article stopwords, stack order, and **apocope** (a trailing ``-e`` on tokens
    longer than three letters: signore→signor, senatore→senator). Gender forms
    (``-a``/``-i`` endings) and the superlative ``-issimo`` are left intact, so
    signor/signora/signori and illustre/illustrissimo never merge.
    """
    s = _strip_accents(raw.lower())
    s = re.sub(r"[^a-z ]", " ", s)
    s = s.replace("chiar", "clar")
    s = re.sub(r"\bhuomo\b", "uomo", s)
    toks = []
    for t in s.split():
        if not t or t in _TITLE_STOP:
            continue
        if len(t) > 3 and t.endswith("e"):
            t = t[:-1]
        toks.append(t)
    return tuple(sorted(toks))


def find_title_duplicates(terms: list[dict[str, Any]]) -> dict[str, Any]:
    """Group ``[{id, value, count}]`` into same-as families (orthographic +
    reordered). No gender/number/intensity folding; every match is a suggestion
    a human confirms. Returns ``{"families": [...], "cautions": []}``."""
    by_sig: dict[tuple, list[dict]] = defaultdict(list)
    for t in terms:
        sig = title_signature(t["value"])
        if sig:
            by_sig[sig].append(t)
    families = [
        {"signature": " ".join(sig), "terms": sorted(grp, key=lambda x: -x["count"])}
        for sig, grp in by_sig.items()
        if len({t["id"] for t in grp}) > 1
    ]
    families.sort(key=lambda f: -sum(t["count"] for t in f["terms"]))
    return {"families": families, "cautions": []}


# --- Place (P0: deterministic exact only) -----------------------------------
# Places are a *knowledge* problem, not a string problem: among the used places,
# edit-distance ≤2 yields ~79 pairs that are almost all DIFFERENT towns
# (Lucca/Lecce, Roma/Rodi/Como, Cagli/Carpi/Calci). And the real duplicates are
# archaic/exonym drift that no spelling rule captures (Reame=Regno,
# Montelione=Monteleone, Bisanzone=Besançon). So the deterministic matcher does
# ONLY accent/case/punctuation-normalized *exact* matching — it never folds a
# descriptor (contado di Firenze ≠ Firenze) or a near-spelling. The meaningful
# candidates come from the reviewed historic-name pass (P1), which writes cached,
# attributed LLM suggestions into this same worklist (flagged source="llm").
_PLACE_NOTE = (
    "Place duplicates are archaic/exonym spelling drift (Reame=Regno, "
    "Montelione=Monteleone) that spelling rules can’t find, and look-alikes here "
    "are usually different towns (Lucca/Lecce). Below are only exact normalized "
    "matches; the reviewed historic-name pass adds knowledge-based suggestions "
    "(clearly marked as machine-proposed) for a human to confirm."
)


def place_signature(raw: str) -> str:
    """Accent/case/punctuation-normalized exact key. Preserves every word and the
    descriptor (no toponym stripping), so only trivially-identical strings match."""
    s = _strip_accents(raw.lower())
    s = s.replace("’", "'").replace("`", "'").replace("´", "'")
    s = re.sub(r"\s*,\s*", ", ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip(" .,")


def find_place_duplicates(terms: list[dict[str, Any]]) -> dict[str, Any]:
    """P0: exact-normalized families only (typically ~0 — places have no clean
    spelling dupes). Returns ``{"families", "cautions", "note"}``; families carry
    ``source="rule"`` so P1's ``source="llm"`` suggestions slot in alongside."""
    by_sig: dict[str, list[dict]] = defaultdict(list)
    for t in terms:
        sig = place_signature(t["value"])
        if sig:
            by_sig[sig].append(t)
    families = [
        {"signature": sig, "source": "rule", "terms": sorted(grp, key=lambda x: -x["count"])}
        for sig, grp in by_sig.items()
        if len({t["id"] for t in grp}) > 1
    ]
    families.sort(key=lambda f: -sum(t["count"] for t in f["terms"]))
    return {"families": families, "cautions": [], "note": _PLACE_NOTE}


# Registry so the server can dispatch by vocabulary kind.
MATCHERS = {
    "currency": find_currency_duplicates,
    "activity": find_activity_duplicates,
    "title": find_title_duplicates,
    "place": find_place_duplicates,
}
