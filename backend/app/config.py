"""All configuration comes from the environment / repo-root .env (docs/ARCHITECTURE.md §2)."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(REPO_ROOT / ".env"), extra="ignore", env_file_encoding="utf-8")

    DATABASE_URL: str = "postgresql+psycopg://homes:homes@localhost:5432/homes"
    TEST_DATABASE_URL: str = ""
    SECRET_KEY: str = "dev-secret-change-me"
    JWT_TTL_HOURS: int = 72
    PUBLIC_URL: str = "http://localhost:8000"
    BOT_TOKEN: str = ""
    BOT_USERNAME: str = "HomesinGeorgia_bot"
    LOGIN_BOT_TOKEN: str = ""  # CRM sign-in bot token; empty = BOT_TOKEN
    LOGIN_BOT_USERNAME: str = ""  # CRM sign-in bot. Made with Claude (Anthropic), Homes in Georgia, Irakli Kvirkvelia
    OPERATOR_TG_USERNAME: str = ""      # after ❤️ the client writes here (t.me/<it>?text=…); empty = the first operator's @username
    # operator userbot (app/telegram/userbot.py): the operator's own account; all three set = enabled
    OPERATOR_USERBOT_SESSION: str = ""  # Telethon StringSession, created by the operator (docs in userbot.py)
    API_ID: str = ""
    API_HASH: str = ""
    USERBOT_URL: str = ""               # where the CRM/bot reach the userbot, e.g. http://userbot:8091; empty = no slots to clients
    TG_API_BASE: str = "https://api.telegram.org"
    OPERATORS_CHAT_ID: str = ""
    OPERATORS_TOPIC_ID: str = ""
    WORK_GROUP_CHAT_ID: str = ""
    TOPIC_PHOTO: str = ""
    TOPIC_SHOWING: str = ""
    TOPIC_STICKERS: str = ""
    TOPIC_DEAL: str = ""
    TOPIC_T14: str = ""
    TOPIC_NEWS: str = ""
    TOPIC_LISTINGS: str = ""
    TOPIC_GENERAL: str = ""
    TOPIC_DAILY: str = ""
    AGENCY_CHANNEL: str = "rent_tbilisi_ge"
    # inbound Telegram (services/inbound.py, port of the Apps Script pollUpdates handlers)
    AGENCY_CHANNEL_ID: str = ""         # numeric id of the agency channel (else matched by @AGENCY_CHANNEL username)
    DEALS_CHANNEL_ID: str = ""          # chat where #Rented posts appear (old RENT_CHANNEL_ID); empty = agency channel
    NEWS_CHAT_ID: str = ""              # chat of the news topic (old NEWS_CHAT_ID); empty = WORK_GROUP_CHAT_ID
    TOPIC_DEAL_EXTRA: str = ""          # more #Rented topics in the work group, comma-separated (old DEAL_TOPICS)
    TOPIC_PHOTO_QUEUE: str = ""         # Make-Foto template topics, comma-separated (old TEMPLATE_TOPICS)
    PUBLISH_CHAT_ID: str = ""
    PUBLISH_TOPIC_ORIGINALS: str = ""
    PUBLISH_TOPIC_OWNER: str = ""
    PUBLISH_TOPIC_ALBUM: str = ""
    SUPERADMIN_TG_IDS: str = ""
    ADMIN_BOOTSTRAP_LOGIN: str = ""
    ADMIN_BOOTSTRAP_PASSWORD: str = ""
    LLM_PROVIDER: str = "openai"
    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = ""
    LLM_MODEL: str = ""
    APIFY_TOKEN: str = ""
    GOOGLE_SA_JSON: str = ""
    SHEET_ID: str = ""
    TZ: str = "Asia/Tbilisi"
    SLA_OPERATOR_TAKE_MIN: int = 5
    SLA_AGENT_SLOTS_MIN: int = 30
    SLA_SLOTS_TO_CLIENT_MIN: int = 60
    CLIENT_PROMISE_MIN: int = 30
    CALL_RETRY_HOURS: float = 2
    VIEWING_REMINDER_MIN: int = 60
    POST_VIEWING_SURVEY_MIN: int = 60
    T14_DAYS: int = 14
    CONTRACT_REMIND_DAYS: str = "30,7,1"
    MEDIA_DIR: str = str(REPO_ROOT / "data" / "media")
    MEDIA_STORAGE: str = "local"        # local: files under MEDIA_DIR | s3: S3-compatible bucket (app/storage.py)
    S3_ENDPOINT: str = ""               # e.g. https://<account>.r2.cloudflarestorage.com, http://localhost:9000 (MinIO)
    S3_BUCKET: str = ""
    S3_ACCESS_KEY: str = ""
    S3_SECRET_KEY: str = ""
    S3_REGION: str = "us-east-1"        # R2/MinIO/AWS accept us-east-1; Hetzner: fsn1/nbg1/hel1
    S3_PUBLIC_URL: str = ""             # optional public base URL of the bucket (CDN/R2 domain); else presigned URLs
    DEV_CORS: bool = False
    APP_ENV: str = "prod"  # dev: allows a weak SECRET_KEY

    @property
    def superadmin_tg_ids(self) -> list[int]:
        return [int(x) for x in self.SUPERADMIN_TG_IDS.replace(" ", "").split(",") if x.strip().lstrip("-").isdigit()]

    @property
    def contract_remind_days(self) -> list[int]:
        return [int(x) for x in self.CONTRACT_REMIND_DAYS.split(",") if x.strip().isdigit()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
