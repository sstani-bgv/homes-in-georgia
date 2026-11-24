"""«Посуточная аренда» on autopilot (Irakli 11-23): the manager should not have to type what the platforms already know.

- auto_stays: every Airbnb reservation (an iCal event «Reserved») becomes a stay by itself — dates, platform, the
  confirmation code; the money comes later (gross empty = «без суммы»). The event's dates move → the stay follows; the
  event vanishes (cancelled on Airbnb) → the stay is removed while it has no money, otherwise it is only flagged.
  Booking's feed says «CLOSED - Not available» for a reservation and for a closed date alike, so it creates nothing.
- today: the «Сегодня» screen — who lives now, arrivals and departures today, the next arrivals, stays without money,
  today's cleanings, the payouts ready, the next 30 days' occupancy, alerts (free gaps, a hidden apartment that is still
  booked, a cancelled reservation that kept its money).
- digest: the same as a Telegram message for the Daily Rent managers every morning (worker, DIGEST_HOUR).
- auto_cleanings: a cleaning for every check-out of the day (cleaner = the stay's assigned staff), so it is never forgotten.
- owner_reports: on the 1st, every apartment's report for the past month (services/daily_rent.period_report) goes to the
  managers to forward to the owner.
Every write is audited (events dr_*, actor label «авто»)."""
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DrApartment, DrBlock, DrBooking, DrCleaning, Setting
from app.services import daily_rent as R
from app.services import daily_rent_ical as C
from app.services.common import log_event, now

AUTO = "airbnb_ical"
LABEL = "авто"
LOOKBACK_DAYS = 60  # reservations that ended earlier are history: no stay is created for them
GAP_DAYS, GAP_MIN = 14, 3  # «свободно N ночей подряд» in the next two weeks
HOUR_RATE = 16.70  # ApartFlow's cleaning rate, ₾ per hour
DIGEST_HOUR = 9
DIGEST_KEY, REPORTS_KEY = "daily_rent_digest_sent", "daily_rent_owner_reports_sent"


def _setting(db: Session, key: str) -> str | None:
    row = db.get(Setting, key)
    return row.value if row else None


def _set(db: Session, key: str, value: str):
    row = db.get(Setting, key)
    if row:
        row.value = value
    else:
        db.add(Setting(key=key, value=value))


def is_auto(b: DrBooking) -> bool:
    return (b.extra or {}).get("auto") == AUTO


def no_money(b: DrBooking) -> bool:
    return not float(b.gross or 0)


# ---------------------------------------------------------------- Airbnb reservation → stay

