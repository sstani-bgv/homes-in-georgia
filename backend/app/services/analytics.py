"""Dashboard metrics (apps-script getRentalStats / updateAnalyticsCache, now straight from Postgres)."""
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import FIELD, ChannelPost, Deal, Event, Lead, Property, User
from app.services.common import TZ, now
from app.services.reports import month_bounds

LIVE = ("confirmed", "pending")


def _f(v) -> float:
    return float(v or 0)


def deals_summary(db: Session, month: str | None = None) -> dict:
    stmt = select(Deal).where(Deal.status.in_(LIVE))
    if month:
        stmt = stmt.where(Deal.month == month)
    rows = list(db.scalars(stmt))
    total = len(rows)
    comm = sum(_f(d.commission_total) for d in rows)
    plat = sum(_f(d.platform_amount) for d in rows)
    return {"total": total, "confirmed": sum(d.status == "confirmed" for d in rows),
            "exclusive": sum(bool(d.is_exclusive) for d in rows), "commission": round(comm), "platform": round(plat),
            "agents": round(sum(_f(d.agent_amount) for d in rows)), "price": round(sum(_f(d.price) for d in rows)),
            "avg_commission": round(comm / total) if total else 0}


def monthly(db: Session, months: int = 12) -> list[dict]:
    rows = db.execute(select(Deal.month, func.count(), func.sum(Deal.commission_total), func.sum(Deal.platform_amount),
                             func.sum(Deal.price)).where(Deal.status.in_(LIVE), Deal.month.isnot(None))
                      .group_by(Deal.month)).all()

    def key(m):
        try:
            mm, yy = m.split("/")
            return int(yy) * 12 + int(mm)
        except ValueError:
            return 0
    rows = sorted(rows, key=lambda r: key(r[0]))[-months:]
    return [{"month": m, "deals": n, "commission": round(_f(c)), "platform": round(_f(p)), "price": round(_f(pr))}
            for m, n, c, p, pr in rows]


def leaderboard(db: Session, month: str | None = None, limit: int = 15) -> list[dict]:
    stmt = (select(User.id, User.name, User.role, User.level, User.exclusive_deals, func.count(Deal.id),
                   func.coalesce(func.sum(Deal.agent_amount), 0), func.coalesce(func.sum(Deal.platform_amount), 0))
            .join(Deal, Deal.agent_id == User.id).where(Deal.status.in_(LIVE)))
    if month:
        stmt = stmt.where(Deal.month == month)
    rows = db.execute(stmt.group_by(User.id).order_by(func.count(Deal.id).desc()).limit(limit)).all()
    return [{"id": i, "name": n, "role": r, "level": lv, "exclusive_deals": ex, "deals": c, "agent_amount": round(_f(a)),
             "platform_amount": round(_f(p))} for i, n, r, lv, ex, c, a, p in rows]


def _avg_minutes(db: Session, start: str, end: str, since: datetime, group_by_actor: bool = False):
    """Average minutes between two lead events (e.g. created → operator_taken), optionally per actor of `end`."""
    a = select(Event.entity_id, func.min(Event.at).label("t")).where(Event.entity == "lead", Event.action == start,
                                                                   Event.at >= since).group_by(Event.entity_id).subquery()
    b = select(Event.entity_id, Event.actor_id, func.min(Event.at).label("t")).where(
        Event.entity == "lead", Event.action == end).group_by(Event.entity_id, Event.actor_id).subquery()
    diff = func.avg(func.extract("epoch", b.c.t - a.c.t) / 60)
    if group_by_actor:
        rows = db.execute(select(b.c.actor_id, diff, func.count()).join(a, a.c.entity_id == b.c.entity_id)
                          .group_by(b.c.actor_id)).all()
        return {aid: {"avg_min": round(float(m or 0), 1), "n": n} for aid, m, n in rows}
    m = db.scalar(select(diff).join(a, a.c.entity_id == b.c.entity_id))
    return round(float(m), 1) if m is not None else None


