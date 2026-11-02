-- TEST-ONLY stand-in for the backend Alembic schema (docs/ARCHITECTURE.md §4), used by bot tests while
-- backend/alembic is missing. Not a migration: the source of truth is backend/alembic.
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS users (
  id SERIAL PRIMARY KEY, tg_id BIGINT UNIQUE, username TEXT, name TEXT NOT NULL, phone TEXT,
  role TEXT NOT NULL CHECK (role IN ('superadmin', 'admin', 'operator', 'agent', 'recruit')),
  level INT, mentor_id INT REFERENCES users(id), districts JSONB NOT NULL DEFAULT '[]', is_active BOOLEAN NOT NULL DEFAULT true,
  login TEXT UNIQUE, password_hash TEXT, photo_url TEXT, birth_date DATE, exclusive_deals INT NOT NULL DEFAULT 0,
  plans JSONB, legacy_id TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), last_login_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS sources (
  id SERIAL PRIMARY KEY, kind TEXT NOT NULL, key TEXT NOT NULL UNIQUE, name TEXT, url TEXT, lang TEXT,
  options TEXT NOT NULL DEFAULT '{}', enabled INTEGER NOT NULL DEFAULT 1, position INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS listings (
  id TEXT PRIMARY KEY, status TEXT NOT NULL, status_reason TEXT, decision TEXT, decided_by INT REFERENCES users(id),
  decided_at TEXT, review_sent_at TEXT, source TEXT NOT NULL, owner_id TEXT,
  urls TEXT NOT NULL DEFAULT '[]', dup_sources TEXT NOT NULL DEFAULT '[]',
  price_usd INTEGER, area REAL, floor INTEGER, bedrooms INTEGER, address TEXT, district TEXT, lat REAL, lng REAL, geo_precision TEXT,
  photos TEXT NOT NULL DEFAULT '[]', description_ru TEXT, description_en TEXT, district_en TEXT,
  pets TEXT, renovation TEXT, style TEXT, light TEXT, furniture TEXT, features TEXT NOT NULL DEFAULT '[]', impression INTEGER, looks TEXT,
  agent_reasons TEXT NOT NULL DEFAULT '[]', agent_suspect INTEGER NOT NULL DEFAULT 0, details TEXT NOT NULL DEFAULT '{}', text TEXT,
  first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS clients (
  id SERIAL PRIMARY KEY, channel TEXT NOT NULL, external_id TEXT NOT NULL, chat_id TEXT, username TEXT, name TEXT, phone TEXT,
  lang TEXT NOT NULL DEFAULT 'ru', state TEXT NOT NULL DEFAULT '{}', profile TEXT NOT NULL DEFAULT '{}',
  profile_done INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE (channel, external_id)
);
CREATE TABLE IF NOT EXISTS reactions (
  client_id INT NOT NULL REFERENCES clients(id), listing_id TEXT NOT NULL REFERENCES listings(id), reaction TEXT NOT NULL,
  created_at TEXT NOT NULL, PRIMARY KEY (client_id, listing_id)
);
CREATE TABLE IF NOT EXISTS owners (
  id SERIAL PRIMARY KEY, name TEXT, phone TEXT, phone_norm TEXT, address TEXT, address_norm TEXT, link TEXT,
  is_agent BOOLEAN NOT NULL DEFAULT false, notes TEXT, created_by INT REFERENCES users(id), created_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS properties (
  id SERIAL PRIMARY KEY, listing_id TEXT UNIQUE REFERENCES listings(id), source_type TEXT, status TEXT NOT NULL DEFAULT 'new',
  owner_id INT REFERENCES owners(id), agent_id INT REFERENCES users(id), address TEXT, district TEXT, price NUMERIC,
  bedrooms INT, area REAL, published_at TIMESTAMPTZ, post_url TEXT, created_at TIMESTAMPTZ DEFAULT now(), updated_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS deals (
  id SERIAL PRIMARY KEY, lead_id INT, property_id INT, agent_id INT REFERENCES users(id), deal_type TEXT DEFAULT 'rent',
  price NUMERIC, address TEXT, client_name TEXT, rent_from DATE, rent_to DATE, status TEXT NOT NULL DEFAULT 'pending',
  t14_status TEXT, agent_amount NUMERIC, created_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS leads (
  id SERIAL PRIMARY KEY, client_id INT NOT NULL REFERENCES clients(id), listing_id TEXT REFERENCES listings(id),
  property_id INT REFERENCES properties(id), status TEXT NOT NULL DEFAULT 'new',
  operator_id INT REFERENCES users(id), agent_id INT REFERENCES users(id), request TEXT,
  sla_stage TEXT, sla_due_at TIMESTAMPTZ, viewing_at TIMESTAMPTZ, contacts_shared_at TIMESTAMPTZ,
  client_outcome TEXT, agent_outcome TEXT, refusal_reason TEXT, client_note TEXT, deal_id INT REFERENCES deals(id),
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, created_ts TIMESTAMPTZ DEFAULT now(), UNIQUE (client_id, listing_id)
);
CREATE TABLE IF NOT EXISTS lead_events (
  id SERIAL PRIMARY KEY, lead_id INT NOT NULL REFERENCES leads(id), status TEXT NOT NULL, agent_id INT REFERENCES users(id),
  actor_id INT REFERENCES users(id), kind TEXT, note TEXT, at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lead_slots (
  id SERIAL PRIMARY KEY, lead_id INT NOT NULL REFERENCES leads(id), starts_at TIMESTAMPTZ NOT NULL, created_by INT REFERENCES users(id),
  chosen BOOLEAN NOT NULL DEFAULT false, created_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS evaluations (
  id SERIAL PRIMARY KEY, deal_id INT REFERENCES deals(id), kind TEXT, author TEXT, h2o_client INT, h2o_owner INT, h2o_process INT,
  result TEXT, changes TEXT, complaints BOOLEAN DEFAULT false, complaints_text TEXT, comment TEXT, created_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS events (
  id SERIAL PRIMARY KEY, entity TEXT, entity_id TEXT, action TEXT, actor_id INT, actor_label TEXT, data JSONB, at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS jobs (
  id SERIAL PRIMARY KEY, kind TEXT NOT NULL, ref TEXT, due_at TIMESTAMPTZ NOT NULL, payload JSONB, done_at TIMESTAMPTZ,
  attempts INT DEFAULT 0, last_error TEXT, created_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS searches (id SERIAL PRIMARY KEY, client_id INT REFERENCES clients(id), results INTEGER NOT NULL DEFAULT 0, at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tg_files (path TEXT PRIMARY KEY, file_id TEXT NOT NULL, size BIGINT);
CREATE TABLE IF NOT EXISTS channel_videos (
  chat TEXT NOT NULL, message_id BIGINT NOT NULL, media_group_id TEXT, file_id TEXT, at TEXT NOT NULL, PRIMARY KEY (chat, message_id)
);
CREATE TABLE IF NOT EXISTS messages (
  kind TEXT NOT NULL, ref TEXT NOT NULL, chat_id BIGINT NOT NULL, message_id BIGINT NOT NULL, PRIMARY KEY (kind, ref, chat_id, message_id)
);
CREATE TABLE IF NOT EXISTS journey_answers (
  id SERIAL PRIMARY KEY, created_at TEXT NOT NULL, name TEXT, payload TEXT NOT NULL, ip_hash TEXT NOT NULL
);