def auto_stays(db: Session) -> dict:
    """Create / move / remove the stays of Airbnb's reservations. Idempotent: run after every calendar sync."""
    since = R.today() - timedelta(days=LOOKBACK_DAYS)
    apts = {a.id: a for a in db.scalars(select(DrApartment).where(DrApartment.deleted_at.is_(None)))}
    blocks = list(db.scalars(select(DrBlock).where(DrBlock.deleted_at.is_(None), DrBlock.source == "airbnb", DrBlock.reserved.is_(True),
                                                   DrBlock.status == "active", DrBlock.end >= since)))
    stays = list(db.scalars(select(DrBooking).where(DrBooking.deleted_at.is_(None), DrBooking.check_in.is_not(None),
                                                    DrBooking.check_out.is_not(None)).execution_options(populate_existing=True)))
    by_uid = {(b.extra or {}).get("block_uid"): b for b in stays if is_auto(b)}
    res = {"created": 0, "moved": 0, "removed": 0, "flagged": 0}
    live_uids = set()
    for k in blocks:
        if k.apartment_id not in apts:
            continue
        live_uids.add(k.external_uid)
        mine = by_uid.get(k.external_uid)
        if mine is not None:
            if (mine.check_in, mine.check_out) != (k.start, k.end) and no_money(mine):
                R.update(db, DrBooking, mine.id, {"check_in": k.start, "check_out": k.end}, None, commit=False, label=LABEL)
                res["moved"] += 1
            continue
        # an Airbnb stay already entered by hand for these nights = the same reservation
        if any(s.apartment_id == k.apartment_id and C.platform_key(s.platform) == "airbnb" and s.check_in < k.end and k.start < s.check_out
               for s in stays):
            continue
        a = apts[k.apartment_id]
        b = R.create(db, DrBooking, {**R.new_booking_defaults(a, "Airbnb"), "apartment_id": a.id, "check_in": k.start, "check_out": k.end,
                                     "payment_status": "pending", "comment": "Создано автоматически из брони Airbnb"
                                     + (f" · код {k.reason}" if k.reason else "")}, None, commit=False, label=LABEL)
        b.extra = {"auto": AUTO, "block_uid": k.external_uid, "code": k.reason}
        stays.append(b)
        res["created"] += 1
    for uid, b in by_uid.items():
        if uid in live_uids or b.check_out < since:
            continue
        if no_money(b) and not b.paid_out:  # cancelled on Airbnb before any money: nothing to keep
            b.deleted_at = now()
            log_event(db, "dr_booking", b.id, "delete", None, label=LABEL, reason="бронь отменена на Airbnb", before=R.out(b))
            res["removed"] += 1
        elif not (b.extra or {}).get("cancelled_on_platform"):
            b.extra = {**(b.extra or {}), "cancelled_on_platform": True}
            log_event(db, "dr_booking", b.id, "flag", None, label=LABEL, reason="бронь отменена на Airbnb, а деньги внесены")
            res["flagged"] += 1
    db.commit()
    return res


# ---------------------------------------------------------------- «Сегодня»

def _brief(b: DrBooking, apts: dict) -> dict:
    a = apts.get(b.apartment_id)
    return {"id": b.id, "apartment_id": b.apartment_id, "apartment": a.name if a else "—", "guest": b.guest_name or "",
            "platform": b.platform, "check_in": b.check_in.isoformat(), "check_out": b.check_out.isoformat(), "nights": R.nights(b),
            "no_money": no_money(b), "auto": is_auto(b), "code": (b.extra or {}).get("code"),
            "cancelled_on_platform": bool((b.extra or {}).get("cancelled_on_platform"))}


def _busy_nights(items: list[dict], start: date, days: int) -> set[date]:
    """Nights of [start, start+days) taken by a stay, a reserved / closed platform date or a manual closure (not echoes)."""
    out = set()
    end = start + timedelta(days=days)
    for x in items:
        s, e = max(date.fromisoformat(x["start"]), start), min(date.fromisoformat(x["end"]), end)
        while s < e:
            out.add(s)
            s += timedelta(days=1)
    return out


def _gaps(free: list[date]) -> list[tuple[date, date]]:
    runs, cur = [], []
    for d in sorted(free):
        if cur and d != cur[-1] + timedelta(days=1):
            runs.append(cur)
            cur = []
        cur.append(d)
    if cur:
        runs.append(cur)
    return [(r[0], r[-1] + timedelta(days=1)) for r in runs if len(r) >= GAP_MIN]


