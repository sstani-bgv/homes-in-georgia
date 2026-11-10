// Types mirror docs/ARCHITECTURE.md §4 / §7. Optional fields are tolerant to backend variations.

export type Role = 'superadmin' | 'admin' | 'operator' | 'agent' | 'recruit';

export interface Page<T> { items: T[]; total: number }

export interface UserRef { id: number; name: string; username?: string | null; photo_url?: string | null; agent_tag?: string | null }

export interface Plans { photo: number; showing: number; stickers: number; rental: number }

export interface User {
  id: number;
  tg_id?: number | null;
  username?: string | null;
  name: string;
  phone?: string | null;
  role: Role;
  level?: number | null;
  mentor_id?: number | null;
  districts: string[];
  is_active: boolean;
  login?: string | null;
  photo_url?: string | null;
  birth_date?: string | null;
  exclusive_deals: number;
  plans?: Plans;
  /** the agent's post hashtag without '#' (Zaza, ArturB), unique */
  agent_tag?: string | null;
  next_level_at?: number | null;
  created_at?: string;
  last_login_at?: string | null;
}

export interface AuthOut { access_token: string; token_type?: string; user: User }

export interface AiFlags {
  owner?: boolean;
  agent_suspect?: boolean;
  duplicate?: boolean;
  reasons?: string[];
}

export interface Listing {
  id: string;
  source?: string | null;
  url?: string | null;
  title?: string | null;
  text?: string | null;
  price?: number | null;
  currency?: string | null;
  district?: string | null;
  address?: string | null;
  bedrooms?: number | null;
  rooms?: number | null;
  area?: number | null;
  photos?: string[];
  ai_flags?: AiFlags | null;
  created_at?: string | null;
  taken_by?: UserRef | null;
  property_id?: number | null;
  status?: string | null;
  /** objects of our base at this address (archived included): for a side-by-side check, not a dup verdict */
  similar?: SimilarFlat[];
  lat?: number | null;
  lng?: number | null;
  /** the previous agent reached the owner, who won't work with the agency */
  refused?: { by?: string | null; at: string; comment?: string | null } | null;
  /** the matcher linked this ad to an object already in the base */
  known_object?: { id: number; address?: string | null } | null;
  /** «похож»: objects an operator should compare with (pending) */
  matches?: ListingMatch[];
  /** active clients whose questionnaire fits this ad (stage 10) */
  demand?: number;
}

export interface ListingMatch { property_id: number; address?: string | null; score: number; reasons: string[] }
/** a live outside ad of an exclusive object (url hidden unless you may see the owner contacts) */
export interface ExternalAd { listing_id: string; source?: string | null; url?: string | null }
export interface Elsewhere extends ExternalAd { verdict: 'linked' | 'same' | 'similar'; reasons: string[] }

export interface SimilarFlat {
  id: number; url?: string | null; address?: string | null; photo?: string | null; price_usd?: number | null; status?: string; archive_reason?: string | null;
  bedrooms?: number | null; area?: number | null; floor?: number | null; distance_m: number;
}

export type PropertyStatus =
  | 'new' | 'taken' | 'call_failed' | 'owner_confirmed' | 'agent_marked' | 'shoot_scheduled'
  | 'media_uploaded' | 'review' | 'returned' | 'published' | 'rented' | 'archived';

/** agent: the agent the owner belongs to (stage 6.5) — the agent of all his objects */
export interface OwnerRef { id: number; name?: string | null; phone?: string | null; is_agent?: boolean; agent?: UserRef | null }

/** The channel post fields of the old generator (backend services/properties.py POST_ONE / POST_MANY / POST_NUM), tags without '#'. */
export interface PostFields {
  deal?: 'Rent' | 'Sale'; prop_type?: string; metro?: string; building?: string; design?: string; condition?: string;
  heating?: string; bathrooms?: string; pets?: string; amenities?: string[]; periods?: string[];
  tenants?: number; deposit?: number; sale_price?: number; headline?: string; discount?: boolean;
}

export interface Media { id: number; kind: 'photo' | 'video'; path: string; url?: string; room?: string | null; position?: number }

