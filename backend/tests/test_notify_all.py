"""David 11-23 + the 11-23 inventory (chat-watch CALL-2026-11-23 §G): every key action notifies the object's / lead's
agent and the operator, never the one who pressed it; owner_transferred goes out after the commit."""
from datetime import datetime, timezone

from app.db import engine
from app.models import Contract, ContractSigner, Lead, Owner, Property, PropertyListing
from app.services import change_requests, contracts as C, deals, esign, leads, post_sync, users
from app.services import properties as P
from app.services.common import now_text
from app.telegram import notify
from tests.conftest import make_client, make_listing, make_user

REAL_STAFF_EVENT = notify.staff_event  # captured before the autouse `sent` stubs it


def heads(sent, name):
    """[(head, actor, agents, ops, note)] of prop_event / lead_event / deal_event calls, defaults filled in."""
    return [tuple((list(a) + [None, None, True, None][len(a) - 2:])[1:6]) for a in sent.of(name)]


def _prop(db, agent, status="published", **kw):
    kw.setdefault("published_at", datetime.now(timezone.utc) if status == "published" else None)
    p = Property(status=status, agent_id=agent.id, address="Абашидзе 10", source_type="manual", bedrooms=2, price=900, **kw)
    db.add(p)
    db.commit()
    return p


def _lead(db, agent, status="time_chosen"):
    lead = Lead(client_id=make_client(db, external_id=7000 + agent.id).id, listing_id=make_listing(db, f"nl{agent.id}").id,
                agent_id=agent.id, status=status, created_at=now_text(), updated_at=now_text())
    db.add(lead)
    db.commit()
    return lead


# ---------- the dispatcher itself: who gets the message

def test_staff_event_reaches_agents_operator_and_the_one_who_pressed(db, monkeypatch):
    got = []
    people = {1: {"tg_id": 101}, 2: {"tg_id": 102}, 3: {"tg_id": 103, "is_active": False}, 9: {"tg_id": 900}}
    monkeypatch.setattr(notify.tg, "base", lambda: "x")
    monkeypatch.setattr(notify.tg, "send", lambda chat, text, markup=None, thread_id=None: got.append(str(chat)) or {"ok": 1})
    monkeypatch.setattr(notify.store, "user", lambda uid: people.get(uid))
    monkeypatch.setattr(notify, "operators_targets", lambda: [("900", None)])
    REAL_STAFF_EVENT("x", "property", 5, [1, 2, 2, 3, None])
    assert got == ["101", "102", "900"]  # each once, an inactive agent skipped, then the operator account
    got.clear()
    REAL_STAFF_EVENT("x", "property", 5, [1, 2], 2)  # agent 2 pressed it: a confirmation to him too (David 11-24)
    assert got == ["101", "102", "900"]
    got.clear()
    REAL_STAFF_EVENT("x", "property", 5, [1], 9)  # the operator (= the connected account) pressed it: once
    assert got == ["101", "900"]
    got.clear()
    REAL_STAFF_EVENT("x", "lead", 5, [1], None, False)
    assert got == ["101"]


# ---------- objects (David's list + §G)

def test_review_return_publish(db, sent, monkeypatch):
    monkeypatch.setattr(P, "require_min_photos", lambda db, p: None)
    ag, op = make_user(db, "agent"), make_user(db, "operator")
    p = _prop(db, ag, status="review")
    P.review(db, p.id, op, False, "мало фото")
    assert ("↩️ <b>Возвращён на доработку</b>", op.id, [], True, "мало фото") in heads(sent, "prop_event")
    assert sent.of("property_returned")  # the agent's own message with the comment

    q = _prop(db, ag, status="review")
    sent.clear()
    P.publish(db, q.id, op, tg=False, site=True)  # approved without the channel: the agent hears of it too
    assert heads(sent, "prop_event") == [("✅ <b>Одобрен и опубликован</b> (сайт)", op.id, None, True, None)]

    r = _prop(db, ag, status="review")
    sent.clear()
    P.publish(db, r.id, op)
    assert sent.of("property_published")  # the agent: the post link
    assert heads(sent, "prop_event") == [("✅ <b>Одобрен и опубликован</b> (канал + сайт)", op.id, [], True, None)]


def test_unpublish_pause_archive_back_to_work(db, sent):
    ag, op = make_user(db, "agent"), make_user(db, "operator")
    p = _prop(db, ag)
    P.unpublish(db, p.id, ag)
    assert heads(sent, "prop_event")[-1][:2] == ("🚫 <b>Снят с публикации</b>", ag.id)
    sent.clear()
    P.resume(db, p.id, op)
    assert heads(sent, "prop_event") == [("▶️ <b>Снова опубликован</b>", op.id, None, True, None)]
    sent.clear()
    P.archive(db, p.id, op, "paused")
    assert heads(sent, "prop_event")[-1][:3] == ("⏸ <b>Приостановлен</b>", op.id, None)
    q = _prop(db, ag, status="taken")
    sent.clear()
    P.archive(db, q.id, op, "not_actual")
    assert heads(sent, "prop_event") == [("🗄 <b>В архив: Неактуально</b>", op.id, None, True, None)]
    sent.clear()
    P.resume(db, q.id, op)
    assert heads(sent, "prop_event") == [("🔁 <b>Снова в работу</b>", op.id, None, True, None)]