def today(db: Session) -> dict:
    t = R.today()
    apts = {a.id: a for a in db.scalars(select(DrApartment).where(DrApartment.deleted_at.is_(None)).order_by(DrApartment.id))}
    stays = [b for b in db.scalars(select(DrBooking).where(DrBooking.deleted_at.is_(None), DrBooking.check_in.is_not(None),
                                                         DrBooking.check_out.is_not(None)).order_by(DrBooking.check_in))
             if b.apartment_id in apts and b.check_out > b.check_in]
    brief = lambda b: _brief(b, apts)  # noqa: E731
    week = t + timedelta(days=7)
    occ = C.mark_echoes(C.occupancy(db))
    per_apt: dict[int, list[dict]] = {}
    for x in occ:
        if not x.get("echo"):
            per_apt.setdefault(x["apartment_id"], []).append(x)
    load, alerts = [], []
    for a in apts.values():
        busy30 = _busy_nights(per_apt.get(a.id, []), t, 30)
        if a.active:
            load.append({"apartment_id": a.id, "apartment": a.name, "busy": len(busy30), "occupancy": round(len(busy30) / 30 * 100)})
            busy = _busy_nights(per_apt.get(a.id, []), t, GAP_DAYS)
            free = [t + timedelta(days=i) for i in range(GAP_DAYS) if t + timedelta(days=i) not in busy]
            for s, e in _gaps(free):
                alerts.append({"kind": "gap", "apartment_id": a.id,
                               "text": f"{a.name}: свободно {(e - s).days} ноч. подряд, {s:%d.%m}–{e:%d.%m}"})
        elif any(b.apartment_id == a.id and b.check_out > t for b in stays) or \
                any(x["kind"] == "block" and x["origin"] in C.CHANNELS and x["reserved"] and x["end"] > t.isoformat()
                    for x in per_apt.get(a.id, [])):
            alerts.append({"kind": "hidden_booked", "apartment_id": a.id,
                           "text": f"{a.name} скрыта (неактивна), но у неё есть будущие брони"})
    for b in stays:
        if (b.extra or {}).get("cancelled_on_platform"):
            alerts.append({"kind": "cancelled_with_money", "apartment_id": b.apartment_id,
                           "text": f"{apts[b.apartment_id].name}: бронь {b.check_in:%d.%m}–{b.check_out:%d.%m} отменена на Airbnb, "
                                   "а деньги по ней внесены — проверьте"})
    cleanings = [{"id": c.id, "apartment_id": c.apartment_id, "apartment": apts[c.apartment_id].name, "cleaner": c.cleaner or "",
                  "notes": c.notes or "", "done": bool(c.hours)}
                 for c in db.scalars(select(DrCleaning).where(DrCleaning.deleted_at.is_(None), DrCleaning.date == t))
                 if c.apartment_id in apts]
    ready = {}
    for p in R.payouts(db):
        for cur, v in p["ready"].items():
            ready[cur] = R.r2(ready.get(cur, 0) + v)
    return {
        "date": t.isoformat(),
        "living": [brief(b) for b in stays if b.check_in <= t < b.check_out],
        "arrivals": [brief(b) for b in stays if b.check_in == t],
        "departures": [brief(b) for b in stays if b.check_out == t],
        "upcoming": [brief(b) for b in stays if t < b.check_in <= week],
        "no_money": [brief(b) for b in stays if no_money(b) and b.check_in <= week and b.check_out >= t - timedelta(days=LOOKBACK_DAYS)],
        "cleanings": cleanings,
        "payouts_ready": ready,
        "load": load,
        "alerts": alerts,
    }


def _line(x: dict, what: str = "") -> str:
    who = x["guest"] or (f"гость Airbnb {x['code']}" if x.get("code") else f"гость {x['platform']}")
    return f"• {x['apartment']} — {who}{what}"


def digest_text(v: dict) -> str:
    d = date.fromisoformat(v["date"])
    out = [f"☀️ <b>Посуточная аренда — {d.day} {R.MONTHS[d.month - 1]}</b>"]

    def block(title, rows, fmt):
        out.append(f"\n<b>{title}</b>")
        out.extend(fmt(x) for x in rows) if rows else out.append("— нет")

    block("Заезжают сегодня", v["arrivals"], lambda x: _line(x, f", до {date.fromisoformat(x['check_out']):%d.%m} ({R.nights_label(x['nights'])})"))
    block("Выезжают сегодня", v["departures"], _line)
    block("Уборки сегодня", v["cleanings"], lambda c: f"• {c['apartment']}" + (f" — {c['cleaner']}" if c["cleaner"] else " — уборщик не назначен"))
    out.append(f"\n<b>Живут сейчас:</b> {len(v['living'])}")
    if v["upcoming"]:
        block("Заезды на неделе", v["upcoming"], lambda x: _line(x, f", {date.fromisoformat(x['check_in']):%d.%m}"))
    if v["no_money"]:
        block("Без суммы — внесите деньги", v["no_money"],
              lambda x: _line(x, f", {date.fromisoformat(x['check_in']):%d.%m}–{date.fromisoformat(x['check_out']):%d.%m}"))
    if v["payouts_ready"]:
        out.append("\n<b>Можно выплатить собственникам:</b> " + R.mix_str(v["payouts_ready"]))
    if v["alerts"]:
        block("Обратите внимание", v["alerts"], lambda a: "• " + a["text"])
    return "\n".join(out)