export interface Property {
  missing?: string[]; // what still blocks «Отправить оператору» (backend properties.missing_fields)
  id: number;
  listing_id?: string | null;
  source_type?: string | null;
  source_url?: string | null;
  status: PropertyStatus;
  owner?: OwnerRef | null;
  owner_id?: number | null;
  owner_phone?: string | null;
  agent?: UserRef | null;
  agent_id?: number | null;
  taken_at?: string | null;
  call_attempts?: number;
  next_call_at?: string | null;
  shoot_at?: string | null;
  address?: string | null;
  district?: string | null;
  cadastral_code?: string | null;
  price?: number | null;
  currency?: string | null;
  bedrooms?: number | null;
  rooms?: number | null;
  area?: number | null;
  floor?: number | null;
  total_floors?: number | null;
  description?: string | null;
  is_exclusive?: boolean;
  checklist?: Record<string, boolean> | null;
  post_fields?: PostFields | null;
  ai_flags?: AiFlags | null;
  duplicate_of?: number | null;
  dup_check?: 'added' | 'exists' | null;
  review_comment?: string | null;
  reviewed_by?: number | null;
  reviewed_at?: string | null;
  published_at?: string | null;
  post_url?: string | null;
  media?: Media[];
  photos?: string[];
  /** list rows: the first own photo, else the origin ad's */
  photo?: string | null;
  /** list rows: own photos + videos */
  media_count?: number;
  listing?: (Listing & { urls?: string[] }) | null;
  /** full card only: our posts of the object (channel: 'tg' for now) */
  publications?: Publication[];
  /** full card only: the object's ads on other sites; url is null unless you may see the owner contacts */
  listings?: PropertyAd[];
  deals?: PropertyDeal[];
  /** exclusive objects: live outside ads → «Эксклюзив висит на …», publishing as exclusive is blocked */
  exclusive_conflict?: ExternalAd[];
  /** stage 6: the operator's first approve / publish; after it locked_fields go through «Запрос оператору» for the agent */
  verified_at?: string | null;
  /** stage 10: «Собственник согласен показать клиентам» */
  fast_track_at?: string | null;
  locked_fields?: string[];
  /** stage 7: «Новый объект из сделки — проверить» (typed in the deal form, not verified yet) */
  needs_check?: boolean;
  /** full card, staff and the object's agent: pending «Запросы оператору» */
  change_requests?: ChangeRequest[];
  /** PATCH / archive: the request this call created instead of applying (locked fields after verification) */
  change_request?: ChangeRequest | null;
  archive_reason?: string | null;
  archive_comment?: string | null;
  archived_at?: string | null;
  created_at?: string;
  updated_at?: string;
}

export interface ChangeRequest {
  id: number; entity: string; entity_id: number; kind: 'edit' | 'archive' | 'transfer_owner' | 'delete';
  status: 'pending' | 'approved' | 'rejected'; reason?: string | null;
  changes: { field: string; old: unknown; new: unknown }[];
  requested_by: { id: number | null; name: string | null }; decided_by?: { id: number; name: string | null } | null;
  decided_at?: string | null; decision_comment?: string | null; created_at: string;
  property?: { id: number; address?: string | null; status: PropertyStatus } | null;
}

/** GET /properties/:id/post: the channel caption built from the object (services.properties.post_text). */
export interface PostDraft { text: string; hashtags: string[]; channels: string[] }

export interface Publication { id: number; channel: string; status: 'draft' | 'published' | 'removed'; url?: string | null; message_id?: string | null; published_at?: string | null; removed_at?: string | null }
export interface PropertyAd { listing_id: string; source?: string | null; status?: string | null; url?: string | null; price_usd?: number | null; first_seen?: string | null; last_seen?: string | null; origin: boolean; removed_checked_at?: string | null }
export interface PropertyDeal { id: number; status: string; deal_type?: string | null; price?: number | null; agent?: string | null; rent_from?: string | null; rent_to?: string | null; created_at?: string | null }

export interface EventItem {
  id: number | string;
  entity?: string;
  entity_id?: string;
  action: string;
  actor_id?: number | null;
  actor_label?: string | null;
  actor_name?: string | null;
  data?: Record<string, unknown> | null;
  text?: string | null;
  at: string;
}

export interface ClientProfile {
  residents?: string;
  pets?: string[];
  terms?: string[];
  budget_max?: number | null;
  budget_min?: number | null;
  bedrooms?: number[];
  districts?: string[];
  pin?: { lat: number; lng: number } | null;
  radius_km?: number | null;
  move_in?: string | null;
  photos?: number;
}

export interface Client {
  id: number;
  name?: string | null;
  username?: string | null;
  phone?: string | null;
  tg_id?: number | null;
  lang?: string | null;
  profile: ClientProfile;
  profile_done?: number | boolean;
  created_at?: string | null;
  /** list: number of leads; detail: lead cards */
  leads?: number | Lead[];
  reactions?: { listing_id: string; reaction: string; created_at?: string | null; listing?: Listing | null }[];
  channel?: string | null;
  /** stage 8 card: «Сейчас: …», the timeline (newest first) and the refusals */
  status?: string;
  timeline?: ClientEvent[];
  refusals?: ClientRefusal[];
}