def test_owner_signed_exclusive_by_link_and_ad_removed(db, sent, monkeypatch):
    ag, op = make_user(db, "agent"), make_user(db, "operator")
    p = _prop(db, ag, status="taken", is_exclusive=True)
    monkeypatch.setattr(P, "_check_owner_doc", lambda *a: None)
    monkeypatch.setattr("app.services.registry.extract_code", lambda b: None)
    P.sign_contract(db, P.sign_token(p.id), {"full_name": "Нана", "personal_id": "01", "phone": "599", "consent": True},
                    b"%PDF-1.4", None, None, {P.OWNER_ID_DOC: ("id.jpg", b"\xff\xd8"), P.REGISTRY_EXTRACT: ("e.pdf", b"%PDF")})
    assert heads(sent, "prop_event") == [("✍️ <b>Собственник подписал эксклюзивный договор</b>", None, None, True, "Нана")]

    make_listing(db, "ss7001")
    db.add(PropertyListing(property_id=p.id, listing_id="ss7001", source="ss.ge", url="https://ss.ge/1"))
    db.commit()
    sent.clear()
    P.ad_removed(db, p.id, "ss7001", op)
    assert heads(sent, "prop_event") == [("✅ <b>Проверено: объявление на ss.ge снято</b>", op.id, None, False, "https://ss.ge/1")]


def test_post_deleted_in_the_channel(db, sent, monkeypatch):
    ag = make_user(db, "agent")
    p = _prop(db, ag, post_url="https://t.me/rent_tbilisi_ge/55")
    monkeypatch.setattr(post_sync, "alive", lambda channel, msgs: set())
    monkeypatch.setattr("app.services.catalog.sync_all", lambda db: None)
    assert post_sync.run(db, pause=0) == 1
    assert heads(sent, "prop_event") == [("🗑 <b>Пост удалён в канале</b>", None, None, True, "Объект в архиве: Сдал сам.")]


# ---------- owner transfer (bug: before the commit) and the request

def test_owner_transfer_notifies_after_commit_both_agents(db, sent, monkeypatch):
    old, new, op, op2 = make_user(db, "agent"), make_user(db, "agent"), make_user(db, "operator"), make_user(db, "operator")
    o = Owner(name="Нана", phone="+995599000777", agent_id=old.id)
    db.add(o)
    db.commit()
    p = _prop(db, old, status="taken", owner_id=o.id)
    cr = change_requests.request_transfer(db, p.id, new.id, old, "уезжаю")
    assert heads(sent, "prop_event")[-1][1:4] == (old.id, [new.id], False)  # the new agent hears of the request
    seen_committed = []

    def committed(*a):  # what another connection sees when the message goes out
        with engine.connect() as c:
            seen_committed.append(c.exec_driver_sql(f"SELECT agent_id FROM owners WHERE id = {o.id}").scalar())
    monkeypatch.setattr(notify, "owner_transferred", committed)
    sent.clear()
    change_requests.decide(db, cr.id, op2, True)
    assert seen_committed == [new.id]  # sent once the transfer is in the database
    assert [a[0] for a in sent.of("owner_transferred_away")] == [old.id]


def test_owner_transfer_rolled_back_sends_nothing(db, sent):
    old, new, op = make_user(db, "agent"), make_user(db, "agent"), make_user(db, "operator")
    o = Owner(name="Нана", phone="+995599000778", agent_id=old.id)
    db.add(o)
    db.commit()
    P.transfer_owner(db, o.id, new.id, op, commit=False)
    db.rollback()
    db.commit()
    assert not sent.of("owner_transferred") and not sent.of("owner_transferred_away")


# ---------- leads

def test_lead_outcomes_reassign_cancel(db, sent):
    ag, ag2, op = make_user(db, "agent"), make_user(db, "agent"), make_user(db, "operator")
    lead = _lead(db, ag, "viewing_done")
    leads.set_outcome(db, lead.id, "client", "thinking")
    assert heads(sent, "lead_event")[-1] == ("🤔 <b>Думает</b> (клиент)", None, None, True, None)  # agent + operator
    leads.set_outcome(db, lead.id, "agent", "refused", "price", ag)
    assert heads(sent, "lead_event")[-1] == ("❌ <b>Не подошло</b> (агент)", ag.id, None, True, "Причина: цена")

    lead2 = _lead(db, ag2, "agent_assigned")
    sent.clear()
    leads.reassign(db, lead2.id, ag.id, op)
    assert heads(sent, "lead_event") == [(f"🔀 <b>Заявку передали агенту {ag.name}</b>", op.id, [ag2.id], False, None)]
    sent.clear()
    leads.cancel(db, lead2.id, op, "клиент пропал")
    assert heads(sent, "lead_event") == [("🚫 <b>Заявка отменена</b>", op.id, None, False, "клиент пропал")]


