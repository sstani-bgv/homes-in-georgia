// Adapters: accept both the §7 contract shapes (used by the mock) and the actual backend shapes
// (price_usd, nested sla, actor label, leaderboard, token, ...). Everything the UI reads goes through here.
import type {
  AgentStat, AuthOut, Cabinet, Client, CounterRow, Dashboard, EventItem, Lead, Listing, Page, Property, RecommendedAgent, Report,
} from './types';

/* eslint-disable @typescript-eslint/no-explicit-any */
type Any = any;

const ACTION_TEXT: Record<string, string> = {
  contacts_shared: 'Бот передал контакты агенту',
};

export function normPage<T>(d: Page<T> | T[] | Any, fn: (x: Any) => T): Page<T> {
  if (!d) return { items: [], total: 0 };
  if (Array.isArray(d)) return { items: d.map(fn), total: d.length };
  const items = (d.items || []).map(fn);
  return { items, total: d.total ?? items.length };
}

export function normAuth(x: Any): AuthOut {
  return { ...x, access_token: x.access_token ?? x.token };
}

export function normListing(x: Any): Listing {
  if (!x) return x;
  let photos: string[] = [];
  if (Array.isArray(x.photos)) photos = x.photos;
  else if (x.photo) photos = [x.photo];
  const price = x.price ?? x.price_usd ?? null;
  const beds = x.bedrooms;
  const title = x.title ?? [x.district, beds !== null && beds !== undefined ? `${beds} ${beds === 1 ? 'спальня' : 'спальни'}` : null, price ? `$${price}` : null].filter(Boolean).join(' · ');
  return {
    ...x, photos, price, currency: x.currency || 'USD', title,
    created_at: x.created_at ?? x.first_seen ?? null,
    url: x.url ?? (Array.isArray(x.urls) ? x.urls[0] : null),
    text: x.text ?? x.description ?? null,
  };
}

export function normEvent(e: Any): EventItem {
  const label = e.actor_label ?? (typeof e.actor === 'string' ? e.actor : null);
  const data = e.data || {};
  return {
    ...e,
    actor_label: label,
    actor_name: e.actor_name ?? data.actor_name ?? (label && !['bot', 'client', 'system'].includes(label) ? label : null),
    text: e.text ?? data.text ?? ACTION_TEXT[e.action] ?? null,
  };
}

export function normLead(l: Any): Lead {
  if (!l) return l;
  const sla = l.sla || {};
  return {
    ...l,
    client_id: l.client_id ?? l.client?.id,
    agent_id: l.agent_id ?? l.agent?.id ?? null,
    operator_id: l.operator_id ?? l.operator?.id ?? null,
    listing_id: l.listing_id ?? l.listing?.id ?? null,
    listing: l.listing ? normListing(l.listing) : null,
    sla_stage: l.sla_stage ?? sla.stage ?? null,
    sla_due_at: l.sla_due_at ?? sla.due_at ?? null,
    contacts_shared_at: l.contacts_shared_at ?? null,
    history: (l.history || []).map(normEvent),
    slots: l.slots || [],
  };
}

export function normBoard<T>(d: Any, fn: (x: Any) => T): Record<string, T[]> {
  const out: Record<string, T[]> = {};
  Object.entries(d || {}).forEach(([k, v]) => { out[k] = Array.isArray(v) ? v.map(fn) : []; });
  return out;
}

export function normProperty(p: Any): Property {
  if (!p) return p;
  const media = (p.media || []).map((m: Any) => ({ ...m, path: m.path ?? m.url, url: m.url ?? m.path }));
  return { ...p, media, photos: p.photos ?? p.listing?.photos ?? [] };
}

export function normRecommend(r: Any): RecommendedAgent {
  return { ...r, load: r.load ?? r.active_leads };
}

export function normAgentStat(a: Any): AgentStat {
  return {
    ...a,
    next_level_at: a.next_level_at ?? a.next ?? null,
    active_leads: a.active_leads ?? a.open_leads,
    active_properties: a.active_properties ?? a.properties,
    deals_month: a.deals_month ?? a.deals,
    exclusive_deals: a.exclusive_deals ?? 0,
  };
}

export function normCounter(r: Any): CounterRow {
  return { ...r, user_id: r.user_id ?? r.user?.id, name: r.name ?? r.user?.name ?? '—', role: r.role ?? r.user?.role, mentor_id: r.mentor_id ?? r.user?.mentor_id ?? null };
}

export function normDashboard(d: Any): Dashboard {
  if (!d) return d;
  const dm = d.deals_month;
  const isObj = dm && typeof dm === 'object';
  const lb = d.top_agents ?? d.leaderboard_all ?? d.leaderboard ?? [];
  return {
    ...d,
    deals_count: d.deals_count ?? (isObj ? dm.total : dm),
    platform_commission: d.platform_commission ?? (isObj ? dm.platform : undefined),
    leads_open: d.leads_open ?? d.open_leads,
    top_agents: [...lb].map(normAgentStat).sort((a, b) => b.exclusive_deals - a.exclusive_deals),
    reposts: d.reposts ?? d.channel_active_listings,
  };
}

export function normCabinet(c: Any): Cabinet {
  if (!c) return c;
  const counters = c.counters ? { photo: c.counters.photo, showing: c.counters.showing, stickers: c.counters.stickers, rental: c.counters.rental } : undefined;
  return {
    ...c,
    counters,
    level: c.level ? { ...c.level, next_level_at: c.level.next_level_at ?? c.level.next ?? null } : undefined,
    properties: (c.properties || []).map(normProperty),
    leads: (c.leads || []).map(normLead),
    viewings: (c.viewings ?? c.upcoming_viewings ?? []).map(normLead),
    expiring: c.expiring ?? c.expiring_contracts ?? [],
  };
}

export function normReport(r: Any): Report {
  const d = r?.data || {};
  const st = r?.dup_check ?? d.dup_check ?? (r?.kind === 'makefoto' ? d.status : null);
  return { ...r, dup_check: st === 'duplicate' ? 'exists' : st };
}

/** /clients/{id}: leads are lead cards (listing without title), reactions carry {reaction, created_at, listing}. */
export function normClient(c: Any): Client {
  if (!c) return c;
  return {
    ...c,
    profile: c.profile || {},
    leads: Array.isArray(c.leads) ? c.leads.map(normLead) : c.leads ?? c.leads_count,
    reactions: (c.reactions || []).map((r: Any) => ({
      ...r, reaction: r.reaction ?? r.kind, created_at: r.created_at ?? r.at ?? null,
      listing: r.listing ? normListing(r.listing) : r.title ? { id: r.listing_id, title: r.title } : null,
    })),
  };
}
