"""One-off: the old «Rent In Tbilisi Form - Statistic» workbook (xlsx export) → owners, properties, deals, leads.

    python -m app.importers.old_sheet FILE.xlsx            # dry run: prints the counts, rolls back
    python -m app.importers.old_sheet FILE.xlsx --commit

Runs after sheets_csv + legacy_objects load and goes into the same tables, no new ones:
  - owner sheets (Owner Traking System 2.0 / 1.0, SALE, Batumi, FROM PLATFORM) → owners by phone (last 9 digits) and
    properties: a row joins an existing object (same cadastral; else same address + bedrooms, no conflicting owner /
    floor / area, exactly one candidate) and only fills its empty fields; otherwise a new archived legacy object;
  - Rent_out_Apartment → deals, only the rows sheets_csv did not load (same ID + start, or same start + price + place);
  - Clients Traking System, RENT Clients Information → clients (channel "legacy") + archived leads.
Rent Agreement sheets are skipped: passport numbers and test junk, the deals already hold the contracts' data.
Re-running is safe: each row's key sits in properties.ai_flags.old_sheet / deals.raw._import_key / clients.external_id."""
import hashlib
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime

import openpyxl
from sqlalchemy import select

from app.db import SessionLocal
from app.importers.legacy_objects import addr_key
from app.importers.sheets_csv import day, num
from app.models import Client, Deal, Lead, Owner, Property, User
from app.services.common import TZ, now
from app.services.dedup import norm_address, norm_phone

JUNK = {"", "-", "--", "?", "??", "???", "xz", ".", "0", "none", "нет", "n/a"}
SKIP_AGENTS = {"open", "1.0", "1"}
# spelling variants of one person in the old sheets → the name/hashtag the CRM already has (legacy_objects agents)
ALIASES = {"vitalik": "vitali", "vitaly": "vitali", "omar": "omari", "grigoriy": "grigori", "victoria": "vika",
           "viktoria": "vika", "viktoriaeliboshvili": "vika", "oxanam": "oxanee", "oksanam": "oxanee", "оксана": "oxanee",
           "dima": "dmitri", "dimitri": "dmitri", "юрий": "yuri", "irakli10": "irakli", "zaza10": "zaza",
           # confirmed by Stanislav 2026-11-12
           "davidt": "david", "iraklisangulia": "irakli", "irakli2": "irakli", "irakli3": "irakli",
           "georged": "giorgi", "giorgi2": "giorgi", "oksana2": "oksana", "denisiurievich": "denis"}


def s(v) -> str | None:
    t = re.sub(r"\s+", " ", str(v if v is not None else "")).strip()
    return None if t.lower() in JUNK else t


def when(v) -> datetime | None:
    if isinstance(v, datetime):
        return v.replace(tzinfo=TZ) if v.year > 1900 else None
    return None


def phone9(v) -> str | None:
    """The last 9 digits of a usable phone; fakes (555 555 555, one repeated digit) are None."""
    d = re.sub(r"\D", "", re.sub(r"\.0$", "", str(v or "").strip()))
    d = re.sub(r"^995", "", d).lstrip("0")
    return d[-9:] if len(d) >= 9 and len(set(d[-9:])) > 2 else None


def beds(v) -> int | None:
    t = str(v or "").lower()
    if "studio" in t:
        return 0
    m = re.search(r"\d+", t)
    return int(m.group()) if m and int(m.group()) < 20 else None


def integer(v) -> int | None:
    n = num(v)
    return int(n) if n is not None and abs(n) < 10 ** 9 else None


def row_key(sheet: str, r) -> str:
    return f"old:{sheet}:" + hashlib.sha1("\x1f".join("" if c is None else str(c) for c in r).encode()).hexdigest()[:16]


def g(r, i):
    return r[i] if i is not None and i < len(r) else None


BUILDING = {"new": "NewBuilding", "new building": "NewBuilding", "old": "OldBuilding", "old building": "OldBuilding",
            "recnstruction": "Reconstruction", "reconstruction": "Reconstruction"}
METRO = {"technical univercity": "TCUniversity", "tcuniversity": "TCUniversity", "liberty square": "LibertySquare",
         "vazha pshavela": "VazhaPshavela", "station square": "StationSquare", "medical univercity": "MCUniversity",
         "state univercity": "STUniversity", "akhmeteli theatre": "AhmeteliTheatre", "avlabari": "AvlabariMetro",
         "isani": "IsaniMetro", "samgori": "SamgoriMetro"}

