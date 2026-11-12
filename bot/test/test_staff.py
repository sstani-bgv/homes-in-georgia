"""Staff side of the bot on the shared DB: users instead of agents, the panel menu button, the Sheets mirror."""
import asyncio
import os
import unittest

import pg  # noqa: F401
import core
import db
import sheets
from test_core import Base, item


class SheetsTest(Base):
    def test_rows(self):
        self.sync([item("ss1"), item("tg1", "agency", urls=["https://t.me/x/1", "https://ss.ge/2"])],
                  [item("ss9", reasons=["одинаковый текст у разных объявлений"])])
        cl = core.client("tg", 42, username="anna", name="Anna")
        core.add_input(cl, text="2 спальни в Ваке")
        a = core.agent(100, "David", "david", create=True)
        core.agent(101, "Old", None, create=True)
        with db.tx() as c:
            c.execute("INSERT INTO leads (client_id, listing_id, status, agent_id, request, created_at, updated_at) "
                      "VALUES (?, 'ss1', 'agent_assigned', ?, '2 спальни в Ваке', ?, ?)", (cl["id"], a["id"], db.now(), db.now()))
            c.execute("UPDATE users SET is_active = false WHERE tg_id = 101")
        tabs = sheets.build()
        self.assertEqual(list(tabs), ["Объявления", "Заявки", "Клиенты", "Агенты"])
        for rows in tabs.values():
            self.assertTrue(all(len(r) == len(rows[0]) for r in rows))  # rectangular
            self.assertTrue(all(x is not None for r in rows for x in r))  # no None for the API
        lst = {r[0]: dict(zip(tabs["Объявления"][0], r)) for r in tabs["Объявления"][1:]}
        self.assertEqual(lst["ss9"]["Статус"], "На проверке")
        self.assertEqual(lst["tg1"]["Ссылки"], "https://t.me/x/1\nhttps://ss.ge/2")
        lead = dict(zip(tabs["Заявки"][0], tabs["Заявки"][1]))
        self.assertEqual((lead["Статус"], lead["Контакт"], lead["Агент"], lead["Объявление"], lead["Запрос"]),
                         ("У агента", "@anna", "@david", "ss1", "2 спальни в Ваке"))
        client = dict(zip(tabs["Клиенты"][0], tabs["Клиенты"][1]))
        self.assertEqual((client["Username"], client["Заявок"], client["Запрос сейчас"]), ("@anna", 1, "2 спальни в Ваке"))
        agents = {r[1]: dict(zip(tabs["Агенты"][0], r)) for r in tabs["Агенты"][1:]}
        self.assertEqual((agents[100]["Заявок"], agents[100]["Активен"], agents[101]["Активен"]), (1, "да", "нет"))

    def test_not_configured_is_noop(self):
        os.environ.pop("GOOGLE_SA_JSON", None)
        os.environ["SHEET_ID"] = "x"
        self.assertFalse(sheets.safe_sync())
        sheets.sync_later()  # returns at once, no thread, no error
        os.environ.pop("SHEET_ID")


class BotMenuTest(Base):
    def test_menu_button_only_for_staff(self):
        import bot
        calls = []

        class FakeBot:
            async def set_chat_menu_button(self, chat_id, menu_button):
                calls.append((chat_id, menu_button.type, getattr(getattr(menu_button, "web_app", None), "url", None)))

        bot.menus.clear()
        os.environ["PUBLIC_URL"] = ""
        asyncio.run(bot.sync_menu(FakeBot(), 100))
        self.assertEqual(calls, [])  # not configured: quietly nothing
        os.environ["PUBLIC_URL"] = "https://crm.example.com"
        try:
            core.agent(100, "David", "david", create=True)
            core.client("tg", 42)
            for chat in (100, 42, 100, 42):
                asyncio.run(bot.sync_menu(FakeBot(), chat))
            self.assertEqual(calls, [(100, "web_app", "https://crm.example.com/")])  # once; the client untouched
            with db.tx() as c:
                c.execute("UPDATE users SET is_active = false WHERE tg_id = 100")
            asyncio.run(bot.sync_menu(FakeBot(), 100))
            self.assertEqual(calls[-1][:2], (100, "default"))  # disabled in the CRM: loses the panel button
        finally:
            os.environ.pop("PUBLIC_URL", None)
            bot.menus.clear()


class AgentPhonesTest(Base):
    def test_known_agent_phone_marks_listing(self):
        with db.tx() as c:
            c.execute("INSERT INTO owners (name, phone, phone_norm, is_agent) VALUES ('X', '+995 599 12 34 56', '599123456', true)")
        self.sync([item("ss1", text="Сдаю, звоните 599-12-34-56"), item("ss2", text="Собственник 577 00 00 00"),
                   item("tg_x_1", "tg:x", text="599 12 34 56")])
        i = core.listing("ss1")
        self.assertEqual((i["status"], i["agent_suspect"]), ("pending_review", 1))
        self.assertIn("телефон в базе агентов: 599123456", i["agent_reasons"])
        self.assertEqual(core.listing("ss2")["status"], "published")
        self.assertEqual(core.listing("tg_x_1")["status"], "published")  # competitor channels are agencies anyway


if __name__ == "__main__":
    unittest.main()