def response_times(db: Session, days: int = 30) -> dict:
    since = now() - timedelta(days=days)
    names = dict(db.execute(select(User.id, User.name)).all())
    per_op = _avg_minutes(db, "created", "operator_taken", since, True)
    per_agent = _avg_minutes(db, "agent_assigned", "slots_sent", since, True)
    return {"operator_take_avg_min": _avg_minutes(db, "created", "operator_taken", since),
            "agent_slots_avg_min": _avg_minutes(db, "agent_assigned", "slots_sent", since),
            "operators": [{"id": k, "name": names.get(k), **v} for k, v in per_op.items() if k],
            "agents": [{"id": k, "name": names.get(k), **v} for k, v in per_agent.items() if k]}


def funnel(db: Session, date_from: date | None = None, date_to: date | None = None) -> dict:
    lq = select(Lead.status, func.count()).group_by(Lead.status)
    pq = select(Property.status, func.count()).group_by(Property.status)
    if date_from:
        lq = lq.where(Lead.created_ts >= datetime.combine(date_from, datetime.min.time(), TZ))
        pq = pq.where(Property.created_at >= datetime.combine(date_from, datetime.min.time(), TZ))
    if date_to:
        end = datetime.combine(date_to + timedelta(days=1), datetime.min.time(), TZ)
        lq, pq = lq.where(Lead.created_ts < end), pq.where(Property.created_at < end)
    leads = dict(db.execute(lq).all())
    props = dict(db.execute(pq).all())
    order = ["new", "operator_taken", "agent_assigned", "slots_sent", "time_chosen", "viewing_done", "thinking",
             "deal_pending", "rented", "refused"]
    reached = {}
    for i, st in enumerate(order[:-1]):  # leads that reached at least this stage (refused counted separately)
        reached[st] = sum(n for s, n in leads.items() if s in order[i:-1])
    return {"leads": leads, "leads_reached": reached, "leads_total": sum(leads.values()),
            "properties": props, "properties_total": sum(props.values())}


def _avg(xs: list[float]) -> int:
    return round(sum(xs) / len(xs)) if xs else 0


def listings_stats(db: Session) -> dict:
    """apps-script updateAnalyticsCache: Listings (all parsed posts) by district/agent/rooms with prices, and
    Active_Listings (status active) totals, exclusive share, by rooms/agent."""
    rows = db.execute(select(ChannelPost.district, ChannelPost.price, ChannelPost.rooms, ChannelPost.agent_name,
                             ChannelPost.status, ChannelPost.is_exclusive)).all()
    by_d: dict[str, list[float]] = {}
    counts, by_a, by_r, prices = {}, {}, {}, []
    for d, p, r, a, _, _ in rows:
        p = _f(p)
        if d:
            counts[d] = counts.get(d, 0) + 1
            by_d.setdefault(d, [])
            if p:
                by_d[d].append(p)
        if a:
            by_a[a] = by_a.get(a, 0) + 1
        if r:
            by_r[r] = by_r.get(r, 0) + 1
        if p:
            prices.append(p)
    districts = sorted(({"district": d, "count": counts[d], "avg_price": _avg(ps), "min_price": round(min(ps)) if ps else 0,
                         "max_price": round(max(ps)) if ps else 0} for d, ps in by_d.items()), key=lambda x: -x["count"])
    act = [(a, _f(p), r, ex) for _, p, r, a, st, ex in rows if st == "active" and a]
    act_agents, act_rooms = {}, {}
    for a, _, r, _ in act:
        act_agents[a] = act_agents.get(a, 0) + 1
        if r:
            act_rooms[r] = act_rooms.get(r, 0) + 1
    return {
        "listings": {"total": len(rows), "avg_price": _avg(prices), "by_district": districts, "by_agent": by_a, "by_rooms": by_r},
        "active": {"total": len(act), "avg_price": _avg([p for _, p, _, _ in act if p]),
                   "excl_pct": round(sum(1 for *_, ex in act if ex) / len(act) * 100) if act else 0, "by_rooms": act_rooms,
                   "top_agents": [{"name": n, "count": c} for n, c in sorted(act_agents.items(), key=lambda x: -x[1])]},
    }


