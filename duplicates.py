"""Find items that are probably the same physical part entered twice.

Names alone are misleading ("Bearing 6304" and "Bearing 30305C" share the
name "Bearing" but are different parts), so two items are only suggested
when their *sizes/numbers* match AND their key words overlap, e.g.
"10x16 ALLEN Bolt HT" (ALLEN Bolt HT M10x16) and "M10x16 Allen Bolt".
"""
import re

# words that don't tell parts apart
_IGNORE = {"nos", "pcs", "no", "m", "x", "mm", "make", "with", "for", "and", "the", "of", "type", "new"}
# words that DO make two otherwise-identical parts different
_EXCLUSIVE = [{"@a", "@b", "@c", "@d"}, {"ss", "ms", "ht", "gi", "brass", "aluminium", "aluminum", "plastic"}, {"male", "female"},
              {"left", "right", "lh", "rh"}, {"2p", "3p", "4p"}]

_NUM = re.compile(r"\d+(?:\.\d+)?")
_WORD = re.compile(r"[a-z]+")


def _fingerprint(part):
    text = f"{part.get('name') or ''} {part.get('part_id') or ''}".lower()
    text = text.replace("timming", "timing").replace("weel", "wheel").replace("wight", "weight")
    nums = frozenset(n.lstrip("0") or "0" for n in _NUM.findall(text))
    words = frozenset(w for w in _WORD.findall(text) if w not in _IGNORE and len(w) > 1)
    poles = frozenset(re.findall(r"\b\d+p\b", text))
    letters = frozenset("@" + l for l in re.findall(r"(?:^|[\s\-_/(])([a-d])(?=$|[\s\-_/)])", text))  # "Temp A" vs "Temp B"
    return nums, words | poles | letters


# a word present in only ONE of the two items that marks a different part
_HARD_DIFF = {"csk", "countersunk", "top", "bottom", "base", "temp", "semi", "circule", "circle", "butterfly",
              "ball", "brass", "check", "chuck", "bush", "holding", "nut", "washer", "spring", "plate", "cover",
              "adapter", "housing", "bearing", "coupling", "pulley", "pully", "gear", "shaft", "front", "rear",
              "upper", "lower", "inner", "outer", "fixture", "jaw", "grip"}
# words whose absence doesn't matter (people often leave them out)
_SOFT_DIFF = {"ht", "hex", "socket", "head", "std", "standard", "steel", "allen", "pc", "set", "w", "gm", "kg", "kn"}


def _conflict(a_words, b_words):
    if (a_words ^ b_words) & _HARD_DIFF:
        return True
    for group in _EXCLUSIVE:
        a, b = a_words & group, b_words & group
        if a and b and a != b:
            return True
    return False


def is_possible_duplicate(a, b):
    """True if two items look like the same part (used to warn when adding a new item)."""
    an, aw = _fingerprint(a)
    bn, bw = _fingerprint(b)
    if an != bn or not aw or not bw or _conflict(aw, bw):
        return False
    overlap = len(aw & bw) / len(aw | bw)
    return overlap >= (0.5 if an else 0.8)


def pair_key(a_code, b_code):
    return tuple(sorted([str(a_code), str(b_code)]))


def find_duplicate_groups(parts, dismissed=()):
    """Return a list of suggestions: {"items": [part, part, ...], "confidence": "High"|"Possible", "why": str}."""
    active = [p for p in parts if p.get("active")]
    prints = [(p, *_fingerprint(p)) for p in active]
    pairs = []
    for i in range(len(prints)):
        a, an, aw = prints[i]
        if not an and len(aw) < 2:
            continue
        for j in range(i + 1, len(prints)):
            b, bn, bw = prints[j]
            if an != bn:
                continue
            if pair_key(a["part_id"], b["part_id"]) in dismissed:
                continue
            if not aw or not bw or _conflict(aw, bw):
                continue
            overlap = len(aw & bw) / len(aw | bw)
            if overlap < 0.5:
                continue
            same_qty = int(a.get("quantity") or 0) == int(b.get("quantity") or 0) and int(a.get("quantity") or 0) > 0
            if not an and overlap < 0.8:
                continue  # no sizes to compare: only very close names
            only_soft = (aw ^ bw) <= _SOFT_DIFF
            high = bool(an) and (only_soft or (overlap >= 0.75 and same_qty))
            high = high or (not an and overlap == 1)
            why = []
            if an:
                why.append("same size " + "/".join(sorted(an, key=lambda n: float(n))))
            why.append(f"{int(overlap * 100)}% same words")
            if same_qty:
                why.append(f"same stock ({int(a.get('quantity') or 0)})")
            pairs.append((a, b, "High" if high else "Possible", ", ".join(why)))

    # join overlapping pairs into groups (A~B, B~C -> A,B,C)
    groups, index = [], {}
    for a, b, conf, why in pairs:
        ga, gb = index.get(a["part_id"]), index.get(b["part_id"])
        if ga is None and gb is None:
            g = {"items": [a, b], "confidence": conf, "why": why}
            groups.append(g)
            index[a["part_id"]] = index[b["part_id"]] = g
        elif ga is not None and gb is None:
            ga["items"].append(b); index[b["part_id"]] = ga
        elif gb is not None and ga is None:
            gb["items"].append(a); index[a["part_id"]] = gb
        elif ga is not gb:
            ga["items"] += gb["items"]
            for p in gb["items"]:
                index[p["part_id"]] = ga
            groups.remove(gb)
        if conf == "High":
            index[a["part_id"]]["confidence"] = "High"
    groups.sort(key=lambda g: (g["confidence"] != "High", str(g["items"][0].get("name") or "").lower()))
    return groups