export type ClientEventType = 'profile' | 'search' | 'shown' | 'reaction' | 'lead' | 'message' | 'viewing' | 'refusal' | 'deal';
export interface ClientEvent {
  at: string | null;
  type: ClientEventType;
  action?: string | null;
  actor?: string | null;
  text: string;
  links: { listing_id?: string; property_id?: number; lead_id?: number; deal_id?: number };
}
export interface ClientRefusal {
  lead_id: number;
  listing_id?: string | null;
  property_id?: number | null;
  object: string;
  at?: string | null;
  by: 'client' | 'agent' | 'operator';
  by_name: string;
  reason?: string | null;
  note?: string | null;
  stage?: LeadStatus | null;
}
/** stage 10: active clients an object fits (agents get no names / ids) */
export interface Demand {
  count: number;
  active_days: number;
  pushed: number;
  fast_track_at?: string | null;
  can_fast_track: boolean;
  clients: { id?: number; name?: string | null; username?: string | null; lang?: string; profile: string; sent: boolean }[];
}

export type LeadStatus =
  | 'new' | 'operator_taken' | 'agent_assigned' | 'slots_sent' | 'time_chosen' | 'viewing_done'
  | 'deal_pending' | 'rented' | 'refused' | 'thinking';

export interface Slot { id: number; starts_at: string; chosen: boolean }

export interface Lead {
  id: number;
  client_id: number;
  client?: Client | null;
  listing_id?: string | null;
  listing?: Listing | null;
  property_id?: number | null;
  status: LeadStatus;
  operator?: UserRef | null;
  operator_id?: number | null;
  agent?: UserRef | null;
  agent_id?: number | null;
  request?: string | null;
  sla_stage?: 'operator_take' | 'agent_slots' | 'slots_to_client' | null;
  sla_due_at?: string | null;
  sla_window_min?: number | null;
  viewing_at?: string | null;
  contacts_shared_at?: string | null;
  contacts_visible?: boolean;
  owner?: { name?: string | null; phone?: string | null; is_agent?: boolean } | null;
  client_outcome?: string | null;
  agent_outcome?: string | null;
  refusal_reason?: string | null;
  client_note?: string | null;
  archived_at?: string | null;
  deal_id?: number | null;
  created_at?: string | null;
  slots?: Slot[];
  history?: EventItem[];
}

/** GET /leads/{id}/contract: ContractWizard prefill (data keys = CxData). */
export interface LeadContract {
  lead_id: number;
  property_id?: number | null;
  deal_id?: number | null;
  type: 'rent' | 'comm' | 'sale';
  data: Record<string, string>;
  district?: string | null;
  client: { name?: string | null; phone?: string | null; username?: string | null };
  owner: { name?: string | null; phone?: string | null };
}

export interface RecommendedAgent {
  agent: UserRef & { districts?: string[]; level?: number | null };
  score: number;
  reasons: string[];
  load?: number;
  recommended?: boolean;
}

export type DealStatus = 'pending' | 'confirmed' | 'cancelled';

export interface Deal {
  id: number;
  lead_id?: number | null;
  property_id?: number | null;
  agent_id: number;
  agent?: UserRef | null;
  recruit_id?: number | null;
  recruit?: UserRef | null;
  deal_type: 'rent' | 'sale';
  sub_type?: string | null;
  is_exclusive: boolean;
  is_rerented?: boolean;
  price: number;
  commission_pct?: number | null;
  commission_total?: number | null;
  platform_amount?: number | null;
  agent_amount?: number | null;
  recruit_pct?: number | null;
  recruit_amount?: number | null;
  client_src?: string | null;
  client_src_amount?: number | null;
  agent_level?: number | null;
  agent_deal_num?: number | null;
  cadastral?: string | null;
  ad_link?: string | null;
  address?: string | null;
  metro?: string | null;
  client_name?: string | null;
  client_phone?: string | null;
  client_tg?: string | null;
  owner_name?: string | null;
  owner_phone?: string | null;
  rent_from?: string | null;
  rent_to?: string | null;
  status: DealStatus;
  confirmed_at?: string | null;
  t14_date?: string | null;
  t14_status?: string | null;
  month?: string | null;
  source?: string | null;
  created_at?: string;
  days_left?: number | null;
  /** GET /deals/:id: the deal's object (stage 7) */
  property?: { id: number; address?: string | null; status: PropertyStatus; needs_check: boolean } | null;
  /** POST: «Объекта нет в базе» → the object is created from the form's fields */
  new_object?: boolean;
}

/** GET /properties/deal-search: the deal form's object picker; another agent's object comes `blocked`, without fields. */
export interface DealPick {
  id: number; address?: string | null; district?: string | null; status: PropertyStatus; bedrooms?: number | null;
  agent_tag?: string | null; needs_check: boolean; blocked?: string | null;
  fields: { address?: string; cadastral?: string; metro?: string; ad_link?: string; owner_name?: string; owner_phone?: string; is_exclusive?: boolean; price?: number } | null;
}

