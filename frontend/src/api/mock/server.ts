// VITE_MOCK=1 in-memory API. Mimics §7 shapes and the main state transitions so the UI can be exercised offline.
import { ApiError, getToken } from '../client';
import { scenarioRows, type DistRow, type Scenario } from '../../lib/scenarios';
import type { ChangeRequest, Client, Deal, EventItem, Lead, LeadStatus, Listing, PhoneRow, Property, PropertyStatus, Report, SimilarItem, User } from '../types';
import { STAFF_ROLES } from '../types';
import * as F from './fixtures';
import { registerDailyRent } from './dailyRent';

const delay = (ms = 160) => new Promise((r) => setTimeout(r, ms));
const page = <T>(items: T[]) => ({ items, total: items.length });
let seq = 10_000;
const nextId = () => ++seq;

function currentUser(): User {
  const t = getToken();
  if (!t) throw new ApiError(401, 'Требуется вход');
  const id = Number(t.replace('mock-', ''));
  const u = F.users.find((x) => x.id === id);
  if (!u) throw new ApiError(401, 'Требуется вход');
  // «Права» defaults (backend services/perms.py SECTIONS)
  const ladder = ['superadmin', 'admin', 'operator', 'agent', 'recruit'].includes(u.role);
  return { ...u, perms: { contracts: ladder ? 'rcu' : '', originals: STAFF_ROLES.includes(u.role) ? 'r' : '' } };
}
const isStaff = (u: User) => STAFF_ROLES.includes(u.role);

function levelFor(n: number) { return n < 11 ? 50 : n < 101 ? 70 : 90; }
function nextLevelAt(n: number) { return n < 11 ? 11 : n < 101 ? 101 : null; }

/** backend dedup.phone_rows / e164, roughly: the list (or the single main phone over the old list), «+995» for a local number, repeats dropped */
function mockPhones(list: unknown, main: unknown, old?: { phone?: string | null; phones?: PhoneRow[] | null } | null): PhoneRow[] {
  const e164 = (s: string) => { const d = s.replace(/\D/g, ''); return s.trim().startsWith('+') ? '+' + d : d.length <= 10 ? '+995' + d.slice(-9) : '+' + d; };
  const rows = Array.isArray(list) && list.length ? list as PhoneRow[]
    : main ? [{ phone: String(main) }, ...(old?.phones || []).slice(1)] : old?.phones || (old?.phone ? [{ phone: old.phone }] : []);
  const seen = new Set<string>();
  return rows.filter((r) => r.phone && r.phone.replace(/\D/g, '').replace(/^995$/, '')).map((r) => ({ phone: e164(r.phone), note: r.note || null }))
    .filter((r) => !seen.has(r.phone) && !!seen.add(r.phone));
}

// «Где ещё выложен» (backend properties.copies): the found copies of a card on the sites
const COPIES: NonNullable<Property['copies']> = [
  { listing_id: 'mh26740808', where: 'myhome.ge', url: 'https://www.myhome.ge/pr/26740808/', pct: null, level: 'linked', who: 'собственник', blocking: false, asked: null },
  { listing_id: 'mh26738483', where: 'myhome.ge', url: 'https://www.myhome.ge/pr/26738483/', pct: 96, level: 'confirmed', who: 'агент', blocking: false, asked: null },
  { listing_id: 'mh26720465', where: 'myhome.ge', url: 'https://www.myhome.ge/pr/26720465/', pct: 91, level: 'confirmed', who: 'агент', blocking: false,
    asked: { at: new Date(Date.now() - 3 * 3600e3).toISOString(), by: 'Вика' } },
  { listing_id: 'ss31877412', where: 'ss.ge', url: 'https://home.ss.ge/ka/udzravi-qoneba/31877412', pct: 88, level: 'confirmed', who: null, blocking: false, asked: null },
  { listing_id: 'kr1073432', where: 'korter', url: 'https://korter.ge/en/1073432', pct: 72, level: 'probable', who: null, blocking: false, asked: null },
];
function propOut(p: Property, u: User): Property {
  if (!p.copies) p.copies = COPIES.map((c) => ({ ...c, blocking: c.level === 'linked' && !!p.is_exclusive }));
  const canPhone = isStaff(u) || p.agent_id === u.id;
  return { ...p, photo: p.photo ?? p.media?.find((m) => m.kind === 'photo')?.path ?? null, owner: p.owner ? { ...p.owner, phone: canPhone ? p.owner.phone : null, phones: canPhone ? p.owner.phones : null } : null };
}
function leadOut(l: Lead, u: User): Lead {
  const client = F.clients.find((c) => c.id === l.client_id) || null;
  const canContacts = isStaff(u) || !!l.contacts_shared_at;
  return {
    ...l,
    client: client ? { ...client, phone: canContacts ? client.phone : null, username: canContacts ? client.username : null, name: canContacts ? client.name : client.name?.[0] + '.' } : null,
  };
}
function propEvent(pid: number, action: string, text: string, u?: User | null, label?: string) {
  (F.propertyEvents[pid] ||= []).push({ id: nextId(), action, text, actor_id: u?.id ?? null, actor_name: u?.name ?? null, actor_label: label ?? null, at: new Date().toISOString() });
}
function leadEvent(l: Lead, action: string, text: string, u?: User | null, label?: string) {
  (l.history ||= []).push({ id: nextId(), action, text, actor_id: u?.id ?? null, actor_name: u?.name ?? null, actor_label: label ?? null, at: new Date().toISOString() });
}
function setLeadStatus(l: Lead, s: LeadStatus, stage: Lead['sla_stage'] = null) {
  l.status = s;
  l.sla_stage = stage;
  if (stage) {
    const m = F.SLA_MIN[stage];
    l.sla_window_min = m;
    l.sla_due_at = new Date(Date.now() + m * 60_000).toISOString();
  } else { l.sla_due_at = null; l.sla_window_min = null; }
}
function getProp(id: number) {
  const p = F.properties.find((x) => x.id === id);
  if (!p) throw new ApiError(404, 'Объект не найден');
  return p;
}
function getLead(id: number) {
  const l = F.leads.find((x) => x.id === id);
  if (!l) throw new ApiError(404, 'Заявка не найдена');
  return l;
}
function calc(b: Record<string, unknown>) {
  const price = Number(b.price) || 0;
  const pct = Number(b.commission_pct ?? 50);
  const level = Number(b.agent_level ?? 50);
  const comm = b.is_rerented ? 0 : b.commission_total ? Number(b.commission_total) : Math.round((price * pct) / 100);
  const recruit = Math.round((comm * Number(b.recruit_pct || 0)) / 100);
  const src = b.client_src ? Math.round(comm * 0.1) : 0;
  const sc = b.scenario as Scenario | null | undefined;
  if (sc && !b.is_rerented) {
    const rows = scenarioRows(sc, level, !!b.is_exclusive).map((r) => ({ ...r, amount: r.who === 'platform' ? 0 : Math.round((comm * r.pct) / 100) }));
    const pl = rows.find((r) => r.who === 'platform')!;
    pl.amount = comm - rows.filter((r) => r !== pl).reduce((s, r) => s + r.amount!, 0);
    const sum = (w: string) => rows.filter((r) => r.who === w).reduce((s, r) => s + r.amount!, 0);
    return { commission_total: comm, platform_amount: pl.amount, agent_amount: sum('agent'), recruit_amount: sum('partner'), client_src_amount: 0, distribution: rows, scenario: sc };
  }
  const share = b.is_exclusive === false ? 50 : level;
  const plat = b.is_rerented ? 0 : Math.round((comm * (100 - share)) / 100);
  const agent = comm - plat - recruit - src;
  const pc = (a: number) => (comm ? Math.round((a / comm) * 100) : 0);
  const distribution: DistRow[] = [{ who: 'platform', role: 'Платформа', pct: pc(plat), amount: plat }, { who: 'agent', role: 'Агент', pct: pc(agent), amount: agent },
    ...(recruit ? [{ who: 'partner' as const, role: 'Рекрут', pct: pc(recruit), amount: recruit }] : []), ...(src ? [{ who: 'other' as const, role: 'Источник клиента', pct: pc(src), amount: src }] : [])];
  return { commission_total: comm, platform_amount: plat, agent_amount: agent, recruit_amount: recruit, client_src_amount: src, distribution };
}
function monthKey(d: string | undefined) {
  const dt = d ? new Date(d) : new Date();
  return `${dt.getMonth() + 1}/${dt.getFullYear()}`;
}
function counters(month?: string) {
  const m = month || monthKey(undefined);
  return F.users.filter((u) => u.role === 'recruit' && u.is_active).map((u) => {
    const rs = F.reports.filter((r) => r.recruit_id === u.id && monthKey(r.created_at) === m);
    const plans = u.plans || { photo: 15, showing: 15, stickers: 15, rental: 15 };
    const cnt = (k: string) => rs.filter((r) => r.kind === k).length;
    const rental = F.deals.filter((d) => d.recruit_id === u.id && d.month === m && d.status !== 'cancelled').length;
    return {
      user_id: u.id, name: u.name, mentor_name: F.users.find((x) => x.id === u.mentor_id)?.name ?? null,
      photo: { done: cnt('photo') + 6, plan: plans.photo }, showing: { done: cnt('showing') + 9, plan: plans.showing },
      stickers: { done: cnt('stickers') + 11, plan: plans.stickers }, rental: { done: rental + 2, plan: plans.rental },
    };
  });
}
function mockActivity(n: number, active: number, deals: number) {
  const targets = { new: 60, active: 60, deals: 5 }, weights = { new: 40, active: 30, deals: 30 };
  const score = Math.round(Math.min(n / 60, 1) * 40 + Math.min(active / 60, 1) * 30 + Math.min(deals / 5, 1) * 30);
  return { score, new: n, active, deals, targets, weights, days: 30, since: '2026-11-13' };
}

function agentStats() {
  return F.users.filter((u) => (u.role === 'agent' || u.role === 'recruit') && u.is_active).map((u) => ({
    id: u.id, name: u.name, role: u.role, photo_url: u.photo_url, level: u.level ?? levelFor(u.exclusive_deals),
    exclusive_deals: u.exclusive_deals, next_level_at: nextLevelAt(u.exclusive_deals),
    deals_month: F.deals.filter((d) => d.agent_id === u.id && d.status === 'confirmed').length,
    active_leads: F.leads.filter((l) => l.agent_id === u.id && !['rented', 'refused'].includes(l.status)).length,
    active_properties: F.properties.filter((p) => p.agent_id === u.id && !['published', 'rented', 'archived'].includes(p.status)).length,
    avg_response_min: 8 + (u.id * 7) % 25,
    activity: mockActivity(u.id % 4, u.id % 3, u.id % 5 === 0 ? 1 : 0),
  })).sort((a, b) => b.exclusive_deals - a.exclusive_deals);
}

type Handler = (m: RegExpMatchArray, body: Record<string, unknown>, q: URLSearchParams) => unknown;
const routes: [string, RegExp, Handler][] = [];
const on = (method: string, path: string, h: Handler) =>
  routes.push([method, new RegExp('^' + path.replace(/:(\w+)/g, '([^/]+)') + '$'), h]);

registerDailyRent(on);  // «Посуточная аренда»

// ---------- auth ----------
on('GET', '/config', () => ({ bot_username: 'HomesinGeorgia_bot', mock: true }));
on('POST', '/auth/login', (_m, b) => {
  const login = String(b.login || '').toLowerCase();
  const u = F.users.find((x) => x.login === login);
  if (!u || !b.password) throw new ApiError(400, 'Неверный логин или пароль', 'invalid_credentials');
  return { access_token: `mock-${u.id}`, user: u };
});
on('POST', '/auth/telegram-webapp', () => ({ access_token: 'mock-3', user: F.users[2] }));
on('POST', '/auth/telegram-login', () => ({ access_token: 'mock-1', user: F.users[0] }));
on('GET', '/auth/me', () => currentUser());

// ---------- users ----------
on('GET', '/users', (_m, _b, q) => {
  let us = F.users;
  if (q.get('role')) us = us.filter((u) => q.get('role')!.split(',').includes(u.role));
  if (q.get('active')) us = us.filter((u) => String(u.is_active) === q.get('active') || (q.get('active') === '1' && u.is_active));
  return page(us.map((u) => ({ ...u, next_level_at: nextLevelAt(u.exclusive_deals) })));
});
on('POST', '/users', (_m, b) => {
  const u = { id: nextId(), districts: [], is_active: true, exclusive_deals: 0, plans: { photo: 15, showing: 15, stickers: 15, rental: 15 }, ...b } as unknown as User;
  F.users.push(u);
  return u;
});
on('PATCH', '/users/:id', (m, b) => {
  const u = F.users.find((x) => x.id === Number(m[1]));
  if (!u) throw new ApiError(404, 'Не найдено');
  Object.assign(u, b);
  return u;
});
on('POST', '/users/:id/promote', (m) => {
  const u = F.users.find((x) => x.id === Number(m[1]))!;
  u.role = 'agent'; u.level = 50;
  return u;
});
on('GET', '/me/cabinet', () => {
  const u = currentUser();
  const c = counters().find((x) => x.user_id === u.id);
  return {
    user: u,
    month: monthKey(undefined),
    counters: c ? { photo: c.photo, showing: c.showing, stickers: c.stickers, rental: c.rental } : {
      photo: { done: 5, plan: 15 }, showing: { done: 11, plan: 15 }, stickers: { done: 0, plan: 15 }, rental: { done: 3, plan: 15 },
    },
    level: { level: u.level ?? levelFor(u.exclusive_deals), exclusive_deals: u.exclusive_deals, next_level_at: nextLevelAt(u.exclusive_deals), to_next: nextLevelAt(u.exclusive_deals) ? nextLevelAt(u.exclusive_deals)! - u.exclusive_deals : null },
    deals: F.deals.filter((d) => d.agent_id === u.id),
    properties: F.properties.filter((p) => p.agent_id === u.id).map((p) => propOut(p, u)),
    leads: F.leads.filter((l) => l.agent_id === u.id).map((l) => leadOut(l, u)),
    viewings: F.leads.filter((l) => l.agent_id === u.id && l.viewing_at && new Date(l.viewing_at).getTime() > Date.now() - 3600_000).map((l) => leadOut(l, u)),
    expiring: F.deals.filter((d) => d.agent_id === u.id && d.rent_to && d.status === 'confirmed' && new Date(d.rent_to).getTime() - Date.now() < 31 * 86400_000),
  };
});