def weekly_listings(db: Session, at: datetime | None = None) -> dict:
    """apps-script getWeeklyListingsStats: listings posted per agent this week vs last week; weeks start Monday 12:00
    Tbilisi time."""
    t = (at or now()).astimezone(TZ)
    mon = (t - timedelta(days=t.weekday())).replace(hour=12, minute=0, second=0, microsecond=0)
    if t < mon:
        mon -= timedelta(days=7)
    last, nxt = mon - timedelta(days=7), mon + timedelta(days=7)

    def build(a: datetime, b: datetime) -> dict:
        rows = db.execute(select(ChannelPost.agent_name, func.count()).where(
            ChannelPost.date >= a, ChannelPost.date < b, ChannelPost.agent_name.isnot(None), ChannelPost.agent_name != "")
            .group_by(ChannelPost.agent_name)).all()
        by = dict(rows)
        return {"total": sum(by.values()), "week_start": a.strftime("%d.%m"), "by_agent": by,
                "top_agents": [{"name": n, "count": c} for n, c in sorted(by.items(), key=lambda x: -x[1])]}
    return {"current": build(mon, nxt), "last_week": build(last, mon), "week_start": mon.strftime("%d.%m")}


def dashboard(db: Session, month: str | None = None) -> dict:
    y, m = month_bounds(month)
    mo = f"{m}/{y}"
    active_objects = db.scalar(select(func.count()).select_from(Property).where(Property.status == "published")) or 0
    channel_active = db.scalar(select(func.count()).select_from(ChannelPost).where(ChannelPost.status == "active")) or 0
    open_leads = db.scalar(select(func.count()).select_from(Lead).where(Lead.status.not_in(("rented", "refused")))) or 0
    overdue = db.scalar(select(func.count()).select_from(Lead).where(Lead.sla_due_at < now())) or 0
    last = db.scalars(select(Deal).where(Deal.status.in_(LIVE)).order_by(Deal.created_at.desc()).limit(10))
    names = dict(db.execute(select(User.id, User.name).where(User.role.in_(FIELD + ("admin", "superadmin", "operator")))).all())
    return {
        "month": mo, "deals_month": deals_summary(db, mo), "deals_all": deals_summary(db),
        "active_objects": active_objects, "channel_active_listings": channel_active, "open_leads": open_leads,
        "sla_overdue": overdue, "monthly": monthly(db), "listings": listings_stats(db), "weekly_listings": weekly_listings(db), "leaderboard": leaderboard(db, mo),
        "leaderboard_all": leaderboard(db), "funnel": funnel(db), "response_times": response_times(db),
        "last_deals": [{"id": d.id, "agent": names.get(d.agent_id), "price": _f(d.price), "commission": _f(d.commission_total),
                        "platform": _f(d.platform_amount), "exclusive": d.is_exclusive, "status": d.status,
                        "date": d.created_at.astimezone(TZ).strftime("%d.%m.%y")} for d in last],
    }


def agents(db: Session, month: str | None = None) -> list[dict]:
    from app.services import reports, users
    y, m = month_bounds(month)
    mo = f"{m}/{y}"
    board = {r["id"]: r for r in leaderboard(db, mo, limit=1000)}
    counters = {r["user"]["id"]: r for r in reports.counters(db, mo)}
    lead_load = dict(db.execute(select(Lead.agent_id, func.count()).where(Lead.status.not_in(("rented", "refused")))
                                .group_by(Lead.agent_id)).all())
    props = dict(db.execute(select(Property.agent_id, func.count()).where(Property.status.not_in(("archived",)))
                            .group_by(Property.agent_id)).all())
    out = []
    # «Команда» shows only working agents (Irakli 2026-11-09): a deactivated one stays in the history, not on the board
    for u in db.scalars(select(User).where(User.role.in_(FIELD), User.is_active.is_(True)).order_by(User.name)):
        out.append({"id": u.id, "name": u.name, "username": u.username, "role": u.role, "is_active": u.is_active,
                    "mentor_id": u.mentor_id, **users.level_info(u), "deals_month": board.get(u.id, {}).get("deals", 0),
                    "agent_amount_month": board.get(u.id, {}).get("agent_amount", 0), "open_leads": lead_load.get(u.id, 0),
                    "properties": props.get(u.id, 0), "counters": counters.get(u.id)})
    return out
