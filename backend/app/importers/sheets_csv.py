"""Google Sheets CSV export → Postgres. Idempotent (re-running updates/skips, never duplicates).

    python -m app.importers.sheets_csv data/legacy-sheets

Every row runs in a savepoint: a junk cell never aborts the import, the row is counted as skipped.
Positional mapping where headers are unusable: Reports_Deals (no header; apps-script dealsSh.appendRow order),
Rent_out_Apartment (duplicate 'Price $$', trailing spaces), Agents/Recruits (empty header in column 6)."""
import csv
import hashlib
import re
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import (ROLES, Address, ChannelPost, Deal, Evaluation, News, Owner, Report, Setting, TickerMessage,
                        User)
from app.services import deals as deals_svc
from app.services import users as users_svc
from app.services.common import TZ
from app.services.dedup import norm_address, norm_phone

csv.field_size_limit(10 ** 8)

# ---------------------------------------------------------------- tolerant parsers


def s(v) -> str | None:
    v = (v or "").strip() if isinstance(v, str) else v
    return v or None


def num(v) -> Decimal | None:
    if v in (None, ""):
        return None
    t = re.sub(r"[^\d,.\-]", "", str(v)).replace(",", ".")
    if t.count(".") > 1:
        t = t.replace(".", "", t.count(".") - 1)
    try:
        return Decimal(t) if t not in ("", "-", ".") else None
    except InvalidOperation:
        return None


def integer(v) -> int | None:
    n = num(v)
    return int(n) if n is not None else None


def boolean(v) -> bool:
    return str(v or "").strip().lower() in ("true", "1", "yes", "да", "y", "эксклюзив", "exclusive")


def tg_id(v) -> int | None:
    t = str(v or "").strip()
    return int(t) if re.fullmatch(r"-?\d{5,15}", t) else None


DATE_FORMATS = ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%m/%d/%Y", "%d.%m.%y", "%Y-%m-%d %H:%M:%S", "%d.%m.%Y %H:%M:%S",
                "%d.%m.%Y %H:%M", "%m/%d/%Y %H:%M:%S", "%Y-%m-%dT%H:%M:%S")


def dt(v) -> datetime | None:
    t = str(v or "").strip()
    if not t:
        return None
    try:
        d = datetime.fromisoformat(t.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=TZ)
    except ValueError:
        pass
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(t[:19], fmt).replace(tzinfo=TZ)
        except ValueError:
            continue
    return None


def day(v) -> date | None:
    d = dt(v)
    return d.date() if d else None


def key_of(sheet: str, row: list) -> str:
    return f"{sheet}:" + hashlib.sha1("\x1f".join(row).encode()).hexdigest()[:16]


def read(path: Path) -> tuple[list[str], list[list[str]]]:
    if not path.exists() or path.stat().st_size == 0:
        return [], []
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = [r for r in csv.reader(f)]
    if not rows:
        return [], []
    return rows[0], [r for r in rows[1:] if any(c.strip() for c in r)]


def pad(r: list, n: int) -> list:
    return (r + [""] * n)[:max(n, len(r))]


# ---------------------------------------------------------------- importer


