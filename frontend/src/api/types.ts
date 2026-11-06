// Types mirror docs/ARCHITECTURE.md §4 / §7. Optional fields are tolerant to backend variations.

export type Role = 'superadmin' | 'admin' | 'operator' | 'agent' | 'recruit';

export interface Page<T> { items: T[]; total: number }

export interface UserRef { id: number; name: string; username?: string | null; photo_url?: string | null }

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
  /** agency-channel flats in the same building with close parameters: for a side-by-side check, not a dup verdict */
  similar?: SimilarFlat[];
  /** the previous agent reached the owner, who won't work with the agency */
  refused?: { by?: string | null; at: string; comment?: string | null } | null;
}

export interface SimilarFlat {
  id: string; url?: string | null; address?: string | null; photo?: string | null; price_usd?: number | null;
  bedrooms?: number | null; area?: number | null; floor?: number | null; distance_m: number;
}

export type PropertyStatus =
  | 'new' | 'taken' | 'call_failed' | 'owner_confirmed' | 'agent_marked' | 'shoot_scheduled'
  | 'media_uploaded' | 'review' | 'returned' | 'published' | 'rented' | 'archived';

export interface OwnerRef { id: number; name?: string | null; phone?: string | null; is_agent?: boolean }

export interface Media { id: number; kind: 'photo' | 'video'; path: string; url?: string; room?: string | null; position?: number }

export interface Property {
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
  created_at?: string;
  updated_at?: string;
}

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
  deal_id?: number | null;
  created_at?: string | null;
  slots?: Slot[];
  history?: EventItem[];
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
  created_at?: string | null;
  properties_count?: number;
  properties?: Property[];
}

export interface TickerMessage { id: number; text: string; status?: string; created_at?: string }
export interface NewsItem { id: number; date?: string; text: string }

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

/** GET /api/channel-posts — agency listings parsed from the channel / work-group topic. */
export interface ChannelPost {
  id: number; message_id?: number | null; source?: string | null; date?: string | null; agent_name?: string | null; agent_id?: number | null;
  district?: string | null; price?: number | null; rooms?: string | null; building?: string | null; is_exclusive?: boolean | null;
  link?: string | null; status?: string | null; raw?: string | null;
}
export interface WeeklyListings { total: number; week_start: string; by_agent: Record<string, number>; top_agents: { name: string; count: number }[] }
/** GET /api/analytics/listings */
export interface ListingsStats {
  listings: { total: number; avg_price: number; by_district: { district: string; count: number; avg_price: number; min_price: number; max_price: number }[]; by_agent: Record<string, number>; by_rooms: Record<string, number> };
  active: { total: number; avg_price: number; excl_pct: number; by_rooms: Record<string, number>; top_agents: { name: string; count: number }[] };
  weekly: { current: WeeklyListings; last_week: WeeklyListings; week_start: string };
}