# column indexes per owner sheet (0-based, from the header row); None = the sheet has no such column
OWNER_SHEETS = {
    "Owner Traking System 2.0 (From ": dict(at=1, deal=4, ext=5, name=6, phone=7, addr=8, district=9, metro=10, beds=11,
                                            sale=12, price=14, area=15, notes=18, agent=19, cad=20, phone2=22, excl=23,
                                            link=24, link2=26, status=3, road=2),
    "Owner Traking System 1.0 (From ": dict(at=0, beds=1, metro=2, addr=3, building=4, floor=5, area=6, price=8, phone=9,
                                            deal=10, name=11, agent=12),
    "SALE Owner inforatmatiom": dict(at=0, beds=2, metro=3, addr=4, building=5, floor=6, area=7, sale=8, name=10,
                                     phone=11, agent=12, deal_fixed="Sale"),
    "Batumi Owners Information": dict(at=0, beds=2, addr=3, building=4, floor=5, area=6, price=11, district=15, name=16,
                                      phone=17, agent=18, city="Batumi"),
    "Owner FROM PLARFORM MYHOME,SS (": dict(at=1, road=2, kind=3, ext=5, name=6, phone=7, addr=8, district=9, metro=10,
                                            beds=11, sale=12, price=14, area=15, building=17, agent=19, excl=21, link=26),
}