class Importer:
    def __init__(self, db: Session, folder: Path):
        self.db, self.folder = db, folder
        self.stats = defaultdict(lambda: defaultdict(int))
        self.errors: dict[str, list[str]] = defaultdict(list)
        self._users_by_name: dict[str, User] | None = None

    def rows(self, sheet: str):
        return read(self.folder / f"{sheet}.csv")

    def each(self, sheet: str, fn, positional_width: int | None = None):
        headers, rows = self.rows(sheet)
        for r in rows:
            r = pad(r, positional_width or len(headers))
            rec = r if positional_width else {h.strip(): (r[i] if i < len(r) else "") for i, h in enumerate(headers) if h.strip()}
            try:
                with self.db.begin_nested():
                    res = fn(r, rec) or "imported"
                self.stats[sheet][res] += 1
            except Exception as e:  # noqa: BLE001
                self.stats[sheet]["skipped"] += 1
                errors = self.errors[sheet]
                if len(errors) < 3:
                    errors.append(f"{type(e).__name__}: {str(e)[:120]}")
        self.db.commit()

    # -------- people

    def user_by_name(self, name) -> User | None:
        n = (s(name) or "").lstrip("#@").replace("_", " ").strip().lower()
        n = re.sub(r"\s*\(\d+%\)\s*$", "", n)
        if not n:
            return None
        if self._users_by_name is None:
            self._users_by_name = {}
            people = list(self.db.scalars(select(User).order_by(User.id)))
            for attr in ("name", "username"):  # exact names win over usernames
                for u in people:
                    if getattr(u, attr):
                        self._users_by_name.setdefault(getattr(u, attr).lower().replace("_", " "), u)
        return self._users_by_name.get(n)

    def _remember(self, u: User):
        if self._users_by_name is not None:
            for k in (u.name, u.username):
                if k:
                    self._users_by_name.setdefault(k.lower().replace("_", " "), u)

    def upsert_user(self, legacy_id: str, **f) -> str:
        db = self.db
        u = db.scalar(select(User).where(User.legacy_id == legacy_id))
        if u is None and f.get("tg_id"):
            u = users_svc.by_tg(db, f["tg_id"])
        if u is None and f.get("username"):
            u = db.scalar(select(User).where(func.lower(User.username) == f["username"].lower()))
        if u is None and f.get("login"):
            u = users_svc.by_login(db, f["login"])
        created = u is None
        if created:
            if not f.get("name"):
                raise ValueError("no name")
            u = User(legacy_id=legacy_id, name=f["name"], role=f.get("role") or "agent")
            db.add(u)
        for k, v in f.items():
            if v is None:
                continue
            if k == "tg_id" and u.tg_id and u.tg_id != v:
                continue
            if k == "tg_id" and db.scalar(select(User.id).where(User.tg_id == v, User.id != (u.id or 0))):
                continue  # tg id already belongs to another user
            if not created and k in ("role", "is_active") and (u.role == "superadmin" or (
                    f.get("role") == "recruit" and u.role != "recruit")):
                continue  # a promoted recruit stays an agent (promoteRecruit keeps the old Recruits row inactive)
            setattr(u, k, v)
        db.flush()
        self._remember(u)
        return "imported" if created else "updated"

    def people(self):
        def agent(r, _):
            return self.upsert_user(f"agent:{r[0]}", name=s(r[1]), username=(s(r[2]) or "").lstrip("@") or None, role="agent",
                                    is_active=boolean(r[3]), level=integer(r[4]) if integer(r[4]) in (50, 70, 90) else None,
                                    photo_url=s(r[6]), tg_id=tg_id(r[7]))

        def recruit(r, _):
            return self._recruit(r)
        self.each("Agents", agent, positional_width=8)
        self.each("Recruits", recruit, positional_width=8)

        def account(r, rec):
            role = (s(rec.get("role")) or "agent").lower()
            role = role if role in ROLES else {"manager": "operator", "оператор": "operator", "агент": "agent",
                                               "рекрут": "recruit"}.get(role, "agent")
            tid = tg_id(rec.get("tg_id"))
            legacy = f"account:{tid or s(rec.get('telegram')) or s(rec.get('name'))}"
            return self.upsert_user(legacy, name=s(rec.get("name")), username=(s(rec.get("telegram")) or "").lstrip("@") or None,
                                    tg_id=tid, role=role, phone=s(rec.get("phone")), birth_date=day(rec.get("birth_date")),
                                    last_login_at=dt(rec.get("last_login_at")))
        self.each("Agent_Accounts", account)

        def desk(r, rec):
            name, pw = s(rec.get("name")), s(rec.get("password"))
            if not name or not pw:
                raise ValueError("desk user without name/password")
            role = (s(rec.get("role")) or "operator").lower()
            role = role if role in ROLES else "admin" if "admin" in role else "operator"
            existing = users_svc.by_login(self.db, name)
            f = {"name": name, "login": name, "role": role}
            if not existing or not users_svc.check_password(pw, existing.password_hash):
                f["password_hash"] = users_svc.hash_password(pw)
            return self.upsert_user(f"desk:{name.lower()}", **f)
        self.each("Platform_Desk_Users", desk)

        def tg_user(r, rec):
            tid = tg_id(rec.get("tg_id"))
            if not tid:
                raise ValueError("bad tg_id")
            u = users_svc.by_tg(self.db, tid) or self.user_by_name(rec.get("username")) or self.user_by_name(rec.get("first_name"))
            if not u:
                return "unmatched"
            if u.tg_id == tid:
                return "unchanged"
            if u.tg_id or self.db.scalar(select(User.id).where(User.tg_id == tid)):
                return "conflict"
            u.tg_id = tid
            return "updated"
        self.each("TG_Users", tg_user)

        def counters(r, rec):
            u = self.user_by_name(rec.get("agent"))
            if not u:
                return "unmatched"
            n = integer(rec.get("exclusive_deals"))
            if n is None:
                raise ValueError("bad exclusive_deals")
            u.exclusive_deals = max(u.exclusive_deals or 0, n)
            return "updated"
        self.each("Agent_Counters", counters)

        def plans(r, rec):
            u = self.user_by_name(rec.get("recruit"))
            if not u:
                return "unmatched"
            p = dict(u.plans or {})
            for k in ("photo", "showing", "stickers", "rental"):
                v = integer(rec.get(f"{k}_plan"))
                if v:
                    p[k] = v
            u.plans = p
            return "updated"
        self.each("Counters", plans)

    def _recruit(self, r):
        res = self.upsert_user(f"recruit:{r[0]}", name=s(r[1]), username=(s(r[2]) or "").lstrip("@") or None,
                               role="recruit", is_active=boolean(r[4]), photo_url=s(r[6]), tg_id=tg_id(r[7]))
        mentor = self.db.scalar(select(User).where(User.legacy_id == f"agent:{s(r[3])}")) if s(r[3]) else None
        mentor = mentor or self.user_by_name(r[3])
        if mentor:
            u = self.db.scalar(select(User).where(User.legacy_id == f"recruit:{r[0]}"))
            if u and u.id != mentor.id:
                u.mentor_id = mentor.id
        return res

    # -------- owners

    def owners(self):
        def owner(r, _):
            name, phone, addr, link = (pad(r, 4) + [""])[:4]
            if not s(phone) and not s(addr):
                raise ValueError("owner without phone and address")
            pn, an = norm_phone(phone), norm_address(addr)
            q = select(Owner).where(Owner.phone_norm == pn, func.coalesce(Owner.address_norm, "") == an)
            if self.db.scalar(q):
                return "unchanged"
            self.db.add(Owner(name=s(name), phone=s(phone), phone_norm=pn, address=s(addr), address_norm=an, link=s(link),
                              notes="import:Owner_Base"))
            return "imported"
        self.each("Owner_Base", owner, positional_width=4)

    # -------- deals

    def _deal(self, sheet: str, row: list, **f) -> str:
        key = key_of(sheet, row)
        db = self.db
        if db.scalar(select(Deal.id).where((Deal.raw["_import_key"].astext == key) | Deal.raw["_merged"].contains([key]))):
            return "unchanged"
        agent = f.pop("agent", None)
        f["agent_id"] = agent.id if agent else None
        if f.get("recruit") is not None:
            r = f.pop("recruit")
            f["recruit_id"] = r.id if r else None
        f.pop("recruit", None)
        created_at = f.pop("created_at", None)
        rf = f.get("rent_from")
        f.setdefault("month", f"{rf.month}/{rf.year}" if rf else (f"{created_at.month}/{created_at.year}" if created_at else None))
        if not f.get("month"):
            f.pop("month", None)
        # the same deal is often written to several sheets: merge by agent + price + object + month (+ rent start),
        # so a re-rent of the same flat at the same price in another month stays a separate deal
        obj = f.get("cadastral") or f.get("ad_link")
        twin = None
        if obj and f.get("price") is not None:
            conds = [Deal.source == "import", Deal.agent_id.is_not_distinct_from(f["agent_id"]), Deal.price == f["price"],
                     (Deal.cadastral == obj) | (Deal.ad_link == obj),
                     Deal.month.is_not_distinct_from(f.get("month"))]
            if rf is not None:
                conds.append((Deal.rent_from == rf) | Deal.rent_from.is_(None))
            twin = db.scalar(select(Deal).where(*conds).order_by(Deal.id).limit(1))
        if twin is not None:
            for k, v in f.items():
                if v is not None and getattr(twin, k) in (None, "", 0):
                    setattr(twin, k, v)
            raw = dict(twin.raw or {})
            raw.setdefault("_merged", []).append(key)
            twin.raw = raw
            return "merged"
        comm = f.get("commission_total")
        if comm is not None and f.get("agent_amount") is None:
            f["agent_amount"] = comm - (f.get("platform_amount") or 0) - (f.get("recruit_amount") or 0)
        d = Deal(source="import", status="confirmed", t14_status=f.pop("t14_status", None) or "done",
                 raw={"_import_key": key, "_sheet": sheet, "row": row}, **f)
        if created_at:
            d.created_at = created_at
        db.add(d)
        return "imported"

    def deals(self):
        def reports_deals(r, _):
            # apps-script: [timestamp, message_id, agent, level, deal_num, link, price, commission, agent_comm, platform,
            #  cadastral, client_name, client_phone, owner_name, owner_phone, date_str, is_excl, source, raw, deal_type, month]
            lvl = integer(r[3])
            return self._deal("Reports_Deals", r, created_at=dt(r[0]), agent=self.user_by_name(r[2]),
                              agent_level=lvl if lvl in (50, 70, 90) else None, agent_deal_num=integer(r[4]),
                              ad_link=s(r[5]), price=num(r[6]), commission_total=num(r[7]), agent_amount=num(r[8]),
                              platform_amount=num(r[9]), cadastral=s(r[10]), client_name=s(r[11]), client_phone=s(r[12]),
                              owner_name=s(r[13]), owner_phone=s(r[14]), rent_from=day(r[15]),
                              is_exclusive="скл" in (r[16] or "").lower() and "неэкс" not in (r[16] or "").lower(),
                              deal_type="sale" if (r[19] or "").strip().lower() in ("sale", "продажа") else "rent",
                              month=s(r[20]) if re.fullmatch(r"\d{1,2}/\d{4}", (r[20] or "").strip()) else None)
        self.each("Reports_Deals", reports_deals, positional_width=21)

        def rental(r, rec):
            g = rec.get
            return self._deal("Reports_Rental", r, created_at=dt(g("timestamp")), agent=self.user_by_name(g("agent")),
                              recruit=self.user_by_name(g("recruit")), ad_link=s(g("ad_link_advertising")),
                              price=num(g("rent_price")), commission_total=num(g("agency_commission")),
                              recruit_amount=num(g("recruit_commission")), platform_amount=num(g("platform_commission")),
                              cadastral=s(g("cadastral_number")), client_tg=s(g("client_telegram")),
                              owner_name=s(g("owner_name")), owner_phone=s(g("owner_phone")), rent_from=day(g("rent_from")),
                              rent_to=day(g("rent_to")),
                              deal_type="sale" if (g("deal_type") or "").lower() in ("sale", "продажа") else "rent",
                              t14_date=day(g("t14_date")),
                              t14_status=(g("t14_status") or "").strip().lower() if (g("t14_status") or "").strip().lower()
                              in ("pending", "sent", "done") else None)
        self.each("Reports_Rental", rental)

        def rent_out(r, _):
            # positional (duplicate 'Price $$', trailing spaces): 0 ts, 1 cadastral, 2 price, 3 sale price, 4 start, 5 end,
            # 6 owner, 7 owner contact, 8 owner birth, 9 client, 10 client contact, 11 client birth, 12 address, 13 score,
            # 14 metro, 15 agent, 16 total commission, 17 total sale commission, 18 agent commission %, 19 price $$, 20 month, 21 id
            sale = num(r[3])
            pct = integer(r[18])
            return self._deal("Rent_out_Apartment", r, created_at=dt(r[0]), cadastral=s(r[1]),
                              price=num(r[2]) or num(r[19]) or sale, rent_from=day(r[4]), rent_to=day(r[5]),
                              owner_name=s(r[6]), owner_phone=s(r[7]), owner_birth=day(r[8]), client_name=s(r[9]),
                              client_phone=s(r[10]), client_birth=day(r[11]), address=s(r[12]), metro=s(r[14]),
                              agent=self.user_by_name(r[15]), commission_total=num(r[16]) or num(r[17]),
                              agent_level=pct if pct in (50, 70, 90) else None,
                              deal_type="sale" if sale and not num(r[2]) else "rent",
                              month=s(r[20]) if re.fullmatch(r"\d{1,2}/\d{4}", (r[20] or "").strip()) else None)
        self.each("Rent_out_Apartment", rent_out, positional_width=22)

        def rerented(r, rec):
            g = rec.get
            return self._deal("Reports_Rerented", r, created_at=dt(g("timestamp")), agent=self.user_by_name(g("agent")),
                              ad_link=s(g("link")), price=num(g("price")), client_name=s(g("client_name")),
                              client_phone=s(g("client_phone")), owner_name=s(g("owner_name")), owner_phone=s(g("owner_phone")),
                              rent_from=day(g("rent_from")), rent_to=day(g("rent_to")), cadastral=s(g("cadastral")),
                              is_rerented=True, commission_total=Decimal(0), platform_amount=Decimal(0))
        self.each("Reports_Rerented", rerented)

        def calc(r, rec):
            g = rec.get
            lvl = integer(g("agent_level"))
            return self._deal("Reports_Calc", r, created_at=dt(g("timestamp")), agent=self.user_by_name(g("agent")),
                              agent_level=lvl if lvl in (50, 70, 90) else None, price=num(g("price")),
                              commission_pct=num(g("comm_pct")), commission_total=num(g("comm_total")),
                              platform_amount=num(g("platform")), agent_amount=num(g("agent_amt")),
                              is_exclusive=boolean(g("is_exclusive")),
                              deal_type="sale" if (g("deal_type") or "").lower() in ("sale", "продажа") else "rent",
                              ad_link=s(g("link")), rent_from=day(g("rent_from")), rent_to=day(g("rent_to")))
        self.each("Reports_Calc", calc)

    # -------- evaluations

    def evaluations(self):
        def score(v):
            n = integer(v)
            return n if n is not None and 0 <= n <= 3 else None

        def ev(sheet, kind):
            def fn(r, rec):
                g = rec.get
                key = key_of(sheet, r)
                if self.db.scalar(select(Evaluation.id).where(Evaluation.raw["_import_key"].astext == key)):
                    return "unchanged"
                obj = s(g("object"))
                deal = self.db.scalar(select(Deal).where((Deal.ad_link == obj) | (Deal.cadastral == obj)).limit(1)) if obj else None
                result = (g("deal_result") or "").strip().lower()
                complaints = (g("complaints") or "").strip().lower()
                e = Evaluation(deal_id=deal.id if deal else None, kind=kind, author="agent", h2o_client=score(g("h2o_client")),
                               h2o_owner=score(g("h2o_owner")), h2o_process=score(g("h2o_process")),
                               result=result if result in ("clean", "dirty") else None, changes=s(g("changes")),
                               complaints=complaints in ("yes", "да", "true"), complaints_text=s(g("complaints_text")),
                               comment=s(g("comment")) or s(g("agent_comment")),
                               raw={"_import_key": key, "_sheet": sheet, **{k: v for k, v in rec.items()}})
                if dt(g("timestamp")):
                    e.created_at = dt(g("timestamp"))
                self.db.add(e)
            return fn
        self.each("Reports_T0", ev("Reports_T0", "t0"))
        self.each("Reports_T14", ev("Reports_T14", "t14"))

    # -------- reports

    def reports(self):
        def rep(sheet, kind, who="recruit", date_col="date"):
            def fn(r, rec):
                key = key_of(sheet, r)
                if self.db.scalar(select(Report.id).where(Report.data["_import_key"].astext == key)):
                    return "unchanged"
                person = self.user_by_name(rec.get(who))
                mentor = self.user_by_name(rec.get("mentor"))
                rep_date = day(rec.get(date_col)) or day(rec.get("timestamp"))
                data = {k: v for k, v in rec.items()}
                data.update(_import_key=key, _sheet=sheet)
                x = Report(kind=kind, author_id=person.id if person else None,
                           recruit_id=person.id if person and kind in ("photo", "showing", "stickers") else None,
                           mentor_id=mentor.id if mentor else None, date=rep_date, data=data)
                if dt(rec.get("timestamp")):
                    x.created_at = dt(rec.get("timestamp"))
                self.db.add(x)
            return fn
        self.each("Reports_Photo", rep("Reports_Photo", "photo"))
        self.each("Reports_Showing", rep("Reports_Showing", "showing"))
        self.each("Reports_Stickers", rep("Reports_Stickers", "stickers"))
        self.each("Reports_Daily", rep("Reports_Daily", "daily", "author"))
        self.each("Reports_General", rep("Reports_General", "general", "author", "timestamp"))
        self.each("Assigned_Showings", rep("Assigned_Showings", "assigned_showing", "agent"))
        self.each("Photo_Queue", rep("Photo_Queue", "makefoto", "agent"))

    # -------- content

    def content(self):
        db = self.db

        def ticker(r, rec):
            lid = s(rec.get("id")) or key_of("Ticker", r)
            t = db.scalar(select(TickerMessage).where(TickerMessage.legacy_id == lid))
            status = "deleted" if (rec.get("status") or "").strip().lower() == "deleted" else "active"
            if t:
                t.text, t.status = s(rec.get("text")) or t.text, status
                return "updated"
            if not s(rec.get("text")):
                raise ValueError("empty ticker text")
            t = TickerMessage(legacy_id=lid, text=s(rec.get("text")), status=status)
            if dt(rec.get("created")):
                t.created_at = dt(rec.get("created"))
            db.add(t)
        self.each("Ticker_Messages", ticker)

        def news(r, rec):
            mid = s(rec.get("message_id"))
            q = select(News).where(News.message_id == mid) if mid else select(News).where(News.text == s(rec.get("text")))
            if db.scalar(q):
                return "unchanged"
            db.add(News(date=dt(rec.get("date")), text=s(rec.get("text")), message_id=mid))
        self.each("News", news)

        def address(r, rec):
            lid = s(rec.get("id")) or key_of("Addresses", r)
            a = db.scalar(select(Address).where(Address.legacy_id == lid)) or Address(legacy_id=lid)
            new = a.id is None
            a.district, a.address, a.ad_link = s(rec.get("district")), s(rec.get("address")), s(rec.get("ad_link_advertising"))
            a.is_active = boolean(rec.get("is_active")) if s(rec.get("is_active")) else True
            db.add(a)
            return "imported" if new else "updated"
        self.each("Addresses", address)

        def post(sheet):
            def fn(r, rec):
                mid = s(rec.get("message_id"))
                if not mid:
                    raise ValueError("no message_id")
                p = db.scalar(select(ChannelPost).where(ChannelPost.message_id == mid))
                new = p is None
                p = p or ChannelPost(message_id=mid)
                agent = self.user_by_name(rec.get("agent"))
                p.date = dt(rec.get("date")) or p.date
                p.agent_name, p.agent_id = s(rec.get("agent")) or p.agent_name, agent.id if agent else p.agent_id
                p.district, p.price, p.rooms = s(rec.get("district")), num(rec.get("price")), s(rec.get("rooms"))
                p.building, p.link = s(rec.get("building")), s(rec.get("link"))
                p.is_exclusive = boolean(rec.get("exclusive"))
                if "raw" in rec:
                    p.raw = s(rec.get("raw"))
                if sheet == "Active_Listings":
                    st = (rec.get("status") or "").strip().lower()
                    p.status = "archived" if st == "deleted" else "rented" if st == "rented" else "active"
                elif new:
                    p.status = "active"
                db.add(p)
                return "imported" if new else "updated"
            return fn
        self.each("Listings", post("Listings"))
        self.each("Active_Listings", post("Active_Listings"))

        def setting(r, rec):
            k = s(rec.get("key"))
            if not k:
                raise ValueError("empty key")
            key = f"sheets.{k}"
            row = db.get(Setting, key)
            if row:
                row.value = rec.get("value") or ""
                return "updated"
            db.add(Setting(key=key, value=rec.get("value") or ""))
        self.each("Settings", setting)

    def schedule_reminders(self):
        """Imported confirmed deals still ahead of T14 or of the contract end get their worker jobs (idempotent)."""
        db, today = self.db, datetime.now(timezone.utc).date()
        rows = db.scalars(select(Deal).where(Deal.source == "import", Deal.status == "confirmed", or_(
            (Deal.t14_status == "pending") & (Deal.t14_date > today), Deal.rent_to > today)))
        n = 0
        for d in rows:
            deals_svc.schedule_reminders(db, d, past_t14=False)
            n += 1
        db.commit()
        if n:
            self.stats["Deals_Reminders"]["scheduled"] += n

    def run(self) -> dict:
        self.people()
        self.owners()
        self.deals()
        self.evaluations()
        self.reports()
        self.content()
        self.db.commit()
        self.schedule_reminders()
        return {k: {**dict(v), **({"errors": self.errors[k]} if self.errors.get(k) else {})} for k, v in self.stats.items()}


def run(folder: str | Path, db: Session | None = None) -> dict:
    own = db is None
    db = db or SessionLocal()
    try:
        return Importer(db, Path(folder)).run()
    finally:
        if own:
            db.close()


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    folder = Path(argv[0] if argv else "data/legacy-sheets")
    if not folder.is_dir():
        print(f"no such directory: {folder}")
        sys.exit(2)
    stats = run(folder)
    print(f"import from {folder} at {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    total = defaultdict(int)
    for sheet, st in stats.items():
        errs = st.pop("errors", [])
        print(f"  {sheet:22s} " + ", ".join(f"{k}={v}" for k, v in sorted(st.items())))
        for e in errs:
            print(f"  {'':22s}   ! {e}")
        for k, v in st.items():
            total[k] += v
    print("  " + "total".ljust(22) + " " + ", ".join(f"{k}={v}" for k, v in sorted(total.items())))


if __name__ == "__main__":
    main()
