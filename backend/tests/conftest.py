"""Tests run against TEST_DATABASE_URL (schema built by `alembic upgrade head`, tables truncated between tests)."""
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
sys.path.insert(0, str(BACKEND))


def _env_file_value(key):
    f = REPO / ".env"
    if f.exists():
        for line in f.read_text().splitlines():
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip()
    return None


TEST_URL = os.environ.get("TEST_DATABASE_URL") or _env_file_value("TEST_DATABASE_URL")
if not TEST_URL:
    raise RuntimeError("TEST_DATABASE_URL is not set")
os.environ["DATABASE_URL"] = TEST_URL
os.environ["BOT_TOKEN"] = "123456:TEST-TOKEN"
os.environ["SECRET_KEY"] = "test-secret-key-that-is-long-enough-for-hs256-ok"
os.environ["ADMIN_BOOTSTRAP_LOGIN"] = ""
os.environ["PUBLISH_CHAT_ID"] = "-100555"

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.db import SessionLocal, engine  # noqa: E402
from app.models import Base, Client, Listing, User  # noqa: E402
from app.services import users as users_svc  # noqa: E402
from app.services.common import jdump, now_text  # noqa: E402
from app.telegram import notify  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def schema():
    with engine.begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", TEST_URL.replace("%", "%%"))
    command.upgrade(cfg, "head")
    with engine.connect() as c:
        defaults = dict(c.execute(text("SELECT key, value FROM settings")).all())
    yield defaults


@pytest.fixture(autouse=True)
def clean(schema, tmp_path, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "MEDIA_DIR", str(tmp_path / "media"))
    yield
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    with engine.begin() as c:
        c.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
        for k, v in schema.items():
            c.execute(text("INSERT INTO settings (key, value) VALUES (:k, :v)"), {"k": k, "v": v})


@pytest.fixture
def db():
    s = SessionLocal()
    yield s
    s.close()


class Sent(list):
    def names(self):
        return [n for n, _ in self]

    def of(self, name):
        return [a for n, a in self if n == name]


@pytest.fixture(autouse=True)
def sent(monkeypatch):
    """Record every notify.* call instead of talking to Telegram."""
    rec = Sent()
    for name in [n for n in dir(notify) if not n.startswith("_") and callable(getattr(notify, n))]:
        if name in ("log", "logging"):
            continue
        def fn(*args, _n=name):
            rec.append((_n, args))
            return (777, "https://t.me/rent_tbilisi_ge/777") if _n == "publish_post" else None
        monkeypatch.setattr(notify, name, fn)
    return rec


def make_user(db, role="agent", name=None, **kw):
    u = User(name=name or f"{role} {db.query(User).count() + 1}", role=role, **kw)
    if kw.get("password_hash") is None and kw.get("login"):
        u.password_hash = users_svc.hash_password("pw")
    db.add(u)
    db.commit()
    return u


def make_client(db, name="Анна", **kw):
    t = now_text()
    c = Client(channel="tg", external_id=str(kw.pop("external_id", 5550001)), chat_id="5550001", username="anna_demo",
               name=name, phone="+995 555 000 111", lang="ru", state="{}",
               profile=jdump({"residents": "2", "budget_max": 900, "districts": ["Ваке"]}), created_at=t, updated_at=t, **kw)
    db.add(c)
    db.commit()
    return c


def make_listing(db, lid="ss1001", **kw):
    t = now_text()
    data = dict(id=lid, status="published", source="ss", urls=jdump([f"https://ss.ge/{lid}"]), price_usd=850, bedrooms=2,
                area=70.0, floor=4, address="ул. Палиашвили 10", district="Ваке", photos="[]",
                text="Сдаю квартиру, собственник, +995 555 12 34 56", first_seen=t, last_seen=t, updated_at=t)
    data.update(kw)
    x = Listing(**data)
    db.add(x)
    db.commit()
    return x


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        yield c


def auth(user):
    from app.api.deps import create_token
    return {"Authorization": f"Bearer {create_token(user)}"}