class Run:
    def __init__(self, db):
        self.db, self.n = db, Counter()
        self.users = {}
        for u in db.scalars(select(User).order_by(User.is_active.desc(), User.id)):  # an active account wins a shared name
            for k in (u.agent_tag, u.name):
                if k:
                    self.users.setdefault(self.akey(k), u)
        self.owners = {}
        for o in db.scalars(select(Owner)):
            p = phone9(o.phone_norm) or phone9(o.phone)
            if p:
                self.owners.setdefault(p, o)
        self.props = list(db.scalars(select(Property)))
        self.done = {k for p in self.props for k in ((p.ai_flags or {}).get("old_sheet") or [])}
        self.by_cad, self.by_ab, self.by_a = {}, defaultdict(list), defaultdict(list)
        for p in self.props:
            self.index(p)

    @staticmethod
    def akey(name: str) -> str:
        t = re.sub(r"\s*\(?\d+%\)?\s*$", "", name.strip().lstrip("#@").lower())
        t = re.sub(r"[\s._\-]+", "", t)
        return ALIASES.get(t, t)

    def agent(self, name) -> User | None:
        name = s(name)
        if not name or name.lower() in SKIP_AGENTS or self.akey(name) in SKIP_AGENTS:
            return None
        k = self.akey(name)
        if k not in self.users:  # like legacy_objects: an unknown agent becomes an inactive one, a manager can merge it
            u = User(name=re.sub(r"\s*\d+%$", "", name), role="agent", agent_tag=name, is_active=False)
            self.db.add(u)
            self.db.flush()
            self.users[k] = u
            self.n["agents_created_inactive"] += 1
            self.n["agent_new: " + name] += 1
        return self.users[k]

    def index(self, p: Property):
        if p.cadastral_code:
            self.by_cad.setdefault(p.cadastral_code.strip(), p)
        k = addr_key(p.address)
        if k:
            self.by_ab[(k, p.bedrooms)].append(p)
            self.by_a[k].append(p)

    def owner(self, phone, name, address, agent, is_agent=False) -> Owner | None:
        p = phone9(phone)
        if not p:
            return None
        o = self.owners.get(p)
        if o is None:
            o = Owner(name=s(name), phone="+995" + p if p[0] in "53" else s(phone), phone_norm=norm_phone("+995" + p),
                      address=s(address), address_norm=norm_address(address) if s(address) else None,
                      agent_id=agent.id if agent else None, is_agent=is_agent, notes="import:old_sheet")
            self.db.add(o)
            self.db.flush()
            self.owners[p] = o
            self.n["owners_created"] += 1
        else:
            o.name = o.name or s(name)
            o.address = o.address or s(address)
            o.agent_id = o.agent_id or (agent.id if agent else None)
        return o

    @staticmethod
    def is_batumi(p: Property) -> bool:
        return "batumi" in ((p.ai_flags or {}).get("legacy") or {}).get("sheet", "").lower() or \
            "batumi" in (p.district or "").lower()

    def match(self, f: dict, batumi: bool = False) -> Property | None:
        self.maybe = []
        if not batumi and f["cadastral_code"] and f["cadastral_code"] in self.by_cad:
            return self.by_cad[f["cadastral_code"]]
        k = addr_key(f["address"])
        if not k:
            return None
        # address = building, so same address + beds can still be a neighbour flat: score the candidates and take
        # the clear winner. A different owner phone is allowed only for a near-identical flat (same area and price):
        # the same flat often comes with another family member's number.
        scored = []
        for p in self.by_ab.get((k, f["bedrooms"]), []):
            if self.is_batumi(p) != batumi:
                continue
            if p.floor is not None and f["floor"] is not None and p.floor != f["floor"]:
                continue
            if p.area and f["area"] and abs(float(p.area) - f["area"]) > 0.1 * f["area"]:
                continue
            same_area = bool(p.area and f["area"] and abs(float(p.area) - f["area"]) <= 2)
            same_price = bool(p.price and f["price"] and float(p.price) == f["price"])
            other_owner = bool(p.owner_id and f["owner_id"] and p.owner_id != f["owner_id"])
            if other_owner and not (same_area and same_price):
                continue
            sc = (3 if f["owner_id"] and p.owner_id == f["owner_id"] else 0) + 2 * same_area + same_price + \
                (2 if f["floor"] is not None and p.floor == f["floor"] else 0)
            scored.append((sc, other_owner, p))
        scored.sort(key=lambda x: -x[0])
        if scored and scored[0][0] >= 3 and (len(scored) == 1 or scored[0][0] > scored[1][0]):
            return scored[0][2]
        free = [p for sc, other, p in scored if not other]
        if len(free) == 1 and len(scored) == 1:
            return free[0]
        if scored:
            self.n["objects_maybe_dup"] += 1
            self.maybe = [p.id for _, _, p in scored]
        return None

    # ---------------------------------------------------------------- owners + objects

    def owner_row(self, sheet: str, c: dict, r):
        name, phone, addr = s(g(r, c.get("name"))), g(r, c.get("phone")), s(g(r, c.get("addr")))
        if not phone9(phone) and not phone9(g(r, c.get("phone2"))) and not addr:
            return "skipped_empty"
        key = row_key(sheet, r)
        if key in self.done:
            return "unchanged"
        a = self.agent(g(r, c.get("agent")))
        o = self.owner(phone if phone9(phone) else g(r, c.get("phone2")), name, addr, a,
                       is_agent=(s(g(r, c.get("kind"))) or "").lower() == "agent")
        deal = c.get("deal_fixed") or s(g(r, c.get("deal"))) or "Rent"
        sale = integer(g(r, c.get("sale")))
        price = integer(g(r, c.get("price")))
        area = num(g(r, c.get("area")))
        pf = {"deal": "Sale" if deal.lower() == "sale" else "Rent"}
        metro = s(g(r, c.get("metro")))
        if metro:
            pf["metro"] = METRO.get(metro.lower(), metro.replace(" ", ""))
        if BUILDING.get((s(g(r, c.get("building"))) or "").lower()):
            pf["building"] = BUILDING[s(g(r, c.get("building"))).lower()]
        if sale and sale > 1000:
            pf["sale_price"] = sale
        cad = s(g(r, c.get("cad")))
        f = dict(address=addr, district=s(g(r, c.get("district"))) or c.get("city"), bedrooms=beds(g(r, c.get("beds"))),
                 floor=integer(g(r, c.get("floor"))), area=float(area) if area and 5 < area < 5000 else None,
                 price=price if price and 50 <= price < 100000 else None,
                 cadastral_code=cad if cad and re.search(r"\d{2}\.\d{2}\.\d{2}", cad) else None,
                 owner_id=o.id if o else None, agent_id=a.id if a else None)
        p = self.match(f, batumi=bool(c.get("city")))
        if p is not None:
            if o and p.owner_id and p.owner_id != o.id:  # same flat, another contact: keep it on the object
                p.description = "\n".join(x for x in (p.description, f"Другой контакт (старая таблица): {o.name or ''} {o.phone or ''}".strip()) if x)
                self.n["objects_merged_other_contact"] += 1
            for k, v in f.items():
                if v is not None and getattr(p, k) in (None, ""):
                    setattr(p, k, v)
            p.post_fields = {**pf, **(p.post_fields or {})}
            p.ai_flags = {**(p.ai_flags or {}), "old_sheet": [*((p.ai_flags or {}).get("old_sheet") or []), key]}
            self.done.add(key)
            self.n["objects_filled"] += 1
            return "merged"
        st = (s(g(r, c.get("status"))) or "").lower()
        status, reason = (("rented", "rented_by_us") if st == "agent rented" else
                          ("archived", "rented_by_owner") if st == "owner rented" else ("archived", "not_actual"))
        at = when(g(r, c.get("at")))
        links = [s(g(r, c.get(k))) for k in ("link", "link2")]
        notes = [(f"Возможный дубль: {', '.join(f'#{i}' for i in self.maybe)}" if self.maybe else None),
                 s(g(r, c.get("notes"))), (f"Откуда: {s(g(r, c.get('road')))}" if s(g(r, c.get("road"))) else None),
                 (f"Срок: {s(g(r, c.get('period'))) }" if s(g(r, c.get("period"))) else None)]
        p = Property(source_type="legacy", source_external_id=key, status=status, archive_reason=reason,
                     archived_at=at or now(), source_url=next((x for x in links if x and x.startswith("http")), None),
                     description="\n".join(x for x in notes if x) or None,
                     is_exclusive=(s(g(r, c.get("excl"))) or "").lower().startswith("exclusive"), post_fields=pf,
                     ai_flags={"old_sheet": [key], "legacy": {"key": key, "sheet": sheet.strip(),
                                                              "id": s(g(r, c.get("ext")))}}, **f)
        if at:
            p.created_at = at
        self.db.add(p)
        self.db.flush()
        self.index(p)
        self.done.add(key)
        self.n["objects_created"] += 1
        return "created"

    # ---------------------------------------------------------------- deals (Rent_out_Apartment)

    def deals(self, ws):
        have_id, have_place = set(), set()
        for d in self.db.scalars(select(Deal)):
            row = (d.raw or {}).get("row") or []
            if d.raw and d.raw.get("_sheet") == "Rent_out_Apartment" and len(row) > 21:
                have_id.add((str(row[21]).strip(), d.rent_from))
            for place in (d.cadastral, addr_key(d.address)):
                if place:
                    have_place.add((place.strip(), d.rent_from, int(d.price or 0)))
            if (d.raw or {}).get("_import_key", "").startswith("old:"):
                have_id.add(d.raw["_import_key"])
        for r in list(ws.iter_rows(values_only=True))[1:]:
            if not s(g(r, 6)) and not s(g(r, 12)):
                continue
            key = row_key("Rent_out_Apartment", r)
            start = day(g(r, 4)) if when(g(r, 4)) else None
            price = num(g(r, 2))
            sale = num(g(r, 3))
            cad, addr = s(g(r, 1)), s(g(r, 12))
            places = {(x.strip(), start, int(price or sale or 0)) for x in (cad, addr_key(addr)) if x}
            if key in have_id or (s(g(r, 21)), start) in have_id or places & have_place:
                self.n["deals_unchanged"] += 1
                continue
            pct = integer(num(g(r, 18)) * 100) if num(g(r, 18)) is not None and num(g(r, 18)) <= 1 else None
            a = self.agent(g(r, 15))
            p = self.by_cad.get(cad) if cad else None
            if p is None and addr_key(addr):
                c = self.by_a.get(addr_key(addr)) or []
                p = c[0] if len(c) == 1 else None
            d = Deal(source="import", status="confirmed", t14_status="done", agent_id=a.id if a else None,
                     property_id=p.id if p else None, cadastral=cad, price=price or sale, rent_from=start,
                     rent_to=day(g(r, 5)) if when(g(r, 5)) else None, owner_name=s(g(r, 6)), owner_phone=s(g(r, 7)),
                     client_name=s(g(r, 9)), client_phone=s(g(r, 10)), address=addr, metro=s(g(r, 14)),
                     commission_total=num(g(r, 16)) or num(g(r, 17)), agent_level=pct if pct in (50, 70, 90) else None,
                     deal_type="sale" if sale and not price else "rent",
                     month=start.strftime("%m/%Y") if start else None,
                     raw={"_import_key": key, "_sheet": "old:Rent_out_Apartment",
                          "row": ["" if x is None else str(x) for x in r]})
            if when(g(r, 0)):
                d.created_at = when(g(r, 0))
            self.db.add(d)
            have_id.add(key)
            have_place |= places
            self.n["deals_created"] += 1

    # ---------------------------------------------------------------- clients → archived leads

    LEAD_STATUS = {"заселили": "rented", "договор продлён": "rented", "сами нашли": "refused", "не отвечает": "refused",
                   "агент назначен": "agent_assigned", "первый контакт": "operator_taken"}

    def lead(self, sheet: str, r, name, contact, status, agent, request: list, note: list, at):
        key = row_key(sheet, r)
        if self.db.scalar(select(Client.id).where(Client.channel == "legacy", Client.external_id == key)):
            return "unchanged"
        contact = s(contact)
        t = (at or now()).isoformat()
        c = Client(channel="legacy", external_id=key, name=s(name),
                   username=contact.lstrip("@") if contact and contact.startswith("@") else None,
                   phone=contact if contact and not contact.startswith("@") else None, lang="ru", state="{}",
                   profile="{}", created_at=t, updated_at=t)
        self.db.add(c)
        self.db.flush()
        st = self.LEAD_STATUS.get((s(status) or "").lower(), "new")
        a = self.agent(agent)
        self.db.add(Lead(client_id=c.id, status=st, agent_id=a.id if a else None,
                         request=", ".join(x for x in request if x) or None,
                         client_note="\n".join(x for x in note if x) or None,
                         refusal_reason=s(status) if st == "refused" else None, archived_at=now(),
                         created_at=t, updated_at=t, created_ts=at or now()))
        self.n["leads_created"] += 1
        return "created"

    def clients(self, wb):
        for r in list(wb["Clients Traking System"].iter_rows(values_only=True))[1:]:
            if not s(g(r, 3)) and not s(g(r, 4)) and not s(g(r, 5)):
                continue
            if (s(g(r, 6)) or "").lower() == "собственник":
                continue
            shown = [f"{s(g(r, i))} ({s(g(r, i + 1)) or '—'})" for i in range(20, 33, 2) if s(g(r, i))]
            price = integer(g(r, 13)) or integer(g(r, 12))
            req = [s(g(r, 2)), f"{integer(g(r, 14))} bed" if integer(g(r, 14)) is not None else None,
                   f"${price}" if price else None, s(g(r, 17)), s(g(r, 18)),
                   f"{integer(g(r, 15))} чел." if integer(g(r, 15)) else None, s(g(r, 16))]
            note = [f"Старая база {s(g(r, 3)) or ''}: {s(g(r, 6)) or ''}", f"Откуда: {s(g(r, 1))}" if s(g(r, 1)) else None,
                    s(g(r, 19)), "Показы: " + "; ".join(shown) if shown else None,
                    f"Заселили: {s(g(r, 41))}, {day(g(r, 42)) or ''} – {day(g(r, 43)) or ''}" if s(g(r, 41)) else None]
            contact = s(g(r, 5)) if s(g(r, 5)) and s(g(r, 5)) != s(g(r, 4)) else None
            self.n["leads:" + self.lead("Clients Traking System", r, g(r, 4), contact, g(r, 6),
                                        g(r, 39) or g(r, 21), req, note, when(g(r, 0)))] += 1
        for r in list(wb["RENT Clients Information"].iter_rows(values_only=True))[1:]:
            if not s(g(r, 1)) and not s(g(r, 2)):
                continue
            req = ["Rent", s(g(r, 3)), f"${integer(g(r, 4))}" if integer(g(r, 4)) else None, s(g(r, 5)), s(g(r, 6)),
                   s(g(r, 7))]
            note = ["Старая база (2023–24)", f"Объект: {s(g(r, 9))}" if s(g(r, 9)) else None]
            self.n["leads:" + self.lead("RENT Clients Information", r, g(r, 1), g(r, 2), None, g(r, 8), req, note,
                                        when(g(r, 0)))] += 1

    def run(self, wb):
        for sheet, cols in OWNER_SHEETS.items():
            for r in list(wb[sheet].iter_rows(values_only=True))[1:]:
                if not any(s(g(r, cols[k])) for k in ("name", "phone", "addr")):
                    continue
                try:
                    with self.db.begin_nested():
                        self.n[f"{sheet.strip()}:{self.owner_row(sheet, cols, r)}"] += 1
                except Exception as e:  # noqa: BLE001 — one bad row is counted, not fatal
                    self.n[f"{sheet.strip()}:error {type(e).__name__}"] += 1
        self.deals(wb["Rent_out_Apartment"])
        self.clients(wb)
        return self.n


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        sys.exit(__doc__)
    wb = openpyxl.load_workbook(argv[0], read_only=True, data_only=True)
    db = SessionLocal()
    n = Run(db).run(wb)
    for k, v in sorted(n.items()):
        print(f"{k:60} {v}")
    if "--commit" in argv:
        db.commit()
        print("committed")
    else:
        db.rollback()
        print("dry run: rolled back (add --commit)")


if __name__ == "__main__":
    main()
