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


PHONE_RE = re.compile(r"(?:\+?995[ \-]?)?(?:5\d{2}|0\d{2,3})[ \-]?\d{2,3}[ \-]?\d{2}[ \-]?\d{2,3}")


def extract_phones(text: str) -> list[str]:
    return list(dict.fromkeys(m.group(0).strip() for m in PHONE_RE.finditer(text or "")))
