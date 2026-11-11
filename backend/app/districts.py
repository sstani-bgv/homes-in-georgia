"""The channel's district filter hashtags (Irakli 11-11: «все районы из наших фильтров») — the one list for the CRM.
objects/listings.district keeps the Russian label (as before); the caption takes the tag. Old coarse / English values
(«Вере», «Ваке-Багеби», «Saburtalo», «Chughureti») still resolve. The frontend copy is frontend/src/lib/format.ts
DISTRICT_LIST (tests/test_districts.py keeps them equal)."""
import re

# (tag, ru, en, ka, group), grouped; Tskneti / Tskhneti are both channel tags (same place, two spellings)
DISTRICTS = [
    ("Mtatsminda", "Мтацминда", "Mtatsminda", "მთაწმინდა", "Старый Тбилиси и центр"),
    ("Sololaki", "Сололаки", "Sololaki", "სოლოლაკი", "Старый Тбилиси и центр"),
    ("Abanotubani", "Абанотубани", "Abanotubani", "აბანოთუბანი", "Старый Тбилиси и центр"),
    ("Kukia", "Кукия", "Kukia", "კუკია", "Старый Тбилиси и центр"),
    ("Vera", "Вера", "Vera", "ვერა", "Старый Тбилиси и центр"),
    ("Chugureti", "Чугурети", "Chugureti", "ჩუღურეთი", "Старый Тбилиси и центр"),
    ("Avlabari", "Авлабари", "Avlabari", "ავლაბარი", "Старый Тбилиси и центр"),
    ("Ortachala", "Ортачала", "Ortachala", "ორთაჭალა", "Старый Тбилиси и центр"),
    ("Krtsanisi", "Крцаниси", "Krtsanisi", "კრწანისი", "Старый Тбилиси и центр"),
    ("Ponichala", "Поничала", "Ponichala", "ფონიჭალა", "Старый Тбилиси и центр"),
    ("QvemoPonichala", "Нижняя Поничала", "Lower Ponichala", "ქვემო ფონიჭალა", "Старый Тбилиси и центр"),
    ("Vake", "Ваке", "Vake", "ვაკე", "Ваке и Сабуртало"),
    ("Bagebi", "Багеби", "Bagebi", "ბაგები", "Ваке и Сабуртало"),
    ("Vashlijvari", "Вашлиджвари", "Vashlijvari", "ვაშლიჯვარი", "Ваке и Сабуртало"),
    ("Lisi", "Лиси", "Lisi", "ლისი", "Ваке и Сабуртало"),
    ("SavnetisUbani", "Сванетский квартал", "Svaneti Quarter", "სვანეთის უბანი", "Ваке и Сабуртало"),
    ("Tskneti", "Цкнети", "Tskneti", "წყნეთი", "Ваке и Сабуртало"),
    ("Tskhneti", "Цхнети", "Tskhneti", "წყნეთი", "Ваке и Сабуртало"),
    ("Afrika", "Африка", "Afrika", "აფრიკა", "Ваке и Сабуртало"),
    ("Saburtalo", "Сабуртало", "Saburtalo", "საბურთალო", "Ваке и Сабуртало"),
    ("VazhaPshavela", "Важа-Пшавела", "Vazha-Pshavela", "ვაჟა-ფშაველა", "Ваке и Сабуртало"),
    ("Nutsubidze", "Нуцубидзе", "Nutsubidze", "ნუცუბიძე", "Ваке и Сабуртало"),
    ("Iverubani", "Ивертубани", "Ivertubani", "ივერთუბანი", "Ваке и Сабуртало"),
    ("Didube", "Дидубе", "Didube", "დიდუბე", "Дидубе и Дигоми"),
    ("Nadzaladevi", "Надзаладеви", "Nadzaladevi", "ნაძალადევი", "Дидубе и Дигоми"),
    ("Naxalovka", "Нахаловка", "Nakhalovka", "ნახალოვკა", "Дидубе и Дигоми"),
    ("Digomi", "Дигоми", "Digomi", "დიღომი", "Дидубе и Дигоми"),
    ("DidiDigomi", "Диди Дигоми", "Didi Digomi", "დიდი დიღომი", "Дидубе и Дигоми"),
    ("DigomiMassive", "Дигомский массив", "Digomi Massive", "დიღმის მასივი", "Дидубе и Дигоми"),
    ("Digomi1to9", "Дигоми 1–9", "Digomi 1–9", "დიღომი 1-9", "Дидубе и Дигоми"),
    ("Gldani", "Глдани", "Gldani", "გლდანი", "Глдани"),
    ("Temqa", "Темка", "Temqa", "თემქა", "Глдани"),
    ("Avchala", "Авчала", "Avchala", "ავჭალა", "Глдани"),
    ("Isani", "Исани", "Isani", "ისანი", "Исани и Самгори"),
    ("Samgori", "Самгори", "Samgori", "სამგორი", "Исани и Самгори"),
    ("Varketili", "Варкетили", "Varketili", "ვარკეთილი", "Исани и Самгори"),
    ("Vazisubani", "Вазисубани", "Vazisubani", "ვაზისუბანი", "Исани и Самгори"),
    ("Saguramo", "Сагурамо", "Saguramo", "საგურამო", "За городом"),
    ("OutOfTown", "За городом", "Out of town", "ქალაქგარეთ", "За городом"),
]
BY_TAG = {d[0]: d for d in DISTRICTS}
# old CRM / OSM / parser spellings -> tag (the old coarse «Старый город» has no single tag: it stays as it is)
_EXTRA = {"вере": "Vera", "ваке багеби": "Bagebi", "ваке сабуртало": "Vake", "нахаловка": "Naxalovka",
          "dighomi massive": "DigomiMassive", "digomi massiv": "DigomiMassive", "dighomi 1 9": "Digomi1to9",
          "ivertubani": "Iverubani", "iveriubani": "Iverubani", "svaneti": "SavnetisUbani", "svanetis ubani": "SavnetisUbani",
          "temka": "Temqa", "phonichala": "Ponichala", "kvemo ponichala": "QvemoPonichala", "africa": "Afrika",
          "vashlijvari": "Vashlijvari", "vashlidzhvari": "Vashlijvari", "nakhalovka": "Naxalovka", "tskhneti": "Tskhneti"}


