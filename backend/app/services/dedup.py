"""Phone / address normalisation and matching (port of apps-script normP / normA / checkProperty)."""
import re


def norm_phone(s) -> str:
    r = re.sub(r"[ +\-()]", "", str(s or ""))
    r = re.sub(r"^995", "", r)
    return re.sub(r"^0", "", r)


def norm_address(s) -> str:
    """normA kept latin+digits only; we also keep Cyrillic/Georgian letters so local addresses match too."""
    r = re.sub(r"[^a-z0-9а-яёႠ-ჿ ]", " ", str(s or "").lower())
    return re.sub(r" +", " ", r).strip()


def phone_match(a: str, b: str) -> bool:
    """Same number: equal normalized phones, or the same last 9 digits (Georgian mobile) when both have ≥9 digits.
    No substring matching: "555123" must not match "555123456"."""
    a, b = norm_phone(a), norm_phone(b)
    if len(a) > 4 and a == b:
        return True
    da, db = re.sub(r"\D", "", a), re.sub(r"\D", "", b)
    return len(da) >= 9 and len(db) >= 9 and da[-9:] == db[-9:]


# tokens that say nothing about the building: city, districts (bot/ingest.py DISTRICTS), street-type words
CITY = {"tbilisi", "тбилиси", "თბილისი", "georgia", "грузия", "city", "город", "г"}
DISTRICT_TOKENS = {
    "ваке", "сабуртало", "вера", "мтацминда", "сололаки", "дидубе", "диди", "дигоми", "глдани", "исани", "самгори",
    "авлабари", "ортачала", "крцаниси", "надзаладеви", "чугурети", "лиси", "вазисубани", "варкетили", "багеби",
    "нуцубидзе", "марджанишвили", "бахтриони", "мухиани", "окросубани", "цхнети", "vake", "saburtalo", "vera",
    "mtatsminda", "sololaki", "didube", "didi", "digomi", "dighomi", "gldani", "isani", "samgori", "avlabari",
    "ortachala", "krtsanisi", "nadzaladevi", "chugureti", "lisi", "vazisubani", "varketili", "bagebi", "nutsubidze",
    "marjanishvili", "bakhtrioni", "mukhiani", "okrosubani", "tskhneti", "district", "район", "р", "н", "massive",
    "массив"}
STREET_WORDS = {"ул", "улица", "пр", "проспект", "пер", "переулок", "пл", "площадь", "шоссе", "тупик", "д", "дом", "кв",
                "квартира", "корп", "корпус", "st", "str", "street", "ave", "avenue", "av", "rd", "road", "lane", "ln",
                "blind", "alley", "sq", "square", "highway", "hwy", "no", "apt", "apartment", "flat", "building", "bldg",
                "kucha", "qucha", "gamziri", "chikhi", "ქუჩა", "გამზირი", "ჩიხი", "შესახვევი", "the", "of"}


def _address_parts(s: str) -> tuple[set[str], set[str]]:
    """(street words, house numbers) as whole tokens; district names are dropped unless they are the only words
    (several districts are also street names: Nutsubidze, Marjanishvili)."""
    tokens = norm_address(s).split(" ")
    numbers = {t for t in tokens if t[:1].isdigit()}
    words = [t for t in tokens if t and not t[:1].isdigit() and t not in CITY and t not in STREET_WORDS and len(t) > 1]
    street = {t for t in words if t not in DISTRICT_TOKENS}
    return (street or set(words)), numbers


def address_match(a: str, b: str) -> bool:
    """Same address: equal after normalization, or the same street word AND the same house number (whole tokens).
    "Chavchavadze 1" ≠ "Chavchavadze 10"; "Tbilisi, Vake, Chavchavadze 5" ≠ "Tbilisi, Vake, Paliashvili 7"."""
    na, nb = norm_address(a), norm_address(b)
    if len(na) <= 2 or len(nb) <= 2:
        return False
    if na == nb:
        return True
    (wa, num_a), (wb, num_b) = _address_parts(na), _address_parts(nb)
    return bool(wa & wb) and bool(num_a & num_b)


PHONE_RE = re.compile(r"(?<![\w+])(?:\+?995[ \-]?)?(?:5(?:[ .\-]?\d){8}|0\d{2,3}[ \-]?\d{2,3}[ \-]?\d{2}[ \-]?\d{2,3})(?!\d)")


def extract_phones(text: str) -> list[str]:
    return list(dict.fromkeys(m.group(0).strip() for m in PHONE_RE.finditer(text or "")))