# ---------- contracts

def test_contract_signed_by_hand_and_closed(db, sent):
    ag, op = make_user(db, "agent"), make_user(db, "operator")
    lead = _lead(db, ag, "contract")
    c = Contract(lead_id=lead.id, kind="rent", lang="RU", html="<p>x</p>", pdf_path="private/x.pdf", status="draft",
                 created_by=ag.id, generated_at=datetime.now(timezone.utc), updated_at=datetime(2020, 1, 1, tzinfo=timezone.utc))
    db.add(c)
    db.commit()
    C.sign(db, c.id, ag)
    assert [(a[1], a[2]) for a in sent.of("contract_event")] == [("✍️ <b>Договор подписан</b> (вручную)", ag.id)]
    c2 = Contract(lead_id=lead.id, kind="rent", lang="RU", html="<p>x</p>", status="draft", created_by=ag.id)
    db.add(c2)
    db.commit()
    sent.clear()
    C.close(db, c2.id, ag, "client_changed_mind")
    assert [a[1:] for a in sent.of("contract_event")] == [("❌ <b>Договор закрыт без сделки: Клиент передумал</b>", ag.id, True, None)]


def test_contract_esign_one_side_then_both(db, sent, monkeypatch):
    ag = make_user(db, "agent")
    c = Contract(kind="rent", lang="RU", html="<p>x</p>", status="draft", created_by=ag.id)
    db.add(c)
    db.flush()
    s1 = ContractSigner(contract_id=c.id, role="owner", token="t-own-0123456789abcdef", doc_sha256=esign.doc_hash(c))
    s2 = ContractSigner(contract_id=c.id, role="tenant", token="t-ten-0123456789abcdef", doc_sha256=esign.doc_hash(c))
    db.add_all([s1, s2])
    db.commit()
    monkeypatch.setattr(esign, "_png", lambda sig: b"")
    monkeypatch.setattr(esign, "view", lambda db, token: {})
    monkeypatch.setattr(esign, "finalize", lambda db, cid: esign._staff(db.get(Contract, cid),
                                                                        "✅ <b>Договор подписан электронно обеими сторонами</b>"))
    body = {"full_name": "Нана", "personal_id": "01", "phone": "599", "consent": True, "doc_sha256": s1.doc_sha256, "signature": "x"}
    esign.sign(db, s1.token, body, None, None)
    first = sent.of("contract_event")
    assert len(first) == 1 and "подписал(а) электронно" in first[0][1] and first[0][2] == ag.id  # the author has his own
    esign.sign(db, s2.token, body, None, None)
    assert sent.of("contract_event")[-1][1] == "✅ <b>Договор подписан электронно обеими сторонами</b>"


# ---------- deals

def test_deal_for_the_agent_confirm_recruit_level_cancel(db, sent):
    ag, rec, op = make_user(db, "agent", exclusive_deals=10), make_user(db, "recruit"), make_user(db, "operator")
    d = deals.create(db, {"agent_id": ag.id, "recruit_id": rec.id, "price": 900, "commission_total": 450, "is_exclusive": True}, op)
    assert heads(sent, "deal_event")[-1][:4] == (f"🤝 <b>{op.name} внёс сделку за вас</b>", op.id, None, False)
    sent.clear()
    deals.confirm(db, d.id, op)
    assert sent.of("deal_confirmed")
    assert heads(sent, "deal_event") == [("✅ <b>Сделка подтверждена</b>", op.id, [rec.id], True, None)]
    assert sent.of("level_changed") == [(ag.id, 70)]  # the 11th exclusive deal
    sent.clear()
    deals.cancel(db, d.id, op, "сорвалась")
    assert heads(sent, "deal_event") == [("🚫 <b>Сделка отменена</b>", op.id, [ag.id, rec.id], True, "сорвалась")]
    assert sent.of("level_changed") == [(ag.id, 50)]


def test_rented_post_deal_goes_to_the_operator(db, sent):
    from app.services import inbound
    msg = {"message_id": 5, "chat_id": -100, "date": 1763900000,
           "text": "#Rented\nPrice: 900$\nCommission: 450$\nAgent: #Agent\nClient: Анна +995599000111"}
    r = inbound.save_deal(db, msg, "group")
    assert r and sent.of("deal_created") and sent.of("deal_created")[0][0].id == r["deal"]


def test_admin_changes_level(db, sent):
    ag, admin = make_user(db, "agent"), make_user(db, "admin")
    users.update(db, ag.id, {"level": 90}, admin, full_profile=False)
    assert sent.of("level_changed") == [(ag.id, 90)]