def _norm(s: str) -> str:
    s = re.sub(r"(?<=[a-z])(?=[A-Z0-9])", " ", (s or "").lstrip("#"))  # DidiDigomi -> Didi Digomi
    s = re.sub(r"[^\w]+", " ", s.lower().replace("ё", "е")).strip()
    return re.sub(r"(\d)\s*to\s*(\d)", r"\1 \2", s.replace("gh", "g"))  # Dighomi / Chughureti (OSM) = Digomi / Chugureti


ALIASES = {_norm(a): d[0] for d in DISTRICTS for a in (d[0], d[1], d[2], d[3])} | {_norm(k): v for k, v in _EXTRA.items()}
_LONGEST = sorted(ALIASES, key=len, reverse=True)


def tag(value: str | None) -> str | None:
    """A stored district (ru label, tag, English, an old coarse value) -> the channel tag without '#', or None."""
    return ALIASES.get(_norm(value or ""))


def label(value: str | None) -> str | None:
    """The CRM's Russian label for a stored value; an unknown value (e.g. «Старый город») is shown as it is."""
    t = tag(value)
    return BY_TAG[t][1] if t else value


def find(text: str | None) -> str | None:
    """The first (longest) district name inside a free text, e.g. OSM's «Didi Dighomi» -> DidiDigomi (whole words)."""
    s = f" {_norm(text or '')} "
    return next((ALIASES[a] for a in _LONGEST if f" {a} " in s), None)
