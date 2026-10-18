"""Forgiving item search used everywhere in the app.

Rules (what store staff expect):
- spaces and punctuation don't matter:  "m10 x16" == "M10x16" == "m10-x16"
- word order doesn't matter:            "bolt 10x16" finds "10x16 ALLEN Bolt"
- letters and numbers can be glued:     "allenbolt", "100kn", "r25ballscrew"
- small spelling slips are tolerated:   "timing" finds "TIMMING BELT"
Results are ranked: exact / starts-with matches first, then everything else.
"""
import difflib
import re
from functools import lru_cache

# glued text like "10x16bolt" or "r25ballscrew" -> ["10x16", "bolt"] / ["r25", "ballscrew"]
_SPLIT = re.compile(r"[a-z]{3,}|[a-z]{0,2}\d+(?:x\d+)*(?:[a-z]{1,2}(?![a-z]))?")
SEARCH_FIELDS = ("name", "part_id", "description", "location", "category")


def compact(text):
    """Lower-case and drop everything that isn't a letter or digit."""
    return re.sub(r"[^a-z0-9.]", "", str(text or "").lower()).replace(".", "")


@lru_cache(maxsize=512)
def _query_terms(query):
    q = str(query or "").lower().strip()
    words = [w for w in re.split(r"[\s,;/|]+", q) if w]
    terms = []
    for w in words:
        c = compact(w)
        if not c:
            continue
        pieces = tuple(_SPLIT.findall(c))
        terms.append((c, pieces if len(pieces) > 1 else ()))
    return compact(q), tuple(terms)


def _haystack(part):
    raw = " ".join(str(part.get(f) or "") for f in SEARCH_FIELDS).lower()
    return compact(raw), tuple(compact(w) for w in re.split(r"[^a-z0-9.]+", raw) if w)


def _fuzzy_word(term, words):
    if len(term) < 4 or not term.isalpha():
        return False
    for w in words:
        if abs(len(w) - len(term)) <= 2 and w[:1] == term[:1]:
            if difflib.SequenceMatcher(None, term, w).ratio() >= 0.8:
                return True
    return False


def match_score(part, query):
    """0 = no match; higher = better match."""
    whole, terms = _query_terms(query)
    if not whole:
        return 1
    hay, words = _haystack(part)
    name = compact(part.get("name"))
    code = compact(part.get("part_id"))
    if whole == code or whole == name:
        return 100
    score = 0
    if code.startswith(whole) or name.startswith(whole):
        score = 80
    elif whole in name or whole in code:
        score = 60
    elif whole in hay:
        score = 50
    if score:
        return score
    # every typed word must be found somewhere (any order)
    fuzzy_used = False
    in_name = True
    for term, pieces in terms:
        if term in hay:
            in_name = in_name and (term in name or term in code)
            continue
        if pieces and all(p in hay for p in pieces):
            in_name = in_name and all(p in name or p in code for p in pieces)
            continue
        if _fuzzy_word(term, words):
            fuzzy_used = True
            continue
        return 0
    if fuzzy_used:
        return 25
    return 45 if in_name else 40


def search_parts(parts, query, limit=None):
    """Return parts matching the query, best matches first (stable within a score)."""
    if not str(query or "").strip():
        return list(parts) if limit is None else list(parts)[:limit]
    scored = []
    for idx, p in enumerate(parts):
        s = match_score(p, query)
        if s:
            scored.append((-s, idx, p))
    scored.sort(key=lambda t: (t[0], t[1]))
    result = [p for _, _, p in scored]
    return result if limit is None else result[:limit]


def row_matches(values, query):
    """Same rules for any row of text values (history, PR lists...)."""
    if not str(query or "").strip():
        return True
    fake = {"name": " ".join(str(v or "") for v in values)}
    return match_score(fake, query) > 0
