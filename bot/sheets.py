"""One-way mirror of the DB into a Google Spreadsheet: tabs Объявления, Заявки, Клиенты, Агенты (full rewrite).

Setup (.env): GOOGLE_SA_JSON=path/to/service-account.json, SHEET_ID=<id from the sheet URL>.
The sheet must be shared (Editor) with the service account's client_email. Not configured -> no-op with a log line.

CLI:  python sheets.py sync      # rewrite every tab now
      python sheets.py preview   # print the first rows of each tab, no network
"""
import json
import os
import sys
import threading

import db

LEAD_STATUS = {"new": "Новая", "operator_taken": "У оператора", "agent_assigned": "У агента", "slots_sent": "Время отправлено",
               "time_chosen": "Просмотр назначен", "viewing_done": "Просмотр прошёл", "deal_pending": "Сделка на проверке",
               "rented": "Сдана", "refused": "Отказ", "thinking": "Думает", "in_work": "В работе", "viewing": "Просмотр назначен"}
LISTING_STATUS = {"published": "Опубликовано", "pending_review": "На проверке", "rejected": "Отклонено", "gone": "Снято"}


def _v(x):
    return "" if x is None else x


def _table(header, rows):
    return [header] + [[_v(x) for x in r] for r in rows]


def listings_rows(c):
    rows = c.execute("SELECT * FROM listings ORDER BY first_seen DESC, id").fetchall()
    return _table(
        ["ID", "Статус", "Причина", "Решение", "Источник", "Район", "Адрес", "Цена $", "Спальни", "м²", "Этаж",
         "Ссылки", "Дубли", "Описание", "Впервые", "Обновлено"],
        [[r["id"], LISTING_STATUS.get(r["status"], r["status"]), r["status_reason"], r["decision"], r["source"],
          r["district"], r["address"], r["price_usd"], r["bedrooms"], r["area"], r["floor"],
          "\n".join(json.loads(r["urls"])), ", ".join(json.loads(r["dup_sources"])), r["description_ru"],
          r["first_seen"], r["updated_at"]] for r in rows])


def leads_rows(c):
    rows = c.execute(
        "SELECT l.*, c.channel, c.external_id, c.username AS cu, c.name AS cn, c.phone, "
        "s.source, s.district, s.price_usd, s.urls, a.name AS an, a.username AS au FROM leads l "
        "JOIN clients c ON c.id = l.client_id JOIN listings s ON s.id = l.listing_id "
        "LEFT JOIN users a ON a.id = l.agent_id ORDER BY l.id DESC").fetchall()
    return _table(
        ["№", "Создана", "Статус", "Клиент", "Контакт", "Запрос", "Объявление", "Источник", "Район", "Цена $",
         "Ссылки", "Агент", "Обновлена"],
        [[r["id"], r["created_at"], LEAD_STATUS.get(r["status"], r["status"]), r["cn"],
          f"@{r['cu']}" if r["cu"] else r["phone"] or f"{r['channel']}:{r['external_id']}", r["request"],
          r["listing_id"], r["source"], r["district"], r["price_usd"], "\n".join(json.loads(r["urls"])),
          f"@{r['au']}" if r["au"] else r["an"], r["updated_at"]] for r in rows])


def clients_rows(c):
    rows = c.execute(
        "SELECT c.*, (SELECT COUNT(*) FROM leads WHERE client_id = c.id) AS nleads, "
        "(SELECT COUNT(*) FROM searches WHERE client_id = c.id) AS nsearch FROM clients c ORDER BY c.id DESC").fetchall()
    return _table(
        ["ID", "Канал", "Username", "Имя", "Телефон", "Язык", "Запрос сейчас", "Поисков", "Заявок", "Создан", "Активность"],
        [[r["id"], r["channel"], r["username"] and f"@{r['username']}", r["name"], r["phone"], r["lang"],
          " / ".join(json.loads(r["state"]).get("msgs", [])[-3:]), r["nsearch"], r["nleads"], r["created_at"],
          r["updated_at"]] for r in rows])


def agents_rows(c):
    rows = c.execute("SELECT a.*, (SELECT COUNT(*) FROM leads WHERE agent_id = a.id) AS nleads FROM users a "
                     "WHERE a.role != 'superadmin' ORDER BY a.id").fetchall()
    return _table(
        ["ID", "Telegram ID", "Username", "Имя", "Роль", "Активен", "Заявок", "Добавлен"],
        [[r["id"], r["tg_id"], r["username"] and f"@{r['username']}", r["name"], r["role"], "да" if r["is_active"] else "нет",
          r["nleads"], r["created_at"].isoformat(timespec="seconds") if r["created_at"] else ""] for r in rows])


TABS = {"Объявления": listings_rows, "Заявки": leads_rows, "Клиенты": clients_rows, "Агенты": agents_rows}


def build(path=None):
    """{tab title: rows (header first)} from the DB. Pure: no network."""
    with db.tx(path) as c:
        return {title: fn(c) for title, fn in TABS.items()}


def configured():
    db.load_env()
    sa, sheet = os.environ.get("GOOGLE_SA_JSON"), os.environ.get("SHEET_ID")
    return (sa, sheet) if sa and sheet and os.path.exists(sa) else None


def sync():
    cfg = configured()
    if not cfg:
        print("sheets: not configured (GOOGLE_SA_JSON file and SHEET_ID in .env), skipping")
        return False
    import gspread
    book = gspread.service_account(filename=cfg[0]).open_by_key(cfg[1])
    have = {ws.title: ws for ws in book.worksheets()}
    data = build()
    for title, rows in data.items():
        ws = have.get(title) or book.add_worksheet(title, rows=len(rows) + 10, cols=len(rows[0]))
        ws.clear()
        ws.update(values=rows, range_name="A1")
        ws.freeze(rows=1)
    print("sheets: synced", {t: len(r) - 1 for t, r in data.items()})
    return True


def safe_sync():
    """Best effort: Sheets being down or misconfigured must never break the bot, the admin or refresh."""
    try:
        return sync()
    except Exception as e:
        print("sheets: sync failed:", repr(e))
        return False


_running = threading.Lock()
_dirty = threading.Event()


def sync_later():
    """Non-blocking sync after a lead change: one background worker, repeated while new changes arrive."""
    if not configured():
        return
    _dirty.set()
    if not _running.acquire(blocking=False):
        return  # the running worker will pick the change up

    def work():
        try:
            while _dirty.is_set():
                _dirty.clear()
                safe_sync()
        finally:
            _running.release()
    threading.Thread(target=work, daemon=True).start()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "sync"
    db.load_env()
    db.init()
    if cmd == "preview":
        for title, rows in build().items():
            print(f"== {title}: {len(rows) - 1} rows")
            for r in rows[:3]:
                print(" | ".join(str(x)[:30] for x in r))
    else:
        sys.exit(0 if safe_sync() or not configured() else 1)
