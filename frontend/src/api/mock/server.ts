// VITE_MOCK=1 in-memory API. Mimics §7 shapes and the main state transitions so the UI can be exercised offline.
import { ApiError, getToken } from '../client';
import type { Deal, EventItem, Lead, LeadStatus, Property, PropertyStatus, Report, User } from '../types';
import * as F from './fixtures';

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
  return u;
}
const isStaff = (u: User) => ['superadmin', 'admin', 'operator'].includes(u.role);

function levelFor(n: number) { return n < 11 ? 50 : n < 101 ? 70 : 90; }
function nextLevelAt(n: number) { return n < 11 ? 11 : n < 101 ? 101 : null; }

function propOut(p: Property, u: User): Property {
  const canPhone = isStaff(u) || p.agent_id === u.id;
  return { ...p, owner: p.owner ? { ...p.owner, phone: canPhone ? p.owner.phone : null } : null };
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
  const platform = Math.round((comm * (100 - level)) / 100);
  const recruit = Math.round((comm * Number(b.recruit_pct || 0)) / 100);
  const src = b.client_src ? Math.round(comm * 0.1) : 0;
  return { commission_total: comm, platform_amount: platform, agent_amount: comm - platform - recruit - src, recruit_amount: recruit, client_src_amount: src };
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
function agentStats() {
  return F.users.filter((u) => (u.role === 'agent' || u.role === 'recruit') && u.is_active).map((u) => ({
    id: u.id, name: u.name, role: u.role, photo_url: u.photo_url, level: u.level ?? levelFor(u.exclusive_deals),
    exclusive_deals: u.exclusive_deals, next_level_at: nextLevelAt(u.exclusive_deals),
    deals_month: F.deals.filter((d) => d.agent_id === u.id && d.status === 'confirmed').length,
    active_leads: F.leads.filter((l) => l.agent_id === u.id && !['rented', 'refused'].includes(l.status)).length,
    active_properties: F.properties.filter((p) => p.agent_id === u.id && !['published', 'rented', 'archived'].includes(p.status)).length,
    avg_response_min: 8 + (u.id * 7) % 25,
  })).sort((a, b) => b.exclusive_deals - a.exclusive_deals);
}

type Handler = (m: RegExpMatchArray, body: Record<string, unknown>, q: URLSearchParams) => unknown;
const routes: [string, RegExp, Handler][] = [];
const on = (method: string, path: string, h: Handler) =>
  routes.push([method, new RegExp('^' + path.replace(/:(\w+)/g, '([^/]+)') + '$'), h]);

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
  let ls = F.listings.filter((l) => !l.property_id);
  const s = q.get('q')?.toLowerCase();
  if (s) ls = ls.filter((l) => `${l.title} ${l.address} ${l.district}`.toLowerCase().includes(s));
  if (q.get('source')) ls = ls.filter((l) => l.source === q.get('source'));
  const flag = q.get('flag');
  if (flag) ls = ls.filter((l) => (l.ai_flags as Record<string, unknown> | null)?.[flag]);
  return page(ls);
});
on('GET', '/listings/:id', (m) => F.listings.find((l) => l.id === m[1]));
on('GET', '/properties/board', () => {
  const u = currentUser();
  const out: Record<string, Property[]> = {};
  F.properties.forEach((p) => { (out[p.status] ||= []).push(propOut(p, u)); });
  return out;
});
on('GET', '/properties', (_m, _b, q) => {
  const u = currentUser();
  let ps = F.properties;
  if (q.get('mine')) ps = ps.filter((p) => p.agent_id === u.id);
  if (q.get('status')) ps = ps.filter((p) => q.get('status')!.split(',').includes(p.status));
  if (q.get('agent_id')) ps = ps.filter((p) => p.agent_id === Number(q.get('agent_id')));
  const s = q.get('q')?.toLowerCase();
  if (s) ps = ps.filter((p) => `${p.address} ${p.district} ${p.id}`.toLowerCase().includes(s));
  return page(ps.map((p) => propOut(p, u)));
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
on('GET', '/properties/:id/history', (m) => page([...(F.propertyEvents[Number(m[1])] || [])].reverse()));
on('GET', '/properties/:id', (m) => propOut(getProp(Number(m[1])), currentUser()));
on('PATCH', '/properties/:id', (m, b) => { const p = getProp(Number(m[1])); Object.assign(p, b); return propOut(p, currentUser()); });
on('POST', '/properties/:id/call', (m, b) => {
  const u = currentUser(); const p = getProp(Number(m[1]));
  p.call_attempts = (p.call_attempts || 0) + 1;
  if (b.reached) { p.status = 'owner_confirmed'; p.next_call_at = null; propEvent(p.id, 'call', 'Дозвонился', u); }
  else { p.status = 'call_failed'; p.next_call_at = new Date(Date.now() + 2 * 3600_000).toISOString(); propEvent(p.id, 'call', 'Не дозвонился · бот напомнит через 2 часа', u); }
  return propOut(p, u);
});
on('POST', '/properties/:id/owner', (m, b) => {
  const u = currentUser(); const p = getProp(Number(m[1]));
  p.owner = { id: p.owner?.id ?? nextId(), name: (b.owner_name as string) || p.owner?.name, phone: (b.owner_phone as string) || p.owner?.phone, is_agent: !b.is_owner };
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
on('POST', '/properties/:id/publish', (m) => {
  const u = currentUser(); const p = getProp(Number(m[1]));
  p.status = 'published'; p.published_at = new Date().toISOString(); p.post_url = 'https://t.me/rent_tbilisi_ge/1299';
  propEvent(p.id, 'published', 'Опубликовано в канале', u);
  return propOut(p, u);
});
on('POST', '/properties/:id/archive', (m) => {
  const u = currentUser(); const p = getProp(Number(m[1]));
  p.status = 'archived' as PropertyStatus; propEvent(p.id, 'archived', 'В архив', u);
  return propOut(p, u);
});

// ---------- owners / clients ----------
on('GET', '/owners', (_m, _b, q) => {
  const s = q.get('q')?.toLowerCase();
  const u = currentUser();
  return page(F.owners.filter((o) => !s || `${o.name} ${o.phone} ${o.address}`.toLowerCase().includes(s)).map((o) => ({
    ...o, phone: isStaff(u) ? o.phone : null,
    properties: F.properties.filter((p) => p.owner_id === o.id).map((p) => propOut(p, u)),
  })));
});
on('POST', '/owners', (_m, b) => { const o = { id: nextId(), ...b }; F.owners.push(o); return o; });
on('PATCH', '/owners/:id', (m, b) => { const o = F.owners.find((x) => x.id === Number(m[1]))!; Object.assign(o, b); return o; });
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
  if (b.outcome === 'rented') setLeadStatus(l, 'deal_pending');
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
on('POST', '/leads/:id/message', (m, b) => {
  const u = currentUser(); const l = getLead(Number(m[1]));
  leadEvent(l, 'message', `Сообщение клиенту: ${b.text}`, u);
  return { ok: true };
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
on('GET', '/analytics/funnel', () => ({}));

// ---------- content / admin ----------
on('GET', '/ticker', () => page(F.ticker.filter((t) => t.status === 'active')));
on('POST', '/ticker', (_m, b) => { const t = { id: nextId(), text: String(b.text), status: 'active' }; F.ticker.push(t); return t; });
on('DELETE', '/ticker/:id', (m) => { const t = F.ticker.find((x) => x.id === Number(m[1])); if (t) t.status = 'deleted'; return { ok: true }; });
on('GET', '/news', () => page([
  { id: 1, date: new Date().toISOString().slice(0, 10), text: 'Новые правила публикации: без проверки оператором в канал ничего не уходит.' },
  { id: 2, date: new Date(Date.now() - 3 * 86400_000).toISOString().slice(0, 10), text: 'Запущена воронка клиентов в боте: заявки приходят в общую группу операторов.' },
]));
on('GET', '/settings', () => F.settings);
on('PUT', '/settings', (_m, b) => Object.assign(F.settings, b));
on('GET', '/sources', () => page(F.sources));
on('POST', '/sources', (_m, b) => { const s = { id: nextId(), enabled: true, ...b } as never; F.sources.push(s); return s; });
on('PATCH', '/sources/:id', (m, b) => { const s = F.sources.find((x) => String(x.id) === m[1])!; Object.assign(s, b); return s; });
on('GET', '/moderation', () => page(F.moderation));
on('POST', '/moderation/:id', (m) => { const i = F.moderation.findIndex((x) => x.id === m[1]); if (i >= 0) F.moderation.splice(i, 1); return { ok: true }; });
on('GET', '/addresses', () => page([]));

async function formToBody(fd: FormData): Promise<Record<string, unknown>> {
  const files: { name: string; url: string }[] = [];
  let room: string | undefined;
  fd.forEach((v, k) => {
    if (v instanceof File) files.push({ name: v.name, url: URL.createObjectURL(v) });
    else if (k === 'room') room = String(v);
  });
  return { __files: files, room };
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