export interface DealCalcIn {
  price: number;
  commission_pct?: number;
  commission_total?: number | null;
  agent_level: number;
  is_exclusive?: boolean;
  recruit_pct?: number;
  client_src?: string | null;
  deal_type?: string;
  is_rerented?: boolean;
}

export interface DealCalcOut {
  commission_total: number;
  platform_amount: number;
  agent_amount: number;
  recruit_amount: number;
  client_src_amount: number;
}

export interface Evaluation {
  id: number;
  deal_id: number;
  kind: 't0' | 't14';
  author: 'agent' | 'client';
  h2o_client?: number | null;
  h2o_owner?: number | null;
  h2o_process?: number | null;
  result?: 'clean' | 'dirty' | null;
  changes?: string | null;
  complaints?: boolean;
  complaints_text?: string | null;
  comment?: string | null;
  created_at?: string;
}

export type ReportKind = 'photo' | 'showing' | 'stickers' | 'daily' | 'general' | 'makefoto';

export interface Report {
  id: number;
  kind: ReportKind | string;
  author_id?: number | null;
  author?: UserRef | null;
  recruit_id?: number | null;
  recruit?: UserRef | null;
  mentor_id?: number | null;
  date?: string | null;
  data: Record<string, unknown>;
  created_at?: string;
  dup_check?: 'added' | 'exists' | null;
}

export interface CounterPair { done: number; plan: number }
export interface CounterRow {
  user_id: number;
  name: string;
  role?: Role | string;
  mentor_id?: number | null;
  photo: CounterPair;
  showing: CounterPair;
  stickers: CounterPair;
  rental: CounterPair;
}

export interface Owner {
  id: number;
  name?: string | null;
  phone?: string | null;
  address?: string | null;
  link?: string | null;
  is_agent?: boolean;
  notes?: string | null;
  agent_id?: number | null;
  created_at?: string | null;
  properties_count?: number;
  properties?: Property[];
}

export interface TickerMessage { id: number; text: string; status?: string; created_at?: string }
export interface NewsItem { id: number; date?: string; text: string }

/** GET /userbot: the operator's Telegram account (the userbot process) and its QR login in progress. */
export interface UserbotStatus {
  configured: boolean; reachable?: boolean; enabled?: boolean; authorized?: boolean;
  me?: { id: number; username: string | null; name: string } | null;
  login?: { status: 'idle' | 'starting' | 'qr' | 'password' | 'done' | 'expired' | 'error'; url?: string; qr_svg?: string; error?: string | null };
}

/** GET /sources/schedule: how often the parser runs a source; paid ones: $ per run + $ per hour of new posts (measured). */
export interface SourceSchedule {
  key: string; label: string; every_min: number;
  paid: { per_run: number; per_hour: number | null; spent_7d: number; runs_7d: number; posts_7d: number } | null;
}

export interface Source { id: number | string; name: string; kind?: string | null; url?: string | null; enabled: boolean; last_run_at?: string | null; count?: number }

export interface AgentStat {
  id: number;
  name: string;
  photo_url?: string | null;
  role?: Role;
  level?: number | null;
  exclusive_deals: number;
  deals_month?: number;
  deals_total?: number;
  next_level_at?: number | null;
  active_leads?: number;
  active_properties?: number;
  mentor_id?: number | null;
}

export interface Dashboard {
  deals_count?: number;
  deals_month?: number;
  platform_commission?: number;
  active_objects?: number;
  reposts?: number;
  leads_open?: number;
  sla_overdue?: number;
  top_agents?: AgentStat[];
  telegram_top?: { name: string; value: number }[];
  ticker?: TickerMessage[];
  funnel?: { properties?: Record<string, number>; leads?: Record<string, number> };
  metrics?: Record<string, boolean>;
  [key: string]: unknown;
}

export interface Cabinet {
  user?: User;
  month?: string;
  counters?: Partial<Record<'photo' | 'showing' | 'stickers' | 'rental', CounterPair>>;
  level?: { level: number; exclusive_deals: number; next_level_at?: number | null; to_next?: number | null };
  deals?: Deal[];
  properties?: Property[];
  leads?: Lead[];
  viewings?: Lead[];
  expiring?: Deal[];
}

export type Settings = Record<string, unknown>;

export interface AppConfig { bot_username?: string; public_url?: string; mock?: boolean }

export interface WeeklyListings { total: number; week_start: string; by_agent: Record<string, number>; top_agents: { name: string; count: number }[] }
/** GET /api/analytics/listings */
export interface ListingsStats {
  listings: { total: number; avg_price: number; by_district: { district: string; count: number; avg_price: number; min_price: number; max_price: number }[]; by_agent: Record<string, number>; by_rooms: Record<string, number> };
  active: { total: number; avg_price: number; excl_pct: number; by_rooms: Record<string, number>; top_agents: { name: string; count: number }[] };
  weekly: { current: WeeklyListings; last_week: WeeklyListings; week_start: string };
}
