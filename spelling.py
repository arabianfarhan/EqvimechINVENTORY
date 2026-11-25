"""Spelling check for item names and descriptions.

Only clear misspellings of ordinary words are fixed. Sizes, numbers, codes, brand names and
abbreviations (SPAC, CIE, OMI, BEP, MOR, TVS, WPU...) are never touched. The case of the original
word is kept: WEEL -> WHEEL, Weel -> Wheel, weel -> wheel.
"""
import re

# phrases first (a word that is only wrong next to another word)
PHRASE_FIXES = {
    "suport plat": "support plate",
    "nob handle": "knob handle",
    "lad cell": "load cell",
    "draig chain": "drag chain",
    "c chaine": "c channel",
    "timmer pully": "timing pulley",
}

WORD_FIXES = {
    "alluminum": "aluminium",
    "boult": "bolt",
    "brakect": "bracket",
    "briqquetes": "briquettes",
    "briquet": "briquette",
    "carrage": "carriage",
    "chainel": "channel",
    "circule": "circle",
    "cylnder": "cylinder",
    "dumbbel": "dumbbell",
    "dumbble": "dumbbell",
    "dumble": "dumbbell",
    "egde": "edge",
    "extenstion": "extension",
    "fom": "foam",
    "granding": "grinding",
    "greet": "grit",
    "handel": "handle",
    "hosing": "housing",
    "hydrolic": "hydraulic",
    "iddler": "idler",
    "intender": "indenter",
    "konb": "knob",
    "motar": "motor",
    "naylon": "nylon",
    "pemeability": "permeability",
    "permability": "permeability",
    "pich": "pitch",
    "pnematic": "pneumatic",
    "pnumatic": "pneumatic",
    "pully": "pulley",
    "safty": "safety",
    "scerew": "screw",
    "scerw": "screw",
    "srew": "screw",
    "singel": "single",
    "stifness": "stiffness",
    "suport": "support",
    "synchronics": "synchronous",
    "teble": "table",
    "tickness": "thickness",
    "timming": "timing",
    "tyep": "type",
    "washar": "washer",
    "weel": "wheel",
    "wight": "weight",
    "unbreko": "unbrako",
}

_LETTER_EDGE = r"(?<![A-Za-z]){}(?![A-Za-z])"


def _same_case(original, replacement):
    if original.isupper():
        return replacement.upper()
    if original[:1].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement


def _sub_words(text, wrong, right):
    # each word of a phrase keeps the case of the word it replaces
    pattern = re.compile(_LETTER_EDGE.format(r"\s+".join(re.escape(w) for w in wrong.split())), re.IGNORECASE)

    def repl(m):
        olds = m.group(0).split()
        news = right.split()
        if len(olds) == len(news):
            return " ".join(_same_case(o, n) for o, n in zip(olds, news))
        return _same_case(m.group(0), right)

    return pattern.sub(repl, text)


def correct_text(text):
    """The text with known misspellings fixed (unchanged if none)."""
    out = str(text or "")
    for wrong, right in PHRASE_FIXES.items():
        out = _sub_words(out, wrong, right)
    for wrong, right in WORD_FIXES.items():
        out = _sub_words(out, wrong, right)
    return out


def spelling_suggestions(parts, fields=("name", "description")):
    """[{part_id, field, current, suggested}] for active items whose text has a known misspelling."""
    found = []
    for p in parts:
        if not p.get("active"):
            continue
        for f in fields:
            current = str(p.get(f) or "")
            fixed = correct_text(current)
            if fixed != current:
                found.append({"part_id": p["part_id"], "field": f, "current": current, "suggested": fixed})
    return found