// ---------- funnel 1 ----------
on('GET', '/listings/new', (_m, _b, q) => {
  let ls = q.get('competitor') ? F.rivalListings : F.listings.filter((l) => !l.property_id);
  const s = q.get('q')?.toLowerCase();
  if (s) ls = ls.filter((l) => `${l.title} ${l.address} ${l.district}`.toLowerCase().includes(s));
  if (q.get('source')) ls = ls.filter((l) => l.source === q.get('source'));
  if (q.get('district')) ls = ls.filter((l) => l.district === q.get('district'));
  // «по этому адресу» hints (11-13): L1000 / L1003 have a live object, L1001 / L1004 only archived ones
  const sim = (l: Listing, st: string[]) => st.map((status, k) => ({ id: 900 + k, url: `/properties/${900 + k}`, address: l.address, status,
    archive_reason: status === 'archived' ? 'not_actual' : null, photo: l.photos?.[0] ?? null, photos: l.photos || [], price_usd: l.price,
    bedrooms: l.bedrooms, area: l.area, floor: null, reasons: ['тот же дом'] }));
  const hints: Record<string, string[]> = { L1000: ['published', 'archived'], L1003: ['review'], L1001: ['archived'], L1004: ['archived', 'archived'] };
  ls = ls.map((l) => (hints[l.id] ? { ...l, similar: sim(l, hints[l.id]) } : l));
  const flag = q.get('flag');
  const live = (l: Listing) => (l.similar || []).some((x) => x.status !== 'archived');
  if (flag === 'ours' || flag === 'not_ours') ls = ls.filter((l) => (l.id === 'R1') === (flag === 'ours'));  // R1: a flat we have
  else if (flag === 'in_base') ls = ls.filter(live);
  else if (flag === 'in_archive') ls = ls.filter((l) => l.similar?.length && !live(l));
  else if (flag) ls = ls.filter((l) => (l.ai_flags as Record<string, unknown> | null)?.[flag]);
  return page(ls);
});
on('GET', '/listings/new/facets', (_m, _b, q) => {
  const count = (xs: (string | null | undefined)[]) => Object.entries(xs.filter(Boolean).reduce<Record<string, number>>((a, v) => ({ ...a, [v!]: (a[v!] || 0) + 1 }), {})).map(([value, n]) => ({ value, count: n }));
  const ls = q.get('competitor') ? F.rivalListings : F.listings.filter((l) => !l.property_id);
  return { sources: count(ls.map((l) => l.source)), districts: count(ls.map((l) => l.district)), metros: [] };
});
on('GET', '/listings/:id', (m) => {
  const l = F.listings.find((x) => x.id === m[1]);
  // backend listing_get: the object's owner with every number, for staff and the object's agent
  const p = F.properties.find((x) => x.listing_id === m[1]); const u = currentUser();
  return l && p?.owner && (isStaff(u) || p.agent_id === u.id) ? { ...l, contacts_visible: true, owner: { name: p.owner.name, phone: p.owner.phone, phones: p.owner.phones, is_agent: p.owner.is_agent } } : l;
});
on('GET', '/listings/:id/similar', () => ({ items: [] }));
// backend properties.similar_with_sites: the object's site ads lead («привязано» first), with the takedown marks
on('GET', '/properties/:id/similar', (m) => {
  const p = getProp(Number(m[1]));
  propOut(p, currentUser());
  const sites: SimilarItem[] = (p.copies || []).map((c, i) => ({
    key: `l:${c.listing_id}`, kind: 'ad', property_id: null, listing_id: c.listing_id, where: c.where, url: c.url,
    level: c.level === 'probable' ? 'maybe' : 'same', pct: c.pct ?? undefined,
    reasons: c.level === 'linked' ? ['привязано к объекту'] : [`общих фото: ${5 - Math.min(i, 3)}`, 'спальни совпадают', 'этаж совпадает'],
    address: 'რამიშვილი ნ. ქ. 3', district: 'Ваке', bedrooms: 1, area: 56 + i, floor: 8, price_usd: 800, photos: [],
    linked: c.level === 'linked', blocking: c.blocking, site: true, who: c.who, asked: c.asked, removed: REMOVED.has(c.listing_id),
  }));
  return { items: [...sites, ...(p.duplicate_scan?.n ? DUPS.filter((x) => !x.key.startsWith('l:mh')) : [])] };
});
const REMOVED = new Set<string>();
on('POST', '/properties/:id/myhome-copies', (m) => { dupScan(getProp(Number(m[1])), 'manual'); return { ok: true }; });
on('POST', '/properties/:id/listings/:lid/takedown', (m) => {
  const p = getProp(Number(m[1]));
  p.copies = (p.copies || []).map((c) => c.listing_id === m[2] ? { ...c, asked: { at: new Date().toISOString(), by: currentUser().name } } : c);
  return propOut(p, currentUser());
});
on('POST', '/properties/:id/listings/:lid/removed', (m) => {
  const p = getProp(Number(m[1]));
  REMOVED.add(m[2]);
  return propOut(p, currentUser());
});
on('POST', '/similar/decide', (_m, b) => ({ ok: true, verdict: (b as { verdict?: string }).verdict || 'different', linked_to: null }));
const dealOf = (p: Property) => (p.post_fields?.deal === 'Sale' ? 'sale' : 'rent');
const dealOk = (q: URLSearchParams) => (p: Property) => !q.get('deal') || dealOf(p) === q.get('deal');
on('GET', '/properties/counts', (_m, _b, q) => F.properties.filter(dealOk(q)).reduce<Record<string, number>>((a, p) => ({ ...a, [p.status]: (a[p.status] || 0) + 1 }), {}));
on('GET', '/properties/board', (_m, _b, q) => {
  const u = currentUser();
  const out: Record<string, Property[]> = {};
  F.properties.filter((p) => !(p as { board_hidden?: boolean }).board_hidden).filter(dealOk(q)).forEach((p) => { (out[p.status] ||= []).push(propOut(p, u)); });
  return out;
});
on('POST', '/properties/board/hide', (_m, b) => {
  const u = currentUser();
  const ps = F.properties.filter((p) => p.status === (b as { status: string }).status && (u.role !== 'agent' || p.agent_id === u.id));
  ps.forEach((p) => { (p as { board_hidden?: boolean }).board_hidden = true; });
  return { hidden: ps.length };
});
const propScope = (q: URLSearchParams) => {
  const u = currentUser();
  let ps = F.properties.filter(dealOk(q));
  if (q.get('mine')) ps = ps.filter((p) => p.agent_id === u.id);
  if (q.get('status')) ps = ps.filter((p) => q.get('status')!.split(',').includes(p.status));
  if (q.get('agent_id')) ps = ps.filter((p) => (p.agent_id ?? 0) === Number(q.get('agent_id')));
  return ps;
};
const metroOf = (p: Property) => (p.post_fields as Record<string, string> | undefined)?.metro;
on('GET', '/properties', (_m, _b, q) => {
  let ps = propScope(q);
  const s = q.get('q')?.toLowerCase();
  if (s) ps = ps.filter((p) => `${p.address} ${p.district} ${p.id}`.toLowerCase().includes(s));
  const any = (k: string, v?: string | null) => !q.getAll(k).length || q.getAll(k).includes(v || '');
  const beds = (q.get('bedrooms') || '').split(',').filter(Boolean).map(Number);
  ps = ps.filter((p) => any('source', p.source_type) && any('district', p.district) && (!q.get('metro') || metroOf(p) === q.get('metro'))
    && (!beds.length || beds.some((b) => (b === 4 ? (p.bedrooms ?? -1) >= 4 : p.bedrooms === b)))
    && (!q.get('price_min') || (p.price ?? 0) >= Number(q.get('price_min'))) && (!q.get('price_max') || (p.price ?? 0) <= Number(q.get('price_max')))
    && (!q.get('archive_reason') || p.archive_reason === q.get('archive_reason'))
    && (!q.get('freeing') || (p.status === 'archived' && p.archive_reason === 'rented_by_owner' && !!p.rent_to && p.rent_to <= new Date().toISOString().slice(0, 10))));
  return page(ps.map((p) => propOut(p, currentUser())));
});
on('GET', '/properties/facets', (_m, _b, q) => {
  const count = (xs: (string | null | undefined)[]) => Object.entries(xs.filter(Boolean).reduce<Record<string, number>>((a, v) => ({ ...a, [v!]: (a[v!] || 0) + 1 }), {})).map(([value, n]) => ({ value, count: n }));
  const ps = propScope(q);
  return { sources: count(ps.map((p) => p.source_type)), districts: count(ps.map((p) => p.district)), metros: count(ps.map(metroOf)) };
});
on('POST', '/properties/check-duplicate', (_m, b) => {
  const phone = String(b.phone || '').replace(/\D/g, '').slice(-6);
  const hit = F.owners.find((o) => phone && o.phone?.replace(/\D/g, '').endsWith(phone));
  return { status: hit ? 'exists' : 'new', matches: hit ? [hit] : [] };
});
on('POST', '/properties', (_m, b) => {
  const u = currentUser();
  const lid = b.listing_id as string | undefined;
  const l = lid ? F.listings.find((x) => x.id === lid) : undefined;
  if (l?.taken_by && l.taken_by.id !== u.id) throw new ApiError(409, 'Объект уже взят другим агентом', 'taken_by_other');
  const p: Property = {
    id: nextId(), listing_id: lid ?? null, source_type: l?.source ?? 'direct', source_url: l?.url ?? null, status: 'taken',
    agent: F.ref(u.id), agent_id: u.id, taken_at: new Date().toISOString(), call_attempts: 0,
    address: (b.address as string) ?? l?.address ?? null, district: (b.district as string) ?? l?.district ?? null,
    price: (b.price as number) ?? l?.price ?? null, currency: 'USD', bedrooms: (b.bedrooms as number) ?? l?.bedrooms ?? null,
    area: l?.area ?? null, ai_flags: l?.ai_flags ?? {}, checklist: {}, media: [],
    owner: { id: nextId(), name: 'Собственник', phone: '+995 598 44 55 66' }, created_at: new Date().toISOString(),
  };
  F.properties.unshift(p);
  if (l) { l.property_id = p.id; l.taken_by = F.ref(u.id); }
  propEvent(p.id, 'taken', 'Беру в работу', u);
  return propOut(p, u);
});
// the contract wizard from the object card + its object picker (Irakli 11-15)
on('GET', '/properties/:id/contract', (m) => {
  const p = getProp(Number(m[1])), n = (v: unknown) => (v ? String(v) : '');
  return { property_id: p.id, type: 'rent', district: p.district, owner: { name: p.owner?.name, phone: p.owner?.phone },
    data: { street_house: n(p.address), cadastral_code: n(p.cadastral_code), floor: n(p.floor), apartment_area: n(p.area), price_usd: n(p.price), deposit_usd: n(p.price), owner_full_name: n(p.owner?.name) } };
});
on('GET', '/properties/deal-search', (_m, _b, q) => {
  const pid = q.get('pid');
  if (pid) {
    const p = getProp(Number(pid));
    return { items: [{ id: p.id, address: p.address, district: p.district, status: p.status, blocked: null, agent_id: p.agent_id, lead_id: null,
      fields: { address: p.address, price: p.price, owner_name: p.owner?.name, owner_phone: p.owner?.phone } }] };
  }
  const t = String(q.get('q') || '').toLowerCase().replace(/^#/, '');
  return { items: t ? F.properties.filter((p) => String(p.id) === t || (p.address || '').toLowerCase().includes(t)).slice(0, 20)
    .map((p) => ({ id: p.id, address: p.address, district: p.district, status: p.status, blocked: null })) : [] };
});
on('GET', '/properties/:id/history', (m) => page([...(F.propertyEvents[Number(m[1])] || [])].reverse()));
on('GET', '/properties/:id', (m) => propOut(getProp(Number(m[1])), currentUser()));
on('PATCH', '/properties/:id', (m, b) => {
  const p = getProp(Number(m[1]));
  const { owner_name, owner_phone, owner_phones, owner_id, owner_separate: _sep, reason: _r, ...rest } = b as Record<string, unknown>;
  const becameExclusive = rest.is_exclusive === true && !p.is_exclusive;
  Object.assign(p, rest);
  if (becameExclusive) dupScan(p, 'exclusive');
  if (owner_id || owner_name || owner_phone || owner_phones) {
    const phones = mockPhones(owner_phones, owner_phone, p.owner);
    p.owner = { ...(p.owner || { id: nextId(), is_agent: false }), ...(owner_id ? { id: Number(owner_id) } : {}),
      name: (owner_name as string) || p.owner?.name, phone: phones[0]?.phone ?? null, phones };
  }
  return propOut(p, currentUser());
});
// Irakli 11-18: an exclusive searches its duplicates everywhere (backend similar.scan_exclusive, a worker job):
// mocked as done at once, with two strong candidates
const DUPS: SimilarItem[] = [
  { key: 'l:mh7001', kind: 'ad', property_id: null, listing_id: 'mh7001', where: 'myhome.ge', level: 'same', reasons: ['общих фото: 3', 'спальни совпадают', 'площадь близкая (70 / 74 м²)'],
    address: 'Tsatskhvebi 4', district: 'Ваке', bedrooms: 2, area: 74, floor: 5, price_usd: 900, photos: [], url: 'https://myhome.ge/7001' },
  { key: 'l:tg88', kind: 'ad', property_id: null, listing_id: 'tg88', where: 'конкурент · tbilisi_flats', competitor: true, level: 'maybe', reasons: ['тот же телефон собственника', 'спальни совпадают'],
    address: null, district: 'Ваке', bedrooms: 2, area: 70, floor: 4, price_usd: 850, photos: [], url: 'https://t.me/tbilisi_flats/88' },
  { key: 'p:104', kind: 'object', property_id: 104, listing_id: null, where: 'наша база', status: 'archived', level: 'maybe', reasons: ['тот же дом', 'этаж совпадает'],
    address: 'ул. Палиашвили 10', district: 'Ваке', bedrooms: 2, area: 68, floor: 4, price_usd: 800, photos: [], can_open: true },
];
const STRONG = ['та же ссылка', 'тот же кадастровый код', 'общих фото', 'одно общее фото', 'тот же телефон собственника'];
function dupScan(p: Property, trigger: string) {
  const strong = DUPS.filter((x) => x.reasons.some((r) => STRONG.some((h) => r.startsWith(h))))
    .map((x) => ({ key: x.key, where: x.where, property_id: x.property_id, listing_id: x.listing_id, url: x.url ?? null, address: x.address ?? null, reasons: x.reasons.filter((r) => STRONG.some((h) => r.startsWith(h))) }));
  p.duplicate_scan = { n: DUPS.length, strong, trigger, at: new Date().toISOString(), pending: false };
  propEvent(p.id, 'duplicate_scan', `Поиск дублей: ${DUPS.length}`);
}
// «Без электронной подписи» → «Запрос оператору» (backend change_requests.request_no_esign / decide)
const CRS: ChangeRequest[] = [];
function noEsignOut(r: ChangeRequest): NonNullable<Property['no_esign']> {
  return { id: r.id, status: r.status, reason: r.reason ?? null, created_at: r.created_at, requested_by: r.requested_by, decided_by: r.decided_by ?? null,
    decided_at: r.decided_at ?? null, comment: r.decision_comment ?? null, scan_url: r.scan_url ?? null };
}
on('POST', '/properties/:id/exclusive-no-esign', (m, b) => {
  const u = currentUser(); const p = getProp(Number(m[1]));
  if (!isStaff(u) && p.agent_id !== u.id) throw new ApiError(403, 'object is taken by another agent');
  const reason = String(b.reason || '').trim();
  if (!reason) throw new ApiError(422, 'Укажите, почему собственник не может подписать по ссылке');
  const file = (b.__files as { name: string; url: string }[] | undefined)?.[0];
  CRS.filter((r) => r.entity_id === p.id && r.kind === 'no_esign' && r.status === 'pending').forEach((r) => { r.status = 'rejected'; r.decision_comment = 'заменён новым запросом'; });
  const r: ChangeRequest = { id: nextId(), entity: 'property', entity_id: p.id, kind: 'no_esign', status: 'pending', reason, changes: [], scan_url: file?.url ?? null,
    requested_by: { id: u.id, name: u.name }, created_at: new Date().toISOString(), property: { id: p.id, address: p.address, status: p.status } };
  CRS.push(r);
  p.no_esign = noEsignOut(r);
  p.change_requests = CRS.filter((x) => x.entity_id === p.id && x.status === 'pending');
  propEvent(p.id, 'change_requested', `Запрос оператору: ${reason}`, u);
  return propOut(p, u);
});
on('GET', '/change-requests', (_m, _b, q) => { const st = (q.get('status') || 'pending').split(','); return page(CRS.filter((r) => st.includes(r.status))); });
on('POST', '/change-requests/:id/decide', (m, b) => {
  const u = currentUser(); const r = CRS.find((x) => x.id === Number(m[1]));
  if (!r) throw new ApiError(404, 'request not found');
  if (!isStaff(u) || r.requested_by.id === u.id) throw new ApiError(403, 'Свой запрос решает другой оператор');
  if (r.status !== 'pending') throw new ApiError(409, 'Запрос уже рассмотрен');
  const comment = String(b.comment || '').trim() || null;
  if (!b.approve && !comment) throw new ApiError(422, 'Напишите агенту, почему отказ');
  Object.assign(r, { status: b.approve ? 'approved' : 'rejected', decided_by: { id: u.id, name: u.name }, decided_at: new Date().toISOString(), decision_comment: comment });
  const p = getProp(r.entity_id);
  if (r.kind === 'no_esign') {
    p.no_esign = noEsignOut(r);
    p.missing = (p.missing || []).filter((x) => !(b.approve && x === 'эксклюзивный договор (PDF)'));
    if (b.approve) { propEvent(p.id, 'exclusive_contract_waived', `${r.reason}`, u); dupScan(p, 'contract'); }
  }
  p.change_requests = CRS.filter((x) => x.entity_id === p.id && x.status === 'pending');
  propEvent(p.id, `change_request_${r.status}`, comment || '', u);
  return r;
});
on('POST', '/properties/:id/call', (m, b) => {
  const u = currentUser(); const p = getProp(Number(m[1]));
  p.call_attempts = (p.call_attempts || 0) + 1;
  if (b.reached) { p.status = 'owner_confirmed'; p.next_call_at = null; propEvent(p.id, 'call', 'Дозвонился', u); }
  else { p.status = 'call_failed'; p.next_call_at = new Date(Date.now() + 2 * 3600_000).toISOString(); propEvent(p.id, 'call', 'Не дозвонился · бот напомнит через 2 часа', u); }
  return propOut(p, u);
});
on('POST', '/properties/:id/owner', (m, b) => {
  const u = currentUser(); const p = getProp(Number(m[1]));
  const phones = mockPhones(b.owner_phones, b.owner_phone, p.owner);
  p.owner = { id: p.owner?.id ?? nextId(), name: (b.owner_name as string) || p.owner?.name, phone: phones[0]?.phone ?? null, phones, is_agent: !b.is_owner };
  p.status = b.is_owner ? 'owner_confirmed' : 'agent_marked';
  propEvent(p.id, b.is_owner ? 'owner_confirmed' : 'agent_marked', b.is_owner ? 'Собственник подтвердил' : 'Ответил агент → в базу с пометкой', u);
  return propOut(p, u);
});
on('POST', '/properties/:id/shoot', (m, b) => {
  const u = currentUser(); const p = getProp(Number(m[1]));
  p.shoot_at = b.shoot_at as string; p.status = 'shoot_scheduled';
  p.dup_check = p.id % 2 ? 'exists' : 'added';
  propEvent(p.id, 'shoot_scheduled', `Съёмка назначена · ${p.dup_check === 'exists' ? '⚠️ Уже есть в базе' : '✅ Добавлено в базу'}`, u);
  return { ...propOut(p, u), dup_check: p.dup_check };
});
on('POST', '/properties/:id/media', (m, b) => {
  const u = currentUser(); const p = getProp(Number(m[1]));
  const files = (b as unknown as { __files?: { name: string; url: string }[] }).__files || [];
  const room = (b as unknown as { room?: string }).room || null;
  files.forEach((f) => (p.media ||= []).push({ id: nextId(), kind: f.name.match(/\.(mp4|mov|webm)$/i) ? 'video' : 'photo', path: f.url, room, position: p.media!.length }));
  if (room) p.checklist = { ...(p.checklist || {}), [room]: true };
  if (['shoot_scheduled', 'returned', 'owner_confirmed'].includes(p.status)) p.status = 'media_uploaded';
  propEvent(p.id, 'media_uploaded', `Загружено файлов: ${files.length}${room ? ` · ${room}` : ''}`, u);
  return propOut(p, u);
});
on('DELETE', '/properties/:id/media/:mid', (m) => { const p = getProp(Number(m[1])); p.media = (p.media || []).filter((x) => x.id !== Number(m[2])); return { ok: true }; });
on('POST', '/properties/:id/checklist', (m, b) => { const p = getProp(Number(m[1])); p.checklist = b.checklist as Record<string, boolean>; return propOut(p, currentUser()); });
on('POST', '/properties/:id/submit', (m) => {
  const u = currentUser(); const p = getProp(Number(m[1]));
  p.status = 'review'; propEvent(p.id, 'submitted', 'Фото и видео сняты → на проверку', u);
  return propOut(p, u);
});
on('POST', '/properties/:id/review', (m, b) => {
  const u = currentUser(); const p = getProp(Number(m[1]));
  p.reviewed_by = u.id; p.reviewed_at = new Date().toISOString(); p.review_comment = (b.comment as string) || null;
  if (b.approve) {
    propEvent(p.id, 'approved', 'Одобрено оператором', u);
    p.status = 'published'; p.published_at = new Date().toISOString(); p.post_url = 'https://t.me/rent_tbilisi_ge/1299';
    propEvent(p.id, 'published', 'Опубликовано в канале', null, 'bot');
  } else { p.status = 'returned'; propEvent(p.id, 'returned', `Возвращено агенту: ${b.comment || ''}`, u); }
  return propOut(p, u);
});
on('GET', '/properties/:id/post', (m) => {
  const p = getProp(Number(m[1]));
  if (dealOf(p) === 'sale') {
    const f = (p.post_fields || {}) as Record<string, string | string[] | number | undefined>;
    const text = [`#${p.district || 'Tbilisi'}${f.metro ? ` 🚇 #${f.metro}` : ''}`, `📍${p.address}`, '', f.headline ? `🌟 ${f.headline} 🌟` : '', '',
      `🏢 ${p.bedrooms ? `#${p.bedrooms}Bed ` : ''}#${f.prop_type || 'Apartment'} for #Sale`, `✨ ${[f.building, f.design === 'New' ? 'NewApartment' : f.design, f.condition].filter(Boolean).map((x) => `#${x}`).join(' | ')}`,
      `🏠 ${p.area} Sq.m | ${p.floor}/${p.total_floors} Floor${f.heating ? ` | #${f.heating}` : ''}`, '', `💰${String(f.sale_price || p.price).replace(/\B(?=(\d{3})+(?!\d))/g, '.')}$`, '0% Commission', '', 'SALE IN TBILISI🇬🇪'].join('\n').replace(/\n{3,}/g, '\n\n');
    return { text, hashtags: text.match(/#\w+/g) || [], channels: ['tg'] };
  }
  const text = [`#${p.district || 'Tbilisi'}`, p.address ? `📍${p.address}` : '', '', `🏢 ${p.bedrooms ? `#${p.bedrooms}Bed ` : ''}#Apartment for #Rent`, '',
    p.price ? `💰 ${p.price}$ + Deposit ${p.price}$` : '', '0% Commission'].join('\n');
  return { text, hashtags: text.match(/#\w+/g) || [], channels: ['tg'] };
});
on('GET', '/properties/:id/publish/progress', () => ({}));
on('POST', '/properties/:id/publish', (m, b) => {
  const u = currentUser(); const p = getProp(Number(m[1]));
  const tg = b.tg !== false, site = b.site !== false;
  if (!tg && !site) throw new ApiError(400, 'Отметьте, куда публиковать: сайт или Telegram');
  if (site) p.site_hidden = false; else if (p.status !== 'published') p.site_hidden = true;
  p.status = 'published'; p.published_at = new Date().toISOString();
  if (tg) p.post_url = 'https://t.me/rent_tbilisi_ge/1299';
  propEvent(p.id, 'published', [site && 'на сайте', tg && 'в Telegram'].filter(Boolean).join(' и '), u);
  return propOut(p, u);
});
on('POST', '/properties/:id/archive', (m, body) => {
  const b = body as { reason?: string; rent_from?: string; rent_to?: string } | undefined;
  const u = currentUser(); const p = getProp(Number(m[1]));
  if (b?.reason === 'rented_by_owner') { if (!b.rent_from || !b.rent_to) throw new ApiError(400, 'Укажите, с какого по какое число собственник сдал'); p.rent_from = b.rent_from; p.rent_to = b.rent_to; }
  p.archive_reason = b?.reason ?? null;
  p.status = 'archived' as PropertyStatus; propEvent(p.id, 'archived', 'В архив', u);
  return propOut(p, u);
});
on('POST', '/properties/:id/rented-self', (m, body) => {
  const b = body as { rent_from?: string; rent_to?: string; price?: string } | undefined;
  const u = currentUser(); const p = getProp(Number(m[1]));
  if (!b?.rent_from || !b?.rent_to) throw new ApiError(400, 'Укажите, с какого по какое число собственник сдал');
  Object.assign(p, { status: 'archived' as PropertyStatus, archive_reason: 'rented_by_owner', rent_from: b.rent_from, rent_to: b.rent_to, archive_comment: b.price ? 'сдано за $' + b.price : null });
  propEvent(p.id, 'owner_rented_self', 'Собственник сдал', u);
  return propOut(p, u);
});
on('POST', '/properties/:id/resume', (m) => {
  const u = currentUser(); const p = getProp(Number(m[1]));
  Object.assign(p, p.archive_reason === 'rented_by_owner' ? { status: 'taken' as PropertyStatus, rent_from: null, rent_to: null } : { status: 'published' as PropertyStatus });
  p.archive_reason = null;
  return propOut(p, u);
});

// ---------- owners / clients ----------
on('GET', '/owners', (_m, _b, q) => {
  const s = q.get('q')?.toLowerCase();
  const u = currentUser();
  return page(F.owners.filter((o) => !s || `${o.name} ${o.phone} ${(o.phones || []).map((r) => r.phone).join(' ')} ${o.address}`.toLowerCase().includes(s)).map((o) => ({
    ...o, phone: isStaff(u) ? o.phone : null, phones: isStaff(u) ? o.phones : null,
    properties: F.properties.filter((p) => p.owner_id === o.id).map((p) => propOut(p, u)),
  })));
});
on('POST', '/owners', (_m, b) => { const phones = mockPhones(b.phones, b.phone); const o = { id: nextId(), ...b, phones, phone: phones[0]?.phone ?? null }; F.owners.push(o); return o; });
on('PATCH', '/owners/:id', (m, b) => {
  const o = F.owners.find((x) => x.id === Number(m[1]))!; const phones = mockPhones(b.phones, b.phone, o);
  Object.assign(o, b, { phones, phone: phones[0]?.phone ?? null }); return o;
});
on('GET', '/clients', (_m, _b, q) => {
  const s = q.get('q')?.toLowerCase();
  return page(F.clients.filter((c) => !s || `${c.name} ${c.username} ${c.phone}`.toLowerCase().includes(s))
    .map((c) => ({ ...c, leads: F.leads.filter((l) => l.client_id === c.id).length })));
});
on('GET', '/clients/:id', (m) => {
  const u = currentUser();
  const c = F.clients.find((x) => x.id === Number(m[1]));
  if (!c) throw new ApiError(404, 'Не найдено');
  return { ...c, leads: F.leads.filter((l) => l.client_id === c.id).map((l) => leadOut(l, u)), reactions: F.listings.slice(0, 3).map((l, i) => ({ listing_id: l.id, listing: l, reaction: i === 2 ? 'dislike' : 'like', created_at: l.created_at })) };
});

// ---------- funnel 2 ----------
on('GET', '/leads/board', () => {
  const u = currentUser();
  const out: Record<string, Lead[]> = {};
  F.leads.filter((l) => isStaff(u) || l.agent_id === u.id).forEach((l) => { (out[l.status] ||= []).push(leadOut(l, u)); });
  return out;
});
on('GET', '/leads', (_m, _b, q) => {
  const u = currentUser();
  let ls = F.leads.filter((l) => isStaff(u) || l.agent_id === u.id);
  if (q.get('status')) ls = ls.filter((l) => q.get('status')!.split(',').includes(l.status));
  if (q.get('agent_id')) ls = ls.filter((l) => l.agent_id === Number(q.get('agent_id')));
  return page(ls.map((l) => leadOut(l, u)));
});
on('GET', '/leads/:id/recommend-agents', (m) => {
  const l = getLead(Number(m[1]));
  const district = l.listing?.district || '';
  return page(F.users.filter((u) => u.role === 'agent' && u.is_active).map((u) => {
    const load = F.leads.filter((x) => x.agent_id === u.id && !['rented', 'refused'].includes(x.status)).length;
    const reasons: string[] = [];
    let score = 0;
    const p = F.properties.find((x) => x.id === l.property_id);
    if (p?.agent_id === u.id) { reasons.push('Его объект'); score += 50; }
    if (u.districts.includes(district)) { reasons.push(`Район ${district}`); score += 30; }
    reasons.push(`В работе: ${load}`); score += Math.max(0, 20 - load * 4);
    return { agent: { ...F.ref(u.id)!, districts: u.districts, level: u.level }, score, reasons, load };
  }).sort((a, b) => b.score - a.score).map((r, i) => ({ ...r, recommended: i === 0 })));
});
on('GET', '/leads/:id', (m) => leadOut(getLead(Number(m[1])), currentUser()));
on('POST', '/leads/:id/take', (m) => {
  const u = currentUser(); const l = getLead(Number(m[1]));
  if (l.operator_id && l.operator_id !== u.id) throw new ApiError(409, 'Заявку уже взял другой оператор', 'already_taken');
  l.operator_id = u.id; l.operator = F.ref(u.id);
  setLeadStatus(l, 'operator_taken', 'operator_take');
  leadEvent(l, 'operator_taken', `✋ Взял(а) ${u.name}`, u);
  return leadOut(l, u);
});
on('POST', '/leads/:id/assign', (m, b) => {
  const u = currentUser(); const l = getLead(Number(m[1]));
  l.agent_id = Number(b.agent_id); l.agent = F.ref(l.agent_id);
  if (!l.operator_id) { l.operator_id = u.id; l.operator = F.ref(u.id); }
  setLeadStatus(l, 'agent_assigned', 'agent_slots');
  leadEvent(l, 'agent_assigned', `Назначен агент ${l.agent?.name}`, u);
  return leadOut(l, u);
});
on('POST', '/leads/:id/slots', (m, b) => {
  const u = currentUser(); const l = getLead(Number(m[1]));
  const starts = (b.starts as string[]) || [];
  l.slots = [...(l.slots || []).filter((s) => !s.chosen && false), ...starts.map((s) => ({ id: nextId(), starts_at: s, chosen: false }))];
  setLeadStatus(l, 'slots_sent');
  leadEvent(l, 'slots_sent', `Агент ввёл ${starts.length} окна, бот отправил клиенту`, u);
  return leadOut(l, u);
});
on('POST', '/leads/:id/reschedule', (m) => {
  const u = currentUser(); const l = getLead(Number(m[1]));
  setLeadStatus(l, 'agent_assigned', 'agent_slots');
  leadEvent(l, 'reschedule', 'Перенос: снова выбор времени', u);
  return leadOut(l, u);
});
on('POST', '/leads/:id/outcome', (m, b) => {
  const u = currentUser(); const l = getLead(Number(m[1]));
  l.agent_outcome = b.outcome as string;
  if (b.reason) l.refusal_reason = b.reason as string;
  if (b.outcome === 'rented') setLeadStatus(l, 'contract');
  else if (b.outcome === 'refused') setLeadStatus(l, 'refused');
  else if (b.outcome === 'thinking') setLeadStatus(l, 'thinking');
  else if (b.outcome === 'reschedule') setLeadStatus(l, 'agent_assigned', 'agent_slots');
  leadEvent(l, 'agent_outcome', `Итог агента: ${b.outcome}`, u);
  return leadOut(l, u);
});
on('POST', '/leads/:id/cancel', (m, b) => {
  const u = currentUser(); const l = getLead(Number(m[1]));
  setLeadStatus(l, 'refused'); l.refusal_reason = (b.reason as string) || 'other';
  leadEvent(l, 'cancelled', `Отменено: ${b.reason || ''}`, u);
  return leadOut(l, u);
});

// ---------- deals ----------
on('POST', '/deals/calc', (_m, b) => calc(b));
on('GET', '/deals/expiring', (_m, _b, q) => {
  const days = Number(q.get('days') || 30);
  return page(F.deals.filter((d) => d.status === 'confirmed' && d.rent_to).map((d) => ({ ...d, days_left: Math.ceil((new Date(d.rent_to!).getTime() - Date.now()) / 86400_000) }))
    .filter((d) => d.days_left! >= 0 && d.days_left! <= days).sort((a, b) => a.days_left! - b.days_left!));
});
on('GET', '/deals/find', (_m, _b, q) => page(F.deals.filter((d) => d.cadastral === q.get('cadastral'))));
on('GET', '/deals', (_m, _b, q) => {
  const u = currentUser();
  let ds = F.deals.filter((d) => isStaff(u) || d.agent_id === u.id || d.recruit_id === u.id);
  if (q.get('month')) ds = ds.filter((d) => d.month === q.get('month'));
  if (q.get('agent_id')) ds = ds.filter((d) => d.agent_id === Number(q.get('agent_id')));
  if (q.get('status')) ds = ds.filter((d) => d.status === q.get('status'));
  return page([...ds].sort((a, b) => (b.created_at || '').localeCompare(a.created_at || '')));
});
on('POST', '/deals', (_m, b) => {
  const u = currentUser();
  const c = calc(b);
  const now = new Date();
  const d: Deal = { id: nextId(), status: 'pending', source: 'crm', created_at: now.toISOString(), month: `${now.getMonth() + 1}/${now.getFullYear()}`, ...(b as Partial<Deal>), ...c } as Deal;
  d.agent = F.ref(d.agent_id || u.id); d.agent_id = d.agent_id || u.id;
  if (d.recruit_id) d.recruit = F.ref(d.recruit_id);
  F.deals.unshift(d);
  if (d.lead_id) { const l = F.leads.find((x) => x.id === d.lead_id); if (l) { l.deal_id = d.id; setLeadStatus(l, 'deal_pending'); } }
  return d;
});
on('GET', '/deals/:id', (m) => F.deals.find((d) => d.id === Number(m[1])));
on('POST', '/deals/:id/confirm', (m) => {
  const u = currentUser();
  const d = F.deals.find((x) => x.id === Number(m[1]))!;
  d.status = 'confirmed'; d.confirmed_at = new Date().toISOString(); d.t14_status = 'pending';
  if (d.rent_from) d.t14_date = new Date(new Date(d.rent_from).getTime() + 14 * 86400_000).toISOString().slice(0, 10);
  const ag = F.users.find((x) => x.id === d.agent_id);
  if (ag && d.is_exclusive) { ag.exclusive_deals += 1; ag.level = levelFor(ag.exclusive_deals); }
  if (d.lead_id) { const l = F.leads.find((x) => x.id === d.lead_id); if (l) { setLeadStatus(l, 'rented'); leadEvent(l, 'deal_confirmed', 'Сделка подтверждена · #Rented', u); } }
  return d;
});
on('POST', '/deals/:id/cancel', (m) => { const d = F.deals.find((x) => x.id === Number(m[1]))!; d.status = 'cancelled'; return d; });

on('POST', '/evaluations', (_m, b) => { const e = { id: nextId(), created_at: new Date().toISOString(), author: 'agent', ...b }; F.evaluations.push(e as never); const d = F.deals.find((x) => x.id === b.deal_id); if (d && b.kind === 't14') d.t14_status = 'done'; return e; });
on('GET', '/evaluations', (_m, _b, q) => page(F.evaluations.filter((e) => e.deal_id === Number(q.get('deal_id')))));

// ---------- reports ----------
on('POST', '/reports/:kind', (m, b) => {
  const u = currentUser();
  const recruitId = b.recruit_id ? Number(b.recruit_id) : null;
  const r: Report = {
    id: nextId(), kind: m[1], author_id: u.id, author: F.ref(u.id), recruit_id: recruitId, recruit: F.ref(recruitId),
    mentor_id: b.mentor_id ? Number(b.mentor_id) : null, date: (b.date as string) || new Date().toISOString().slice(0, 10),
    data: b, created_at: new Date().toISOString(), dup_check: m[1] === 'makefoto' ? (String(b.phone || '').endsWith('0') ? 'exists' : 'added') : null,
  };
  F.reports.unshift(r);
  return r;
});
on('GET', '/reports', (_m, _b, q) => {
  let rs = F.reports;
  if (q.get('kind')) rs = rs.filter((r) => r.kind === q.get('kind'));
  if (q.get('author_id')) rs = rs.filter((r) => r.author_id === Number(q.get('author_id')));
  return page(rs);
});
on('GET', '/counters', (_m, _b, q) => page(counters(q.get('month') || undefined)));

// ---------- analytics ----------
on('GET', '/analytics/dashboard', () => {
  const props: Record<string, number> = {};
  F.properties.forEach((p) => { props[p.status] = (props[p.status] || 0) + 1; });
  const lds: Record<string, number> = {};
  F.leads.forEach((l) => { lds[l.status] = (lds[l.status] || 0) + 1; });
  const conf = F.deals.filter((d) => d.status === 'confirmed');
  return {
    deals_count: 17 + conf.length,
    deals_month: conf.length,
    platform_commission: conf.reduce((s, d) => s + (d.platform_amount || 0), 0) + 4120,
    active_objects: 312,
    reposts: 86,
    leads_open: F.leads.filter((l) => !['rented', 'refused'].includes(l.status)).length,
    sla_overdue: F.leads.filter((l) => l.sla_due_at && new Date(l.sla_due_at).getTime() < Date.now()).length,
    deals_plan: Number(F.settings.deals_plan) || 40,
    top_agents: agentStats().slice(0, 5),
    telegram_top: [
      { name: 'Ваке · 2 спальни · $850', value: 1840 }, { name: 'Сабуртало · студия · $500', value: 1522 },
      { name: 'Вере · 3 спальни · $1 500', value: 1207 }, { name: 'Мтацминда · пентхаус · $2 400', value: 1103 },
      { name: 'Ортачала · 2 спальни · $700', value: 990 }, { name: 'Дидубе · 1 спальня · $550', value: 874 },
      { name: 'Ваке · 3 спальни · $1 300', value: 812 }, { name: 'Исани · студия · $420', value: 640 },
      { name: 'Сабуртало · 2 спальни · $750', value: 611 }, { name: 'Глдани · 1 спальня · $400', value: 515 },
    ],
    ticker: F.ticker.filter((t) => t.status === 'active'),
    funnel: { properties: props, leads: lds },
    response_times: { operator_take_avg_min: 3.4, agent_slots_avg_min: 21.7 },
  };
});
on('GET', '/analytics/agents', () => page(agentStats()));
on('GET', '/analytics/channel-posts', () => ({ total: 9, items: [
  { agent_id: 2, name: 'Георгий', posts: 5, exclusive: 2, last: new Date().toISOString() },
  { agent_id: null, name: 'GiorgiSV', posts: 4, exclusive: 0, last: new Date().toISOString() }] }));
on('GET', '/analytics/funnel', () => ({}));
on('GET', '/analytics/site', (_m, _b, q) => {
  const days = Number(q.get('days')) || 7;
  const k = days / 7;
  const n = (x: number) => Math.round(x * k);
  const today = Date.now();
  const daily = Array.from({ length: days }, (_, i) => {
    const d = new Date(today - (days - 1 - i) * 864e5).toISOString().slice(0, 10);
    const v = 60 + Math.round(40 * Math.sin(i / 2) + (i % 7 === 5 ? 35 : 0));
    return { d, visitors: v, sessions: Math.round(v * 1.3), contacts: Math.round(v / 14), views: v * 3 };
  });
  const L = (id: string, service: string, agent: string, viewers: number, contacts: number, favs = 0) =>
    ({ id, service, agent, views: viewers + 7, viewers, contacts, shares: Math.round(contacts / 2), favs, conversion: viewers ? Math.round(contacts / viewers * 1000) / 10 : 0 });
  const listings = [L('rent-548893', 'rent', 'David_Tibelashvili', n(84), n(9), 6), L('rent-548711', 'rent', 'kvirkvel1a', n(61), n(5), 3),
    L('sale-2603', 'sale', 'David_Tibelashvili', n(47), n(2), 4), L('daily-710', 'daily', '', n(39), 0, 5), L('commercial-2718', 'commercial', 'Dima', n(22), 0, 1)];
  return {
    period: { from: today - days * 864e5, to: today, days },
    totals: { visitors: n(512), newVisitors: n(398), sessions: n(690), pageviews: n(2840), listingViews: n(1630), contacts: n(41), contactSessions: n(36), conversion: 5.2, shares: n(27), favs: n(64) },
    prev: { visitors: n(455), newVisitors: n(361), sessions: n(610), pageviews: n(2500), listingViews: n(1420), contacts: n(33), contactSessions: n(30), conversion: 4.9, shares: n(30), favs: n(51) },
    daily,
    contactsBy: { whatsapp: n(18), telegram: n(12), phone: n(9), office: n(2) },
    sources: [{ name: 'instagram', sessions: n(240), contacts: n(15), conversion: 6.3 }, { name: 'direct', sessions: n(190), contacts: n(9), conversion: 4.7 },
      { name: 'google', sessions: n(130), contacts: n(8), conversion: 6.2 }, { name: 'telegram', sessions: n(90), contacts: n(6), conversion: 6.7 }, { name: 'share', sessions: n(40), contacts: n(3), conversion: 7.5 }],
    campaigns: [{ name: 'reels-vake-2br', source: 'instagram', sessions: n(88), contacts: n(7), conversion: 8 }, { name: 'whatsapp', source: 'share', sessions: n(25), contacts: n(2), conversion: 8 }],
    listings, bestListings: listings.filter((x) => x.contacts > 0), noResponse: listings.filter((x) => x.contacts === 0 && x.viewers >= 5),
    agents: [{ name: 'David_Tibelashvili', listings: 2, views: n(145), contacts: n(11), conversion: 7.6 }, { name: 'kvirkvel1a', listings: 1, views: n(68), contacts: n(5), conversion: 7.4 }, { name: '—', listings: 1, views: n(46), contacts: 0, conversion: 0 }],
    searches: [{ name: 'rent · district=Vake · beds=2', n: n(31) }, { name: 'rent · price=-800', n: n(22) }, { name: 'daily · district=Old Tbilisi', n: n(14) }],
    zeroSearches: [{ name: 'rent · beds=4 · district=Vake · pets=1', n: n(6) }, { name: 'sale · price=-40000 · district=Saburtalo', n: n(3) }],
    sharesBy: { telegram: n(11), whatsapp: n(9), copy: n(7) },
    socialBy: { instagram: n(14), 'telegram:rent_tbilisi_ge': n(9), facebook: n(4) },
    devices: { mobile: n(470), desktop: n(190), tablet: n(30) }, langs: { ru: n(410), en: n(220), ka: n(60) }, themes: { light: n(520), dark: n(170) },
    errors: [{ name: "Cannot read properties of undefined (reading 'photos')", n: 3, sessions: 2, page: '#/listing/rent-548711', device: 'mobile', last: today - 36e5 }],
    perf: { loadMedian: 1840, loadP75: 3120, lcpMedian: 1520, lcpP75: 2610, n: n(600), byDevice: { mobile: { median: 2100, p75: 3400, n: n(420) }, desktop: { median: 1200, p75: 1900, n: n(180) } } },
  };
});
// «Статистика» (11-13): a plausible month of the old workbook's blocks
on('GET', '/analytics/stats', (_m, _b, q) => {
  const [y, mo] = (q.get('month') || '2025-10').split('-').map(Number);
  const trend = Array.from({ length: 12 }, (_, i) => {
    const k = y * 12 + mo - 1 - (11 - i); const r = 90 + ((i * 37) % 50);
    return { month: `${Math.floor(k / 12)}-${String((k % 12) + 1).padStart(2, '0')}`, objects: 120 + ((i * 53) % 180), clients: 280 + ((i * 41) % 110),
      budget_sum: 150000 + i * 4000, rentouts: r, commission: r * 410, platform: r * 60, agents: r * 350, obj_median: 700 + ((i * 29) % 120), budget_median: 560 + ((i * 17) % 90) };
  });
  const names = ['Давид', 'Ираклий', 'Виктория', 'Семён', 'Омари', 'Заза', 'Влад', 'Шафи'];
  const agents = names.map((name, i) => {
    const rentouts = 18 - i * 2, clients = 26 - i * 2, objects = 30 - i * 3, commission = rentouts * 480;
    return { id: i + 1, name, objects, ppm: 10 + i * 0.4, clients, rentouts, commission, agent_amount: Math.round(commission * 0.7), platform: Math.round(commission * 0.3),
      share: 70, obj_conv: Math.round((rentouts / objects) * 100), client_conv: Math.round((rentouts / clients) * 100), quality: Math.round(((rentouts / objects + rentouts / clients) / 2) * 100) };
  });
  const cell = (m: number, n: number) => ({ count: n, median: m, ppm: Math.round((m / 55) * 10) / 10 });
  const row = (district: string, k: number) => ({ district, count: 4 * k + 6, cells: { 0: cell(400 + 20 * k, k), 1: cell(550 + 30 * k, 2 * k), 2: cell(800 + 40 * k, k + 3), 3: cell(1200 + 60 * k, 2), 4: cell(1900, 1) } });
  const last = trend[11], before = trend[10];
  const mom = (a: number, b: number) => Math.round(((a - b) / b) * 100);
  return {
    month: last.month, trend, agents,
    summary: { objects: last.objects, objects_sale: 22, objects_avg_price: 740, objects_avg_ppm: 10.8, clients: last.clients, clients_avg_budget: 640, clients_with_budget: 272,
      budget_sum: last.budget_sum, rentouts: last.rentouts, commission: last.commission, platform: last.platform, agents: last.agents,
      by_type: { rent: { count: last.rentouts - 10, commission: last.commission - 8000, avg_price: 690 }, sale: { count: 10, commission: 8000, avg_price: 120000 } },
      mom: { objects: mom(last.objects, before.objects), clients: mom(last.clients, before.clients), rentouts: mom(last.rentouts, before.rentouts), commission: mom(last.commission, before.commission) } },
    sources: [{ source: 'Телеграмме', count: 296 }, { source: 'MyHome.ge | Ss.ge', count: 83 }, { source: 'Рекомендация', count: 4 }, { source: 'Instagram', count: 3 }],
    prices: { objects: [row('Сабуртало', 9), row('Ваке', 6), row('Вере', 4), row('Чугурети', 3), row('Мтацминда', 2)], market: [] },
  };
});

// ---------- content / admin ----------
on('GET', '/ticker', () => page(F.ticker.filter((t) => t.status === 'active')));
on('POST', '/ticker', (_m, b) => { const t = { id: nextId(), text: String(b.text), status: 'active' }; F.ticker.push(t); return t; });
on('DELETE', '/ticker/:id', (m) => { const t = F.ticker.find((x) => x.id === Number(m[1])); if (t) t.status = 'deleted'; return { ok: true }; });
on('GET', '/news', () => page([
  { id: 1, date: new Date().toISOString().slice(0, 10), text: 'Новые правила публикации: без проверки оператором в канал ничего не уходит.' },
  { id: 2, date: new Date(Date.now() - 3 * 86400_000).toISOString().slice(0, 10), text: 'Запущена воронка клиентов в боте: заявки приходят в общую группу операторов.' },
]));
// Управление → Ключи (backend app/keys.py)
const mockKeys: Record<string, string> = { OPENAI_BASE_URL: 'https://openrouter.ai/api/v1', LLM_MODEL: 'google/gemma-4-31b-it' };
const ENV_KEYS: Record<string, string> = { OPENAI_API_KEY: 'sk-or-v1-0000000000000000a6e0', APIFY_TOKENS: 'apify_api_000000000000T4n0, apify_api_111111111111Zz99', MAPTILER_KEY: 'mt00000000000000evv8' };
const KEY_NOTES: Record<string, [string, string]> = { OPENAI_API_KEY: ['Нейросеть: ключ API', 'Ключ OpenRouter или другого OpenAI-совместимого API'],
  OPENAI_BASE_URL: ['Нейросеть: адрес API', 'Например https://openrouter.ai/api/v1'], LLM_MODEL: ['Нейросеть: модель', 'Например google/gemma-4-31b-it'],
  APIFY_TOKENS: ['Apify: ключи для парсера Facebook', 'Можно несколько через запятую, берутся по очереди'], APIFY_TOKEN: ['Apify: один ключ', 'Если список выше пустой'],
  MAPTILER_KEY: ['Карты MapTiler', 'Подложка карт в CRM и на сайте'] };
const isSecret = (n: string) => /KEY|TOKEN|SECRET|PASS|SESSION|HASH/.test(n);
const maskV = (v: string) => v.split(',').map((p) => p.trim()).filter(Boolean).map((p) => (p.length >= 16 ? `${p.slice(0, 4)}…${p.slice(-4)}` : '••••')).join(', ');
const keyVal = (n: string) => mockKeys[n] ?? ENV_KEYS[n];
const keysOut = () => ({ items: [...new Set([...Object.keys(KEY_NOTES), ...Object.keys(mockKeys).sort()])].map((name) => {
  const v = keyVal(name);
  return { name, title: KEY_NOTES[name]?.[0] ?? null, note: KEY_NOTES[name]?.[1] ?? null, source: mockKeys[name] ? 'crm' : v ? 'env' : null,
    secret: isSecret(name), value: v ? (isSecret(name) ? maskV(v) : v) : null,
    changed_at: mockKeys[name] ? '2026-11-16T14:05:00Z' : null, changed_by: mockKeys[name] ? 'Станислав' : null };
}) });
const llm = (calls: number, usd: number) => ({ calls, prompt: calls * 7000, completion: calls * 450, usd, estimated: true, unpriced: [],
  models: [{ model: 'google/gemma-4-31b-it', calls, tokens: calls * 7450, usd }] });
on('GET', '/keys', () => keysOut());
on('GET', '/keys/:name/value', (m) => ({ name: m[1], value: keyVal(m[1]) }));
on('POST', '/keys/:name/check', (m) => ({ results: m[1].startsWith('APIFY') ? [{ label: 'apif…T4n0', ok: true, ms: 312, error: null }, { label: 'apif…Zz99', ok: false, ms: 280, error: 'Ключ не работает' }]
  : [{ label: 'google/gemma-4-31b-it', ok: true, ms: 840, error: null }] }));
on('GET', '/spend', () => ({ apify: { today_usd: 0, week_usd: 0.01, runs_week: { facebook: 41, threads: 7, ss: 20 },
  accounts: [{ tail: 'T4n0', masked: 'apif…T4n0', plan: 'FREE', created_at: '2026-10-30', cycle_start: '2026-10-30', cycle_end: '2026-11-29',
    used_usd: 5.01, limit_usd: 5, exhausted: true, exhausted_since: '2026-11-16', error: null,
    days: [['10-30', 1.04], ['11-04', 0.72], ['11-10', 1.07], ['11-11', 0.66], ['11-12', 0.25], ['11-13', 0.28], ['11-14', 0.21], ['11-15', 0.26], ['11-16', 0.24], ['11-20', 0.01]]
      .map(([d, usd]) => ({ date: `2026-${d}`, usd: usd as number })),
    by_source: [{ source: 'facebook', runs: 120, usd: 3.9 }, { source: 'ss', runs: 60, usd: 0.7 }, { source: 'threads', runs: 14, usd: 0.4 }] }, { tail: 'Zz99', masked: 'apif…Zz99', used_usd: null, limit_usd: null, cycle_end: null, error: 'invalid_key' }] },
  ai: { mode: 'openai', provider: 'OpenRouter', base_url: 'https://openrouter.ai/api/v1', model: 'google/gemma-4-31b-it',
    today: llm(266, 0.2131), week: llm(266, 0.2131), month: llm(266, 0.2131), balance: { balance_usd: 9.79, used_usd: 0.21 } } }));
on('PUT', '/keys/:name', (m, b) => { mockKeys[m[1]] = String(b.value); return keysOut(); });
on('DELETE', '/keys/:name', (m) => { delete mockKeys[m[1]]; return keysOut(); });
on('GET', '/settings', () => F.settings);
on('PUT', '/settings', (_m, b) => Object.assign(F.settings, b));
on('GET', '/sources', () => page(F.sources));
on('POST', '/sources', (_m, b) => { const s = { id: nextId(), enabled: true, ...b } as never; F.sources.push(s); return s; });
on('PATCH', '/sources/:id', (m, b) => { const s = F.sources.find((x) => String(x.id) === m[1])!; Object.assign(s, b); return s; });
on('GET', '/moderation', () => page(F.moderation));
on('POST', '/moderation/:id', (m) => { const i = F.moderation.findIndex((x) => x.id === m[1]); if (i >= 0) F.moderation.splice(i, 1); return { ok: true }; });
on('GET', '/addresses', () => page([]));

// ---------- NDA: one unsigned agreement and a few clauses, for the per-agent text editor (Ndas.tsx)
const nda = { id: 9101, status: 'sent', status_label: 'Отправлено', lang: 'KA-RU', full_name: 'Nino Beridze', personal_id: '01001055555', phone: null,
  telegram: 'nino_agent', role: 'Рекрут', path: '/nda/mock-token-0000000000000', created_at: new Date().toISOString(), created_by: { id: 1, name: 'Давид' },
  signed_at: null, tg_username: null, has_pdf: false, number: '2026-9101', details: {} as Record<string, unknown> };
const ndaBlocks = [{ k: 'h', n: '1', ka: 'ტერმინები', ru: 'Термины', en: 'Terms' },
  { k: 'n', n: '1.1', ka: 'პლატფორმა — ოპერატორის ღრუბლოვანი სისტემა.', ru: 'Платформа — облачная система Оператора.', en: 'Platform means the Operator’s cloud system.' },
  { k: 'n', n: '1.2', ka: 'მონაწილე — {name_ka}, პირადი ნომერი {pid}.', ru: 'Участник — {name2}, личный номер {pid}.', en: 'Participant means {name2}, personal number {pid}.' },
  { k: 't', head: [{ ka: 'დონე', ru: 'Уровень', en: 'Level' }], rows: [[{ ka: '25', ru: '25', en: '25' }]] }];
const ndaSigned = { ...nda, id: 9102, status: 'signed', status_label: 'Подписано', number: '2026-9102', full_name: 'Giorgi Lomidze', path: null, telegram: 'gio_l',
  signed_at: new Date().toISOString(), tg_username: 'gio_l', has_pdf: true, user_id: 8, details: { level: '50', iban_gel: 'GE29TB0000000000000001', address: 'Tbilisi' } };
on('GET', '/ndas', () => page([nda, ndaSigned].map((x) => ({ ...x, edition: (x.details as { edition?: string }).edition || '1.3' }))));
// team members without a signed agreement: one with no NDA (complete profile), one missing the personal number
const pre = (id: number, full_name: string, personal_id: string, role: string) => ({ user_id: id, lang: 'KA-RU', full_name, full_name_ka: '', personal_id, ie: '', address: '', phone: '+995 555 000 111', email: '', telegram: '', role, level: role === 'recruit' ? '25' : '50', access: '2026-10-01', iban_gel: '', iban_usd: '' });
on('GET', '/ndas/team', () => ({ signed: 3, total: 6, items: [
  { user_id: 7, name: 'Ника', role: 'recruit', level: 25, username: 'nika', can_send: true, missing: ['ie', 'email'], ready: true, status: 'none', nda: null, prefill: pre(7, 'Nika Kapanadze', '01001000007', 'recruit') },
  { user_id: 8, name: 'Георгий', role: 'agent', level: 50, username: null, can_send: false, missing: ['personal_id', 'ie'], ready: false, status: 'none', nda: null, prefill: pre(8, 'Giorgi Lomidze', '', 'agent') },
  { user_id: 9, name: 'Нино', role: 'agent', level: 70, username: 'nino_agent', can_send: true, missing: [], ready: true, status: 'sent', nda: { ...nda, user_id: 9 }, prefill: pre(9, 'Nino Beridze', '01001055555', 'agent') }] }));
on('POST', '/ndas/team/send-all', () => ({ made: 1, sent: 1, not_sent: [] }));
on('POST', '/ndas/:id/send', () => ({ sent: false, url: `${location.origin}${nda.path}`, path: nda.path }));
const tpl = { edition: '1.3', blocks: ndaBlocks as unknown[], history: [{ edition: '1.3', at: null as string | null, by_name: null as string | null, changes: [] as string[], note: '', default: true, from: null as string | null }] };
const tplOut = () => ({ ...tpl, default: tpl.edition === '1.3', next: tpl.edition.replace(/\d+$/, (x) => String(+x + 1)), code_edition: '1.3', history: [...tpl.history].reverse() });
on('GET', '/ndas/template', tplOut);
on('PUT', '/ndas/template', (_m, b) => {
  const ed = (b.edition as string) || tplOut().next;
  const from = (b.from_edition as string) || (b.blocks === null ? '1.3' : null);
  tpl.blocks = (b.blocks as unknown[]) || ndaBlocks;
  tpl.history.push({ edition: ed, at: new Date().toISOString(), by_name: 'Давид', changes: from ? [] : ['1.2'], note: (b.note as string) || '', default: false, from });
  tpl.edition = ed;
  return tplOut();
});
on('POST', '/ndas/template/preview', (_m, b) => ({ html: `<h1>Preview ${b.lang} · ${b.edition}</h1>${(b.blocks as { n?: string; ka?: string; ru?: string }[]).map((x) => `<p>${x.n || ''} ${x.ka || ''} | ${x.ru || ''}</p>`).join('')}` }));
on('GET', '/ndas/company-signature', () => ({ signature: null, at: null, by_name: null }));
on('GET', '/ndas/:id/text', () => ({ blocks: nda.details.blocks || ndaBlocks, edited: !!nda.details.blocks, edition: (nda.details.edition as string) || '1.3', current: tpl.edition, lang: nda.lang, status: nda.status }));
on('PUT', '/ndas/:id/text', (_m, b) => { nda.details = b.blocks ? { blocks: b.blocks } : {}; return nda; });
on('POST', '/ndas/:id/text/preview', (_m, b) => ({ html: `<h1>Preview</h1>${(b.blocks as { n?: string; ka?: string; ru?: string }[]).map((x) => `<p>${x.n || ''} ${x.ka || ''} | ${x.ru || ''}</p>`).join('')}` }));

// ---------- contracts: just the wizard's autosave and the card (no PDF in the mock); 11-13
// one generated rent contract with a saved registry extract: the card, e-sign and /esign/mock-owner-9001-… (Irakli 11-13)
const contracts: Record<string, unknown>[] = [{ id: 9001, kind: 'rent', lang: 'EN', lead_id: null, deal_id: null, cadastral: '01.10.14.016.017.01.001',
  address: 'თბილისი, ტაბიძის ქ. 43', has_pdf: true, status_label: 'Сгенерирован', filename: 'contract.pdf', created_at: new Date().toISOString(),
  generated_at: new Date().toISOString(), data: { extract_file: 'private/registry-extracts/mock.pdf'} }];
// a contract signed from the object card, no lead (Irakli 11-18: «Оформить сделку» on it)
const cardProp = F.properties.find((p) => p.agent_id === 3 && p.status === 'published') || F.properties[0];
contracts.push({ id: 9002, kind: 'rent', lang: 'RU', status: 'signed', status_label: 'Договор подписан', lead_id: null, deal_id: null, property_id: cardProp.id,
  address: cardProp.address, client_name: 'Grishin Andrei', owner_name: cardProp.owner?.name || null, has_pdf: true, filename: 'contract.pdf',
  created_by: F.ref(3), created_at: new Date().toISOString(), generated_at: new Date().toISOString(), signed_at: new Date().toISOString(), signed_by: F.ref(3),
  data: { price_usd: String(cardProp.price || 1200), start_rent: '2026-12-01', end_rent: '2027-12-01', client_full_name: 'Grishin Andrei' } });
const cxOut = (c: Record<string, unknown>) => ({ status: 'draft', status_label: 'Черновик', has_pdf: false, versions: [], versions_count: 0, ...c,
  lead_deal_id: (c.lead_id && F.leads.find((l) => l.id === c.lead_id)?.deal_id) || null });
on('GET', '/contracts', (_m, _b, q) => page(contracts.filter((c) => (!q.get('lead_id') || c.lead_id === Number(q.get('lead_id')))
  && (!q.get('deal_id') || c.deal_id === Number(q.get('deal_id'))) && (!q.get('property_id') || c.property_id === Number(q.get('property_id')))
  && (!q.get('status') || q.get('status') === 'all' || String(q.get('status')).replace('active', 'draft,signed').split(',').includes(String(c.status || 'draft')))).map(cxOut)));
on('POST', '/contracts/translate', (_m, b) => Object.fromEntries((b.to as string[]).map((l) => [l, `[${l}] ${b.text}`])));  // no model in the mock
on('POST', '/contracts/draft', (_m, b) => {
  let c = contracts.find((x) => x.id === b.id);
  if (!c) { c = { id: nextId(), lead_id: b.lead_id || null, deal_id: b.deal_id || null, property_id: b.property_id || null, created_at: new Date().toISOString() }; contracts.push(c); }
  Object.assign(c, { kind: b.kind || null, lang: b.lang || null, data: b.data, updated_at: new Date().toISOString() });
  return cxOut(c);
});
on('GET', '/contracts/:id', (m) => { const c = contracts.find((x) => x.id === Number(m[1])); if (!c) throw new ApiError(404, 'Не найдено'); return cxOut(c); });
// «Оформить сделку» on a signed contract without a lead: a client + lead from the contract (services/contracts.deal_lead)
on('POST', '/contracts/:id/deal-lead', (m) => {
  const c = contracts.find((x) => x.id === Number(m[1]))!;
  if (c.lead_id) return cxOut(c);
  const cl = { id: nextId(), name: String(c.client_name || ''), phone: '+995 599 12 34 56', lang: 'ru', profile_done: 0, created_at: new Date().toISOString(), profile: {} } as Client;
  F.clients.push(cl);
  const p = c.property_id ? getProp(Number(c.property_id)) : null;
  const agent = (c.created_by as { id: number } | undefined)?.id ?? null;
  const lead = { id: nextId(), client_id: cl.id, listing_id: null, property_id: p?.id ?? null, status: 'contract', agent: F.ref(agent), agent_id: agent,
    contacts_shared_at: new Date().toISOString(), created_at: new Date().toISOString(), slots: [], deal_id: null,
    listing: p ? { id: `obj${p.id}`, title: p.address, address: p.address, price: p.price, district: p.district } : null } as unknown as Lead;
  F.leads.unshift(lead);
  c.lead_id = lead.id;
  return cxOut(c);
});
on('GET', '/leads/:id/deal-prefill', (m) => {
  const c = contracts.find((x) => x.lead_id === Number(m[1]) && x.status === 'signed');
  const d = (c?.data || {}) as Record<string, string>;
  return c ? { contract: { id: c.id, status: c.status }, fields: { deal_type: 'rent', sub_type: 'long', price: Number(d.price_usd) || null, client_name: c.client_name,
    address: c.address, rent_from: d.start_rent, rent_to: d.end_rent } } : { contract: null, fields: {} };
});
on('POST', '/contracts/:id/link', (m, b) => { const c = contracts.find((x) => x.id === Number(m[1]))!; c.lead_id = b.lead_id; return cxOut(c); });
// «Сохранить PDF» / «Обновить PDF» and «Отправить клиенту в Telegram» (11-13): a lead's client is reachable, else the typed recipient
const cxGen = (c: Record<string, unknown>, b: Record<string, unknown>) => Object.assign(c, { ...b, html: undefined, has_pdf: true, status_label: 'Сгенерирован',
  generated_at: new Date().toISOString(), filename: 'contract.pdf', client_tg: b.lead_id ? { label: 'Анна (@anna_demo)' } : null });
on('POST', '/contracts', (_m, b) => { const c = cxGen({ id: nextId(), created_at: new Date().toISOString() }, b); contracts.push(c); return cxOut(c); });
on('PUT', '/contracts/:id', (m, b) => {
  const c = contracts.find((x) => x.id === Number(m[1]))!;
  const vs = (c.versions as unknown[] | undefined) || [];
  cxGen(c, { ...b, versions: c.has_pdf ? [...vs, { n: vs.length, at: c.generated_at, by: null }] : vs });
  return cxOut(c);
});
on('POST', '/contracts/:id/send-client', (m, b) => {
  const c = contracts.find((x) => x.id === Number(m[1]))!;
  const to = String(b.to || '').trim() || (c.client_tg as { label: string } | null)?.label;
  if (!to) throw new ApiError(409, 'У клиента нет Telegram: укажите @username или телефон получателя');
  c.sent_to_client = { at: new Date().toISOString(), by: 'Демо', to, version: ((c.versions as unknown[]) || []).length + 1,
    extract: !!(c.data as Record<string, unknown> | undefined)?.extract_file };
  return cxOut(c);
});
// electronic signing (services/esign.py): a token per party, the public page /esign/<token>; 11-13
const ES_LABEL = { owner: { ka: 'გამქირავებელი', ru: 'Арендодатель', en: 'Owner' }, tenant: { ka: 'დამქირავებელი', ru: 'Арендатор', en: 'Tenant' } };
type MockSigner = { role: 'owner' | 'tenant'; label: string; path: string | null; signed_at: string | null; full_name: string | null };
on('POST', '/contracts/:id/esign', (m) => {
  const c = contracts.find((x) => x.id === Number(m[1]))!;
  c.esign = { complete: false, doc_sha256: 'mock', signers: (['owner', 'tenant'] as const).map((role) => ({
    role, label: role === 'owner' ? 'Собственник' : 'Арендатор', path: `/esign/mock-${role}-${c.id}-xxxxxxxxxxxxxxxx`, signed_at: null, full_name: null })) };
  return cxOut(c);
});
on('POST', '/contracts/:id/esign/cancel', (m) => { const c = contracts.find((x) => x.id === Number(m[1]))!; c.esign = null; return cxOut(c); });
const esFind = (token: string) => {
  const [, role, id] = token.split('-');
  const c = contracts.find((x) => x.id === Number(id)) || { id: Number(id) || 1, lang: 'EN', address: 'Тбилиси, ул. Демо, 1' };
  const signers = ((c.esign as { signers: MockSigner[] } | undefined)?.signers) || [];
  if (!ES_LABEL[role as 'owner']) throw new ApiError(404, 'Ссылка недействительна. Попросите агента прислать новую', 'not_found');
  return { c, s: signers.find((x) => x.role === role), role: role as 'owner' | 'tenant', signers };
};
const esView = (token: string) => {
  const { c, s, role, signers } = esFind(token);
  return { role, role_label: ES_LABEL[role], contract_id: c.id, kind: 'rent', lang: c.lang || 'EN', address: c.address || 'Тбилиси, ул. Демо, 1', cadastral: null,
    doc_sha256: 'mock', prefill: { full_name: '', personal_id: '' }, signed_at: s?.signed_at || null, full_name: s?.full_name || null,
    parties: (['owner', 'tenant'] as const).map((r) => ({ role: r, label: ES_LABEL[r], signed: !!signers.find((x) => x.role === r)?.signed_at })),
    complete: false, has_extract: !!(c as { data?: Record<string, unknown> }).data?.extract_file, html: s?.signed_at ? null : '<html><head></head><body><p>ხელშეკრულება / Lease agreement (mock)</p><p>________________________</p></body></html>' };
};
// the agent's signing page /nda/mock-token-…: Telegram confirmed (test mode), IBAN / ИП filled by the agent
const ndaPub = { lang: 'KA-RU', status: 'sent', full_name: 'Nino Beridze', personal_id: '01001055555', phone: null, telegram: 'nino_agent', role: 'Рекрут',
  doc_sha256: 'mock', confirmed: true, tg_username: 'nino_agent', tg_name: 'Nino', signed_at: null, has_pdf: false, test_mode: true,
  html: '<h1 style="text-align:center">შეთანხმება · Соглашение</h1><p>0.2 Индивидуальный предприниматель <b>Nino Beridze</b>, личный номер <b>01001055555</b> …</p>',
  details: { iban_gel: '', iban_usd: '', ie: '' } };
on('GET', '/public/nda/:token', () => ndaPub);
on('POST', '/public/nda/:token/details', (_m, b) => {
  const c = (k: string) => String(b[k] || '').replace(/[\s-]/g, '').toUpperCase();
  ndaPub.details = { iban_gel: c('iban_gel'), iban_usd: c('iban_usd'), ie: c('ie') };
  ndaPub.html = ndaPub.html.replace(/<p>IBAN.*$/, '') + `<p>IBAN GEL: <b>${ndaPub.details.iban_gel || '—'}</b> · IBAN USD: <b>${ndaPub.details.iban_usd || '—'}</b> · ИП: <b>${ndaPub.details.ie || '—'}</b></p>`;
  return ndaPub;
});
// ---------- «Документ на подпись» (pages/SignDocs.tsx, the signing page /doc/<token>; backend services/sign_docs.py)
type SdSigner = { id: number; side: 'party' | 'company'; user_id: number | null; full_name: string; personal_id: string | null; phone: string | null;
  telegram: string | null; token: string; tg_username: string | null; signed_at: string | null };
type SdDoc = { id: number; status: 'draft' | 'sent' | 'signed' | 'cancelled'; lang: 'KA-RU' | 'KA' | 'RU'; title: Record<string, string>;
  body: Record<string, string>; signers: SdSigner[]; created_at: string; sent_at: string | null; signed_at: string | null; confirmed: Record<string, boolean> };
const SD_LABEL = { draft: 'Черновик', sent: 'Отправлен', signed: 'Подписан', cancelled: 'Отменён' };
const sdLns = (d: SdDoc) => (d.lang === 'KA-RU' ? ['ka', 'ru'] : d.lang === 'KA' ? ['ka'] : ['ru']);
const sdNum = (d: SdDoc) => `D-2026-${String(d.id).padStart(4, '0')}`;
const sdEsc = (x: string) => x.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const sdHtml = (d: SdDoc) => {
  const paras = (ln: string) => (d.body[ln] || '').split(/\n\s*\n/).filter((x) => x.trim()).map((x) => `<p>${sdEsc(x.trim()).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').replace(/\n/g, '<br>')}</p>`).join('');
  const cols = sdLns(d).map((ln) => `<td style="vertical-align:top;width:50%">${paras(ln)}</td>`).join('');
  const rows = [['Давид Тибелашвили', '1'], ...d.signers.map((s) => [s.full_name, s.side === 'party' ? '2' : '1'])];
  return `<html><head></head><body style="font-family:sans-serif;font-size:13px">${sdLns(d).map((ln) => `<h2 style="text-align:center;color:#1D3560;margin:0">${sdEsc(d.title[ln] || '')}</h2>`).join('')}`
    + `<p style="text-align:center;color:#666">Homes in Georgia · № ${sdNum(d)} (mock)</p><table style="width:100%"><tr>${cols}</tr></table>`
    + `<h3>${d.lang === 'RU' ? 'Подписи Сторон' : d.lang === 'KA' ? 'მხარეთა ხელმოწერები' : 'მხარეთა ხელმოწერები / Подписи Сторон'}</h3><table border="1" style="border-collapse:collapse;width:100%">${rows.map(([n, side]) => `<tr><td>${sdEsc(n)}<br><small>${side}</small></td><td>${d.status === 'signed' ? '✍️' : '[эл. подпись]'}</td></tr>`).join('')}</table></body></html>`;
};
// kept in localStorage: the signing link opens a new page (a reload would lose an in-memory list)
const SD_KEY = 'mock_sign_docs';
const sdDocs: SdDoc[] = (() => { try { return JSON.parse(localStorage.getItem(SD_KEY) || '[]'); } catch { return []; } })();
let sdNext = Math.max(0, ...sdDocs.map((d) => d.id)) + 1;
const sdSave = <T>(x: T): T => { try { localStorage.setItem(SD_KEY, JSON.stringify(sdDocs)); } catch { /* private mode */ } return x; };
const sdPerson = (p: Record<string, unknown> | null | undefined, side: SdSigner['side'], old?: SdSigner): SdSigner | null => {
  if (!p || (!p.user_id && !String(p.full_name || '').trim())) return null;
  const u = p.user_id ? F.users.find((x) => x.id === Number(p.user_id)) : undefined;
  const full_name = String(p.full_name || '').trim() || [u?.first_name, u?.last_name].filter(Boolean).join(' ') || u?.name || '';
  if (side === 'party' && !u && !String(p.phone || '').trim()) throw new ApiError(400, 'Укажите телефон другой стороны', 'missing_fields');
  return { id: old?.id ?? nextId(), side, user_id: u?.id ?? null, full_name, personal_id: String(p.personal_id || '') || (u ? '01001000000' : null),
    phone: String(p.phone || '') || u?.phone || null, telegram: u ? null : String(p.telegram || '').replace(/^@/, '') || null,
    token: old?.token ?? `mock-doc-${Math.random().toString(36).slice(2, 14)}xxxxxxxx`, tg_username: null, signed_at: null };
};
const sdFill = (d: SdDoc, b: Record<string, unknown>) => {
  d.lang = (b.lang as SdDoc['lang']) || 'KA-RU';
  d.title = (b.title as Record<string, string>) || {};
  d.body = (b.body as Record<string, string>) || {};
  for (const ln of sdLns(d)) if (!d.title[ln]?.trim() || !d.body[ln]?.trim()) throw new ApiError(400, 'Укажите название и вставьте текст документа', 'missing_fields');
  d.signers = [sdPerson(b.party as Record<string, unknown>, 'party', d.signers.find((s) => s.side === 'party')),
    sdPerson(b.cosigner as Record<string, unknown>, 'company', d.signers.find((s) => s.side === 'company'))].filter(Boolean) as SdSigner[];
  if (!d.signers.some((s) => s.side === 'party')) throw new ApiError(400, 'Укажите ФИО другой стороны', 'missing_fields');
};
const sdEditable = (d: SdDoc) => d.status === 'draft' || (d.status === 'sent' && !d.signers.some((s) => s.signed_at));
const sdOut = (d: SdDoc) => ({ id: d.id, number: sdNum(d), status: d.status, status_label: SD_LABEL[d.status], lang: d.lang, title: d.title, body: d.body,
  html: sdHtml(d), created_at: d.created_at, created_by: { id: 1, name: 'Давид' }, sent_at: d.sent_at, signed_at: d.signed_at, has_pdf: d.status === 'signed',
  editable: sdEditable(d), signers: d.signers.map((s) => ({ ...s, need_tg: !!(s.user_id || s.telegram), path: d.status === 'sent' && !s.signed_at ? `/doc/${s.token}` : null })) });
const sdGet = (id: string) => { const d = sdDocs.find((x) => x.id === Number(id)); if (!d) throw new ApiError(404, 'Документ не найден'); return d; };
on('GET', '/sign-docs', (_m, _b, q) => {
  const st = q.get('status'); const needle = (q.get('q') || '').toLowerCase();
  return page(sdDocs.filter((d) => (!st || st === 'all' || d.status === st)
    && (!needle || JSON.stringify(d.title).toLowerCase().includes(needle) || d.signers.some((s) => s.full_name.toLowerCase().includes(needle)))).slice().reverse().map(sdOut));
});
on('POST', '/sign-docs', (_m, b) => { const d: SdDoc = { id: sdNext++, status: 'draft', lang: 'KA-RU', title: {}, body: {}, signers: [], created_at: new Date().toISOString(), sent_at: null, signed_at: null, confirmed: {} }; sdFill(d, b); sdDocs.push(d); return sdSave(sdOut(d)); });
on('GET', '/sign-docs/:id', (m) => sdOut(sdGet(m[1])));
on('PUT', '/sign-docs/:id', (m, b) => { const d = sdGet(m[1]); if (!sdEditable(d)) throw new ApiError(409, 'Документ уже подписан одной из сторон — текст не меняется', 'bad_status'); sdFill(d, b); return sdSave(sdOut(d)); });
on('POST', '/sign-docs/:id/send', (m) => {
  const d = sdGet(m[1]);
  if (d.status === 'draft') Object.assign(d, { status: 'sent', sent_at: new Date().toISOString() });
  return sdSave({ ...sdOut(d), links: d.signers.filter((s) => !s.signed_at).map((s) => ({ id: s.id, sent: !!s.user_id })) });
});
on('POST', '/sign-docs/:id/cancel', (m) => { const d = sdGet(m[1]); if (d.status === 'signed') throw new ApiError(409, 'Документ: Подписан', 'bad_status'); d.status = 'cancelled'; return sdSave(sdOut(d)); });
on('GET', '/sign-docs/:id/pdf', (m) => ({ __html: sdHtml(sdGet(m[1])) }));  // fetchBlob in the mock opens it as a page
const sdTok = (t: string) => {
  for (const d of sdDocs) { const s = d.signers.find((x) => x.token === t); if (s && d.status !== 'draft' && d.status !== 'cancelled') return { d, s }; }
  throw new ApiError(404, 'Ссылка недействительна. Попросите прислать новую');
};
const sdView = (t: string) => {
  const { d, s } = sdTok(t);
  return { kind: 'doc', lang: d.lang, status: d.status, title: d.title, number: sdNum(d), full_name: s.full_name, telegram: s.telegram,
    need_tg: !!(s.user_id || s.telegram), doc_sha256: 'mock', confirmed: !!d.confirmed[s.token] || !!s.signed_at, tg_username: s.tg_username, tg_name: null,
    signed: !!s.signed_at, signed_at: s.signed_at || d.signed_at, has_pdf: d.status === 'signed', test_mode: true,
    signers: d.signers.map((x) => ({ full_name: x.full_name, signed: !!x.signed_at })), html: d.status === 'sent' ? sdHtml(d) : null };
};
on('GET', '/public/doc/:token', (m) => sdView(m[1]));
on('POST', '/public/doc/:token/telegram', (m) => { const { d, s } = sdTok(m[1]); d.confirmed[s.token] = true; s.tg_username = s.telegram || 'mock_user'; sdSave(0); return sdView(m[1]); });
on('POST', '/public/doc/:token', (m, b) => {
  const { d, s } = sdTok(m[1]);
  if ((s.user_id || s.telegram) && !d.confirmed[s.token]) throw new ApiError(403, 'Подтвердите себя через Telegram', 'telegram_required');
  if (b.consent !== true) throw new ApiError(400, 'Нужно согласие с условиями', 'consent_required');
  s.signed_at = new Date().toISOString();
  if (d.signers.every((x) => x.signed_at)) Object.assign(d, { status: 'signed', signed_at: s.signed_at });
  sdSave(0);
  return sdView(m[1]);
});

on('GET', '/public/esign/:token', (m) => esView(m[1]));
on('POST', '/public/esign/:token', (m, b) => {
  const { s } = esFind(m[1]);
  if (!String(b.full_name || '').trim()) throw new ApiError(422, 'Заполните ФИО, личный номер и телефон', 'missing_fields');
  if (s) Object.assign(s, { signed_at: new Date().toISOString(), full_name: b.full_name });
  return esView(m[1]);
});

// the owner's exclusive sign page /sign/mock-ex (backend properties.sign_view / save_sign_draft): the draft survives a
// reload here too (localStorage), so «continue where he stopped» can be checked in the mock
const EX_KEY = 'mock_ex_draft';
const exDraft = (): Record<string, unknown> => { try { return JSON.parse(localStorage.getItem(EX_KEY) || '{}'); } catch { return {}; } };
const exSave = (kv: Record<string, unknown>) => { const d = { ...exDraft(), ...kv, saved_at: new Date().toISOString() }; localStorage.setItem(EX_KEY, JSON.stringify(d)); return d; };
const exView = () => ({ id: 101, address: 'თბილისი, პეკინის გამზ. 7', cadastral_code: '', owner_name: 'Гиорги', owner_phone: '+995 555 12 34 56',
  signed_at: exDraft().signed_at || null, draft: exDraft().signed_at ? null : exDraft() });
on('GET', '/public/exclusive/:token', () => exView());
on('PUT', '/public/exclusive/:token/draft', (_m, b) => exSave(Object.fromEntries(['full_name', 'personal_id', 'phone', 'cadastral_code', 'lang', 'registry_app'].filter((k) => k in b).map((k) => [k, b[k]]))));
on('POST', '/public/exclusive/:token/id-doc', (_m, b) => exSave({ id_doc_name: (b.__files as { name: string }[] | undefined)?.[0]?.name || 'id.jpg' }));
on('POST', '/public/exclusive/:token/extract-code', (_m, b) => { const name = (b.__files as { name: string }[] | undefined)?.[0]?.name || 'extract.pdf'; exSave({ extract_name: name, cadastral_code: '01.10.14.016.017.01.001' }); return { cadastral_code: '01.10.14.016.017.01.001', name }; });
on('POST', '/public/exclusive/:token/extract-from-url', (_m, b) => {
  if (!/napr\.gov\.ge|reestri\.gov\.ge/.test(String(b.url))) throw new ApiError(422, 'Ссылка не на выписку NAPR: нужна ссылка вида https://bs.napr.gov.ge/GetBlob?…');
  exSave({ extract_name: 'extract.pdf', extract_url: b.url, cadastral_code: '01.10.14.016.017.01.001' });
  return { url: b.url, name: 'extract.pdf', cadastral_code: '01.10.14.016.017.01.001', fields: { cadastral_code: '01.10.14.016.017.01.001', owner_full_name: 'Демо Собственник', owner_personal_number: '01001000001' } };
});
on('GET', '/public/exclusive/:token/registry', () => ({ total: 1, page: 1, lastpage: 1, items: [{ app_id: '1', reg_number: '892026010466', date: new Date().toISOString(),
  type: 'ამონაწერის მომზადება უძრავ ნივთზე', address: 'ქალაქი თბილისი', status: 'სარეგისტრაციო წარმოება დასრულებულია', done: true, applicants: [], url: 'https://napr.gov.ge' }] }));
on('POST', '/public/exclusive/:token', () => { exSave({ signed_at: new Date().toISOString() }); return exView(); });

// the registry (services/registry.py): one NAPR application for any owner ID; an extract link fills demo fields
// any owner ID: one object the CRM already knows (the «Уже есть в базе CRM» card, «Подставить» / «Стереть»)
on('GET', '/registry/known', () => ({ items: [{ cadastral: '01.14.12.003.045.01.017', address: 'თბილისი, ჭავჭავაძის გამზ. 12', floor: '4',
  apartment_number: '17', area: '62', owner_name: null, source: 'Tatia · договор #6', date: new Date().toISOString() }] }));
on('GET', '/registry/applications', () => ({ total: 1, page: 1, lastpage: 1, items: [{ app_id: '1', reg_number: '892026010466', date: new Date().toISOString(),
  type: 'ამონაწერის მომზადება უძრავ ნივთზე', address: 'ქალაქი თბილისი', status: 'სარეგისტრაციო წარმოება დასრულებულია', done: true, applicants: [], url: 'https://napr.gov.ge' }] }));
on('POST', '/registry/extract-from-url', (_m, b) => {
  if (!/napr\.gov\.ge|reestri\.gov\.ge/.test(String(b.url))) throw new ApiError(422, 'Ссылка не на выписку NAPR: нужна ссылка вида https://bs.napr.gov.ge/GetBlob?…');
  return { url: b.url, file: 'private/registry-extracts/mock.pdf', cadastral_code: '01.10.14.016.017.01.001',
    fields: { cadastral_code: '01.10.14.016.017.01.001', street_house: 'ამბერკი ტაბიძის ქუჩა, N 43', street_house_en: 'Amberki Tabidze St, 43', apartment_number: '7', apartment_area: '52', owner_full_name: 'Демо Собственник', owner_personal_number: '01001000001' } };
});

// ---------- «Продажа» / «Авто» (backend api/offers.py) and the geocoder (services/geo.py) ----------
type MockOffer = Record<string, unknown> & { id: number; status: string; photos: string[]; video: string | null; post_url: string | null; title: string };
const offerBase = (id: number, o: Partial<MockOffer>): MockOffer => ({ id, status: 'draft', photos: [], video: null, post_url: null, title: '', description: null, price: null,
  currency: 'USD', lat: null, lng: null, is_exclusive: false, site_id: null, agent_id: 2, published_at: null, created_at: new Date().toISOString(), updated_at: new Date().toISOString(), ...o });
const OFFERS: Record<string, MockOffer[]> = {
  car: [
    offerBase(1, { deal: 'rent', title: 'Toyota Camry', year: 2019, price: 60, period: 'day', deposit: 300, gearbox: 'auto', fuel: 'hybrid', body: 'sedan', seats: 5, pickup: 'Аэропорт Тбилиси',
      lat: 41.6692, lng: 44.9547, status: 'published', photos: [F.photo(5, 'c1'), F.photo(6, 'c2')] }),
    offerBase(2, { deal: 'sale', title: 'Mercedes-Benz E 220', year: 2017, price: 21500, gearbox: 'auto', fuel: 'diesel', body: 'business', seats: 5, mileage: 98000, photos: [F.photo(7, 'c3')] }),
  ],
};
const offerOut = (k: string, o: MockOffer) => ({ ...o, item_id: `${k === 'sale' ? 's' : 'c'}${o.id}`, public: o.status === 'published' && o.photos.length > 0 });
const getOffer = (k: string, id: number) => { const o = OFFERS[k].find((x) => x.id === id); if (!o) throw new ApiError(404, 'Не найдено'); return o; };
const dots = (n: unknown) => String(n ?? '').replace(/\B(?=(\d{3})+(?!\d))/g, '.');
const offerPost = (k: string, o: MockOffer) => {
  const f = (o.post_fields || {}) as Record<string, string | string[] | undefined>;
  const lines = k === 'car'
    ? [`🚗 #${o.title.split(' ')[0]} ${o.title.split(' ').slice(1).join(' ')} ${o.year || ''}`.trim(), '', `🏁 #Car for ${o.deal === 'sale' ? '#Sale' : '#Rent'}`,
      `✨ #${String(o.body || '').replace(/^./, (c) => c.toUpperCase())} | #${o.gearbox === 'auto' ? 'Automatic' : 'Manual'}`, '',
      `💰 ${dots(o.price)}$${o.deal === 'rent' ? '/day' : ''}`, '0% Commission', o.deal === 'sale' ? '#CarSale' : '#CarRent', '', 'CARS IN TBILISI🇬🇪']
    : [`#${o.district || 'Tbilisi'}${f.metro ? ` 🚇 #${f.metro}` : ''}`, `📍[${o.title}](https://maps.google.com/?q=${o.lat},${o.lng})`, '', f.headline ? `🌟 ${f.headline} 🌟` : '', '',
      o.is_exclusive ? '❗️#Exclusive' : '', '', `🏢 ${o.rooms ? `#${o.rooms}Bed ` : ''}#${f.prop_type || 'Apartment'} for #Sale`,
      `✨ ${[f.building, f.design === 'New' ? 'NewApartment' : f.design, f.condition].filter(Boolean).map((x) => `#${x}`).join(' | ')}`,
      `🏠 ${o.area} Sq.m | ${o.floor}/${o.floors} Floor${f.heating ? ` | #${f.heating}` : ''}${f.bathrooms ? ` | #${f.bathrooms}` : ''}`, '',
      ((f.amenities as string[]) || []).map((a) => `✅ #${a}`).join(' '), '', `💰${dots(o.price)}$`, '0% Commission', '', '📲 @David_Tibelashvili', '', 'SALE IN TBILISI🇬🇪'];
  return lines.join('\n').replace(/\n{3,}/g, '\n\n');
};
on('GET', '/offers/:kind', (m, _b, q) => page(OFFERS[m[1]].filter((o) => !q.get('status') || o.status === q.get('status'))
  .filter((o) => !q.get('q') || o.title.toLowerCase().includes(q.get('q')!.toLowerCase())).slice().reverse().map((o) => offerOut(m[1], o))));
on('POST', '/offers/:kind', (m, b) => { const o = offerBase(nextId(), b as Partial<MockOffer>); OFFERS[m[1]].push(o); return offerOut(m[1], o); });
on('GET', '/offers/:kind/:id', (m) => offerOut(m[1], getOffer(m[1], Number(m[2]))));
on('PATCH', '/offers/:kind/:id', (m, b) => { const o = getOffer(m[1], Number(m[2])); Object.assign(o, b, { updated_at: new Date().toISOString() }); return offerOut(m[1], o); });
on('POST', '/offers/:kind/:id/status', (m, b) => {
  const o = getOffer(m[1], Number(m[2]));
  if (b.status === 'published' && !o.photos.length) throw new ApiError(400, 'Добавьте хотя бы одно фото перед публикацией');
  o.status = String(b.status);
  return offerOut(m[1], o);
});
on('POST', '/offers/:kind/:id/media', (m, b) => {
  const o = getOffer(m[1], Number(m[2]));
  for (const f of (b as unknown as { __files: { name: string; url: string }[] }).__files) if (/\.(mp4|mov|webm)$/i.test(f.name)) o.video = f.url; else o.photos.push(f.url);
  return { ...offerOut(m[1], o), uploaded_keys: [] };
});
on('PUT', '/offers/:kind/:id/media/order', (m, b) => { const o = getOffer(m[1], Number(m[2])); o.photos = b.keys as string[]; return offerOut(m[1], o); });
on('DELETE', '/offers/:kind/:id/media', (m, _b, q) => {
  const o = getOffer(m[1], Number(m[2])); const k = q.get('key');
  if (k === o.video) o.video = null; else o.photos = o.photos.filter((x) => x !== k);
  return offerOut(m[1], o);
});
on('GET', '/offers/:kind/:id/post', (m) => ({ text: offerPost(m[1], getOffer(m[1], Number(m[2]))) }));
on('POST', '/offers/:kind/:id/publish', (m, b) => {
  const o = getOffer(m[1], Number(m[2]));
  if (!b.tg && !b.site) throw new ApiError(400, 'Отметьте, куда публиковать: сайт или Telegram');
  if (b.tg) o.post_url = `https://t.me/rent_tbilisi_ge/${nextId()}`;
  if (b.site) o.status = 'published';
  return offerOut(m[1], o);
});
on('GET', '/geo/search', (_m, _b, q) => ({ point: false, items: (q.get('q') || '').length < 3 ? [] : [{ label: `${q.get('q')}, Тбилиси`, lat: 41.7105, lng: 44.7612 }] }));
on('GET', '/geo/point', (_m, _b, q) => ({ district: 'Ваке', address: `Ilia Chavchavadze Avenue ${Math.abs(Math.round(Number(q.get('lat')) * 10000)) % 90 + 1}, Tbilisi`, metro: 'Rustaveli', metro_m: 640 }));
on('POST', '/properties/:id/site', (m, b) => { const u = currentUser(); const p = getProp(Number(m[1])); p.site_hidden = !b.on; return propOut(p, u); });
on('POST', '/properties/:id/unpost', (m) => { const p = getProp(Number(m[1])); p.post_url = null; return propOut(p, currentUser()); });
on('POST', '/properties/:id/unpublish', (m) => { const p = getProp(Number(m[1])); Object.assign(p, { status: 'archived' as PropertyStatus, archive_reason: 'unpublished', post_url: null }); return propOut(p, currentUser()); });

async function formToBody(fd: FormData): Promise<Record<string, unknown>> {
  const files: { name: string; url: string }[] = [];
  const fields: Record<string, string> = {};
  fd.forEach((v, k) => {
    if (v instanceof File) files.push({ name: v.name, url: URL.createObjectURL(v) });
    else fields[k] = String(v);
  });
  return { ...fields, __files: files };
}

export async function mockFetch(method: string, url: string, body?: unknown): Promise<unknown> {
  await delay();
  const [path, query] = url.split('?');
  const q = new URLSearchParams(query || '');
  const b = body instanceof FormData ? await formToBody(body) : ((body as Record<string, unknown>) || {});
  if (!path.startsWith('/auth/login') && !path.startsWith('/auth/telegram') && path !== '/config') currentUser();
  for (const [m, re, h] of routes) {
    if (m !== method) continue;
    const match = path.match(re);
    if (match) {
      try {
        return JSON.parse(JSON.stringify(h(match, b, q) ?? null));
      } catch (e) {
        throw e;
      }
    }
  }
  throw new ApiError(404, `mock: ${method} ${path} не реализован`);
}

export type { EventItem };