def digest_due(db: Session) -> bool:
    """Once a day, from DIGEST_HOUR (Tbilisi)."""
    return now().astimezone(R.TZ).hour >= DIGEST_HOUR and _setting(db, DIGEST_KEY) != R.today().isoformat()


def mark_digest(db: Session):
    _set(db, DIGEST_KEY, R.today().isoformat())
    db.commit()


# ---------------------------------------------------------------- cleanings for today's check-outs

def auto_cleanings(db: Session, day: date | None = None) -> int:
    t = day or R.today()
    apts = {a.id: a for a in db.scalars(select(DrApartment).where(DrApartment.deleted_at.is_(None)))}
    have = {c.apartment_id for c in db.scalars(select(DrCleaning).where(DrCleaning.deleted_at.is_(None), DrCleaning.date == t))}
    n = 0
    for b in db.scalars(select(DrBooking).where(DrBooking.deleted_at.is_(None), DrBooking.check_out == t).order_by(DrBooking.id)):
        if b.apartment_id not in apts or b.apartment_id in have:
            continue
        nxt = db.scalar(select(DrBooking.check_in).where(DrBooking.deleted_at.is_(None), DrBooking.apartment_id == b.apartment_id,
                                                         DrBooking.check_in >= t, DrBooking.id != b.id).order_by(DrBooking.check_in).limit(1))
        note = f"Авто: выезд {b.guest_name or 'гостя'}" + (f"; следующий заезд {nxt:%d.%m}" if nxt else "")
        R.create(db, DrCleaning, {"apartment_id": b.apartment_id, "date": t, "cleaner": b.assigned_staff, "hourly_rate": HOUR_RATE,
                                  "notes": note}, None, commit=False, label=LABEL)
        have.add(b.apartment_id)
        n += 1
    db.commit()
    return n


# ---------------------------------------------------------------- owner reports for the past month

def owner_reports(db: Session, month: str | None = None) -> list[dict]:
    """[{apartment, owner_contact, text}] for every apartment with stays checked in during `month` (default: the past one)."""
    if not month:
        first = R.today().replace(day=1)
        prev = first - timedelta(days=1)
        month = f"{prev.year}-{prev.month:02d}"
    rows = R.bookings(db, None, month, month)
    out = []
    for a in db.scalars(select(DrApartment).where(DrApartment.deleted_at.is_(None)).order_by(DrApartment.id)):
        mine = [b for b in rows if b["apartment_id"] == a.id]
        if mine:
            lang = a.report_lang or "ru"  # in the owner's language (the apartment card → «Язык отчётов»)
            out.append({"apartment_id": a.id, "apartment": a.name, "owner_contact": a.owner_contact, "lang": lang,
                        "text": R.period_report(a, mine, R.period_label(month, month, lang), lang)})
    return out


def reports_due(db: Session) -> str | None:
    """The past month, once, on the 1st (from DIGEST_HOUR) or later if the worker was down then. The very first run only
    remembers the past month: the reports start with the next 1st, not on the day of the deploy."""
    first = R.today().replace(day=1)
    prev = first - timedelta(days=1)
    month = f"{prev.year}-{prev.month:02d}"
    seen = _setting(db, REPORTS_KEY)
    if seen is None:
        mark_reports(db, month)
        return None
    return month if seen != month else None


def mark_reports(db: Session, month: str):
    _set(db, REPORTS_KEY, month)
    db.commit()