# ---- «В архиве по этому адресу» (David 2026-11-12): the same building across scripts and spellings ----
# Cyrillic / Georgian → one rough Latin spelling. Not a standard transliteration: only what ad writers
# actually vary (ქ/კ/k, ხ/х/kh, ჯ/дж/j, ღ/г/gh/g, ც/წ/ц/ts).
_TR = {**dict(zip("абвгдезийклмнопрстуфыэ", "abvgdeziiklmnoprstufye")),
       "ё": "e", "ж": "zh", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sh", "ю": "iu", "я": "ia", "ъ": "", "ь": "",
       **dict(zip("აბგდევზთიკლმნოპჟრსტუფქღყშჩცძწჭხჯჰ",
                  ["a", "b", "g", "d", "e", "v", "z", "t", "i", "k", "l", "m", "n", "o", "p", "zh", "r", "s", "t", "u",
                   "p", "k", "gh", "q", "sh", "ch", "ts", "dz", "ts", "ch", "kh", "j", "h"]))}
_SKELETON = [("dzh", "j"), ("dj", "j"), ("gh", "g"), ("kh", "h"), ("x", "h"), ("tz", "ts"), ("c", "ts"), ("tsh", "ch"),
             ("y", "i"), ("w", "v"), ("q", "k")]
_OLD = {"old", "new", "dzveli", "staryi", "novyi", "akhali", "ahali"}  # «Old Tbilisi», «ძველი თბილისი»
# «15 (Block 6)», «Vazha-Pshavela VII Block»: a block / corpus word or a roman numeral never names the street
_BLOCK = {"block", "blok", "блок", "корпус", "корп", "korpus", "ბლოკი", "კორპუსი", "ii", "iii", "iv", "vi", "vii", "viii", "ix",
          "xi", "xii"}


def _skeleton(t: str) -> str:
    t = "".join(_TR.get(ch, ch) for ch in t)
    for a, b in _SKELETON:
        t = t.replace(a, b)
    if len(t) >= 5:  # Georgian genitive: ბაქრაძის / Bakradze, კოსტავას / Kostava → one stem
        t = t[:-2] if t.endswith("is") else t[:-1] if t[-1] == "s" and t[-2] in "aeiou" else t
        t = t[:-1] if t[-1] in "aeiou" else t
    return t


_STOP = {_skeleton(w) for w in CITY | DISTRICT_TOKENS | STREET_WORDS | _OLD | _BLOCK}
_HOUSE = re.compile(r"^(\d{1,4})([a-zа-яა-ჰ]?)$")


def street_house(addr: str | None) -> tuple[frozenset, int | None, str]:
    """("bakradze" street words, 20, "a") of «Old Tbilisi, Vera, Bakradze 20A» / «20A Dimitri Bakradze St» /
    «ул. Бакрадзе, 20а». The street is the comma part holding the house number (or the part before a bare number),
    so districts and cities in other parts never count as the street."""
    parts = [norm_address(p) for p in re.split(r"[,;]", str(addr or "").lower())]
    parts = [p for p in parts if p]
    for i, p in enumerate(parts):
        toks = p.split(" ")
        house = next((m for m in map(_HOUSE.match, toks) if m), None)
        if not house:
            continue
        def street_of(words):
            return frozenset(s for s in map(_skeleton, words) if len(s) > 2 and s not in _STOP)
        street = street_of(t for t in toks if not t[:1].isdigit())
        if not street and i:  # «Bezhan Kalandadze St, 15 (Block 6)»: the street is the part before the number
            street = street_of(parts[i - 1].split(" "))
        if street:
            return street, int(house.group(1)), _skeleton(house.group(2))
    return frozenset(), None, ""


def _same_street(a: frozenset, b: frozenset) -> bool:
    """Every word of the shorter name is in the longer one: «Bakradze» ⊂ «Dimitri Bakradze»,
    but «Dimitri Uznadze» ⊄ «Dimitri Bakradze». A one-letter slip in a long word still counts (Bakradze / Bakradse)."""
    small, big = sorted((a, b), key=len)
    if not small:
        return False
    def has(w):
        return w in big or (len(w) >= 6 and any(len(x) == len(w) and sum(p != q for p, q in zip(w, x)) <= 1 for x in big))
    return all(has(w) for w in small)


NEAR_HOUSES = 4  # «16 Bakradze» next to «20 Bakradze»: neighbour buildings David wants to see too


def house_relation(a: str | None, b: str | None) -> str | None:
    """How address b relates to a: "тот же дом" (20 = 20), "соседний корпус 20A" (20 / 20A),
    "рядом по улице, дом 16" (same street, ≤ NEAR_HOUSES apart), else None."""
    return relation(street_house(a), street_house(b))


def relation(pa: tuple, pb: tuple) -> str | None:
    """house_relation on already parsed street_house() tuples (the base is parsed once per batch)."""
    (sa, na, la), (sb, nb, lb) = pa, pb
    if na is None or nb is None or not _same_street(sa, sb):
        return None
    if na == nb:
        return "тот же дом" if la == lb else f"соседний корпус {nb}{lb.upper()}"
    if abs(na - nb) <= NEAR_HOUSES:
        return f"рядом по улице, дом {nb}{lb.upper()}"
    return None
