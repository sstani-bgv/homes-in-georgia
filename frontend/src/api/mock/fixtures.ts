// In-memory fixtures shaped like the API (journey characters: оператор Нино, агент Георгий, клиент Анна).
import type {
  Client, Deal, EventItem, Evaluation, Lead, LeadStatus, Listing, Owner, Property, PropertyStatus, Report,
  Source, TickerMessage, User,
} from '../types';

const now = Date.now();
const min = 60_000;
const hour = 60 * min;
const day = 24 * hour;
export const iso = (offsetMs: number) => new Date(now + offsetMs).toISOString();
const dateOnly = (offsetMs: number) => iso(offsetMs).slice(0, 10);
const atDay = (dayOffset: number, h: number) => { const d = new Date(now); d.setHours(h, 0, 0, 0); return new Date(d.getTime() + dayOffset * day).toISOString(); };

const PALETTE = [
  ['#032D6A', '#0A3C85'], ['#021F4A', '#2B3B57'], ['#0A3C85', '#4A5A75'], ['#1B4C8F', '#021F4A'],
  ['#2B3B57', '#032D6A'], ['#4A5A75', '#1B4C8F'],
];
export function photo(seed: number, _label: string): string {
  const [a, b] = PALETTE[seed % PALETTE.length];
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="640" height="400" viewBox="0 0 640 400">
<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="${a}"/><stop offset="1" stop-color="${b}"/></linearGradient></defs>
<rect width="640" height="400" fill="url(#g)"/>
<path d="M0 300 L160 200 L300 290 L430 180 L640 320 L640 400 L0 400Z" fill="rgba(255,255,255,.08)"/>
<rect x="250" y="120" width="140" height="110" rx="8" fill="none" stroke="rgba(255,255,255,.35)" stroke-width="4"/>
<path d="M240 130 L320 70 L400 130" fill="none" stroke="rgba(255,255,255,.35)" stroke-width="4"/>
</svg>`;
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}

export const DISTRICTS = ['Ваке', 'Сабуртало', 'Ваке-Багеби', 'Ортачала', 'Мтацминда', 'Дидубе', 'Глдани', 'Исани', 'Надзаладеви', 'Вере'];

export const users: User[] = [
  { id: 1, name: 'Давид', username: 'david_hig', role: 'superadmin', level: 90, districts: [], is_active: true, login: 'admin', exclusive_deals: 140, tg_id: 1001 },
  { id: 2, name: 'Нино', username: 'nino_op', role: 'operator', level: null, districts: [], is_active: true, login: 'nino', exclusive_deals: 0, tg_id: 1002 },
  { id: 3, name: 'Георгий', username: 'giorgi_agent', role: 'agent', level: 70, districts: ['Ваке', 'Сабуртало', 'Мтацминда'], is_active: true, login: 'georgiy', exclusive_deals: 34, phone: '+995 555 10 20 30', tg_id: 1003 },
  { id: 4, name: 'Тамара', username: 'tamara_k', role: 'agent', level: 50, districts: ['Ваке', 'Вере'], is_active: true, login: 'tamara', exclusive_deals: 8, tg_id: 1004 },
  { id: 5, name: 'Леван', username: 'levan_m', role: 'agent', level: 90, districts: ['Сабуртало', 'Дидубе'], is_active: true, exclusive_deals: 112, tg_id: 1005 },
  { id: 6, name: 'Мариам', username: 'mariam_g', role: 'agent', level: 70, districts: ['Ортачала', 'Исани'], is_active: true, exclusive_deals: 41, tg_id: 1006 },
  { id: 7, name: 'Ника', username: 'nika_r', role: 'recruit', level: 50, mentor_id: 3, districts: ['Сабуртало'], is_active: true, exclusive_deals: 0, tg_id: 1007, plans: { photo: 15, showing: 15, stickers: 15, rental: 15 } },
  { id: 8, name: 'Саломе', username: 'salome_b', role: 'recruit', level: 50, mentor_id: 5, districts: ['Ваке'], is_active: true, exclusive_deals: 0, tg_id: 1008, plans: { photo: 15, showing: 15, stickers: 15, rental: 15 } },
  { id: 9, name: 'Иракли', username: 'irakli_admin', role: 'admin', level: null, districts: [], is_active: true, login: 'irakli', exclusive_deals: 0 },
  { id: 10, name: 'Дато', username: 'dato_old', role: 'agent', level: 50, districts: ['Глдани'], is_active: false, exclusive_deals: 3 },
];
users.forEach((u) => {
  u.plans = u.plans || { photo: 15, showing: 15, stickers: 15, rental: 15 };
  u.created_at = iso(-90 * day);
});
export const ref = (id?: number | null) => {
  const u = users.find((x) => x.id === id);
  return u ? { id: u.id, name: u.name, username: u.username, photo_url: u.photo_url } : null;
};

const SOURCES = ['ss', 'myhome', 'facebook', 'telegram'];
const listingSeeds: [string, string, number, number, number, Listing['ai_flags']][] = [
  ['Ваке', 'Чавчавадзе 37', 850, 2, 78, { owner: true, reasons: ['Один номер, одно объявление'] }],
  ['Сабуртало', 'Пекини 12', 650, 1, 52, { agent_suspect: true, reasons: ['Номер встречается в 9 объявлениях'] }],
  ['Мтацминда', 'Гриневича 4', 1200, 3, 115, { owner: true, duplicate: true, reasons: ['Похожее фото уже в канале'] }],
  ['Ваке-Багеби', 'Ирбахи 8', 900, 2, 84, { owner: true }],
  ['Дидубе', 'Агмашенебели 155', 550, 1, 47, { owner: true }],
  ['Ортачала', 'Горгасали 90', 700, 2, 66, { agent_suspect: true, reasons: ['Текст шаблонный, «комиссия 50%»'] }],
  ['Вере', 'Барнова 21', 1500, 3, 130, { owner: true }],
  ['Исани', 'Навтлугская 5', 480, 1, 44, {}],
];
export const listings: Listing[] = listingSeeds.map(([district, address, price, bedrooms, area, flags], i) => ({
  id: `L${1000 + i}`,
  source: SOURCES[i % SOURCES.length],
  url: `https://example.ge/listing/${1000 + i}`,
  title: `${district} · ${bedrooms} ${bedrooms === 1 ? 'спальня' : 'спальни'} · ${area} м²`,
  text: `Сдаётся квартира, ${district}, ${address}. ${bedrooms} спальни, ремонт, мебель, техника. Собственник.`,
  price, currency: 'USD', district, address, bedrooms, rooms: bedrooms + 1, area,
  photos: [photo(i, district), photo(i + 1, district), photo(i + 2, district)],
  ai_flags: flags,
  created_at: iso(-(i * 47 + 12) * min),
  taken_by: i === 3 ? ref(4) : null,
  property_id: null,
}));

const STATUS_SEQ: PropertyStatus[] = ['taken', 'call_failed', 'owner_confirmed', 'agent_marked', 'shoot_scheduled', 'media_uploaded', 'review', 'returned', 'published', 'rented', 'archived'];
const propSeeds: [string, string, number, number, number][] = [
  ['Сабуртало', 'Казбеги 24', 750, 2, 70],
  ['Ваке', 'Абашидзе 61', 1100, 3, 105],
  ['Мтацминда', 'Лермонтова 3', 950, 2, 88],
  ['Дидубе', 'Церетели 77', 600, 1, 55],
  ['Ваке', 'Палиашвили 40', 1300, 3, 120],
  ['Сабуртало', 'Вазисубани 7', 680, 2, 64],
  ['Ваке', 'Ингороква 19', 1050, 2, 92],
  ['Вере', 'Кикодзе 11', 820, 2, 74],
  ['Ваке', 'Чавчавадзе 1', 1600, 3, 140],
  ['Сабуртало', 'Костава 68', 900, 2, 80],
  ['Глдани', 'Хизанишвили 2', 400, 1, 40],
];
export const ROOMS = ['Гостиная', 'Кухня', 'Спальня 1', 'Спальня 2', 'Ванная', 'Балкон', 'Вид из окна', 'Подъезд'];

export const owners: Owner[] = propSeeds.map(([, address], i) => ({
  id: i + 1,
  name: ['Манана', 'Гоча', 'Кетеван', 'Зураб', 'Нана', 'Тенгиз', 'Ия', 'Шота', 'Майя', 'Бесо', 'Лали'][i],
  phone: `+995 599 ${String(100000 + i * 7919).slice(0, 3)} ${String(100 + i * 13).slice(0, 3)}`,
  address, is_agent: i === 3, link: `https://example.ge/listing/${2000 + i}`,
  notes: i === 3 ? 'Ответил агент, объект в базе с пометкой' : null,
  created_at: iso(-(i + 2) * day),
  properties_count: 1,
}));

export const properties: Property[] = propSeeds.map(([district, address, price, bedrooms, area], i) => {
  const status = STATUS_SEQ[i];
  const agentId = i === 9 ? null : i % 4 === 1 ? 4 : 3;  // #109: a legacy object with no agent → «Платформа» (11-13)
  const late = ['shoot_scheduled', 'media_uploaded', 'review', 'returned', 'published', 'rented'].includes(status);
  const withMedia = ['media_uploaded', 'review', 'returned', 'published', 'rented'].includes(status);
  return {
    id: 100 + i,
    listing_id: `L${2000 + i}`,
    source_type: SOURCES[i % SOURCES.length],
    source_url: `https://example.ge/listing/${2000 + i}`,
    status,
    owner: { id: i + 1, name: owners[i].name, phone: owners[i].phone, is_agent: i === 3 },
    owner_id: i + 1,
    agent: agentId ? ref(agentId) : null,
    agent_id: agentId,
    taken_at: iso(-(i + 1) * day),
    call_attempts: status === 'call_failed' ? 1 : status === 'taken' ? 0 : 1,
    next_call_at: status === 'call_failed' ? iso(95 * min) : null,
    shoot_at: late ? atDay(i - 5, 11) : null,
    address, district, price, currency: 'USD', bedrooms, rooms: bedrooms + 1, area, floor: 3 + i, total_floors: 12,
    description: `Светлая квартира в районе ${district}, свежий ремонт, вся техника.`,
    is_exclusive: i % 3 === 0,
    checklist: withMedia ? Object.fromEntries(ROOMS.map((r, k) => [r, k < 6 || status !== 'returned'])) : {},
    ai_flags: i === 2 ? { owner: true, duplicate: true, reasons: ['Адрес совпадает с объектом #108'] } : { owner: i !== 3, agent_suspect: i === 3 },
    dup_check: late ? (i === 6 ? 'exists' : 'added') : null,
    review_comment: status === 'returned' ? 'Нет фото балкона и вида из окна, переснимите пожалуйста' : null,
    published_at: ['published', 'rented'].includes(status) ? iso(-2 * day) : null,
    post_url: ['published', 'rented'].includes(status) ? 'https://t.me/rent_tbilisi_ge/1234' : null,
    media: withMedia ? ROOMS.slice(0, 6).map((room, k) => ({ id: i * 100 + k, kind: 'photo' as const, path: photo(i + k, room), room, position: k })) : [],
    created_at: iso(-(i + 1) * day),
    updated_at: iso(-(i * 3) * hour),
  };
});

export const clients: Client[] = [
  { id: 1, name: 'Анна', username: 'anna_tbs', phone: '+995 577 00 11 22', lang: 'ru', profile_done: 1, created_at: iso(-3 * day),
    profile: { residents: '2', pets: ['cat'], terms: ['6', '12'], budget_min: 600, budget_max: 900, bedrooms: [1, 2], districts: ['Ваке', 'Сабуртало'], pin: { lat: 41.7105, lng: 44.7591 }, radius_km: 2, move_in: dateOnly(10 * day), photos: 2 } },
  { id: 2, name: 'Michael', username: 'mike_ge', phone: '+44 7700 900123', lang: 'en', profile_done: 1, created_at: iso(-5 * day),
    profile: { residents: '1', pets: ['none'], terms: ['12+'], budget_max: 1200, bedrooms: [2], districts: ['any'], move_in: null } },
  { id: 3, name: 'Ольга', username: 'olga_k', phone: '+995 591 22 33 44', lang: 'ru', profile_done: 1, created_at: iso(-1 * day),
    profile: { residents: '3', pets: ['dog'], terms: ['12'], budget_max: 1000, bedrooms: [2, 3], districts: ['Ваке', 'Мтацминда'] } },
  { id: 4, name: 'Sara', username: 'sara_l', phone: '+49 151 2345678', lang: 'en', profile_done: 1, created_at: iso(-7 * day),
    profile: { residents: '1', pets: ['none'], terms: ['1-3'], budget_max: 700, bedrooms: [1], districts: ['Сабуртало'] } },
  { id: 5, name: 'Дмитрий', username: 'dima_t', phone: '+995 555 66 77 88', lang: 'ru', profile_done: 1, created_at: iso(-2 * day),
    profile: { residents: '4+', pets: ['none'], terms: ['12+'], budget_max: 1600, bedrooms: [3], districts: ['Вере', 'Ваке'] } },
  { id: 6, name: 'Elena', username: 'elena_v', lang: 'en', profile_done: 0, created_at: iso(-4 * hour), profile: { residents: '2' } },
];

const LEAD_SEQ: [LeadStatus, number, number | null, number | null][] = [
  // status, client, agent, sla offset minutes (from now)
  ['new', 1, null, 3],
  ['new', 4, null, -2],
  ['operator_taken', 2, null, 1],
  ['agent_assigned', 1, 3, 22],
  ['agent_assigned', 3, 5, 6],
  ['slots_sent', 5, 3, null],
  ['time_chosen', 1, 3, null],
  ['viewing_done', 2, 4, null],
  ['deal_pending', 3, 3, null],
  ['rented', 5, 5, null],
  ['refused', 4, 6, null],
  ['thinking', 6, 3, null],
];
const SLA_STAGE: Partial<Record<LeadStatus, Lead['sla_stage']>> = {
  new: 'operator_take', operator_taken: 'operator_take', agent_assigned: 'agent_slots',
};
export const SLA_MIN = { operator_take: 5, agent_slots: 30, slots_to_client: 60 };

export const leads: Lead[] = LEAD_SEQ.map(([status, clientId, agentId, slaMin], i) => {
  const l = listings[i % listings.length];
  const p = properties[i % properties.length];
  const hasOp = status !== 'new';
  const stage = slaMin !== null ? SLA_STAGE[status] ?? null : null;
  const slots = ['slots_sent', 'time_chosen', 'viewing_done', 'deal_pending', 'rented', 'refused', 'thinking'].includes(status)
    ? [1, 2, 3].map((k) => ({ id: i * 10 + k, starts_at: atDay(k + (status === 'slots_sent' ? 0 : status === 'time_chosen' ? -1 : -3), 10 + k * 2), chosen: status !== 'slots_sent' && k === 2 }))
    : [];
  const chosen = slots.find((s) => s.chosen);
  const after = ['viewing_done', 'deal_pending', 'rented', 'refused', 'thinking'].includes(status);
  const lead: Lead = {
    id: 120 + i,
    client_id: clientId,
    listing_id: l.id,
    listing: { ...l, title: `${p.district} · ${p.bedrooms} ${p.bedrooms === 1 ? 'спальня' : 'спальни'} · $${p.price}`, district: p.district, price: p.price, bedrooms: p.bedrooms, address: p.address, area: p.area, photos: [photo(i, p.district || ''), photo(i + 2, p.district || '')] },
    property_id: p.id,
    status,
    operator: hasOp ? ref(2) : null,
    operator_id: hasOp ? 2 : null,
    agent: ref(agentId),
    agent_id: agentId,
    sla_stage: stage,
    sla_due_at: stage && slaMin !== null ? iso(slaMin * min) : null,
    sla_window_min: stage ? SLA_MIN[stage] : null,
    viewing_at: chosen ? chosen.starts_at : null,
    contacts_shared_at: chosen ? iso(-2 * day) : null,
    client_outcome: after ? (status === 'refused' ? 'refused' : status === 'thinking' ? 'thinking' : status === 'viewing_done' ? null : 'rented') : null,
    agent_outcome: after ? (status === 'refused' ? 'refused' : status === 'thinking' ? 'thinking' : 'rented') : null,
    refusal_reason: status === 'refused' ? 'price' : null,
    deal_id: status === 'deal_pending' ? 501 : status === 'rented' ? 500 : null,
    created_at: iso(-(i * 3 + 1) * hour),
    slots,
  };
  return lead;
});

function leadHistory(l: Lead): EventItem[] {
  const ev: EventItem[] = [];
  let id = l.id * 100;
  const at = (h: number) => new Date(new Date(l.created_at || iso(0)).getTime() + h * min).toISOString();
  ev.push({ id: id++, action: 'created', actor_label: 'client', text: 'Клиент нажал ❤️ — заявка создана', at: at(0) });
  if (l.operator) ev.push({ id: id++, action: 'operator_taken', actor_id: 2, actor_name: 'Нино', text: '✋ Взяла Нино', at: at(2) });
  if (l.agent) ev.push({ id: id++, action: 'agent_assigned', actor_id: 2, actor_name: 'Нино', text: `Назначен агент ${l.agent.name}`, at: at(4) });
  if (l.slots && l.slots.length) ev.push({ id: id++, action: 'slots_sent', actor_id: l.agent_id, actor_name: l.agent?.name, text: `Агент ввёл ${l.slots.length} окна, бот отправил клиенту`, at: at(25) });
  if (l.contacts_shared_at) {
    ev.push({ id: id++, action: 'time_chosen', actor_label: 'client', text: 'Клиент выбрал время просмотра', at: at(60) });
    ev.push({ id: id++, action: 'contacts_shared', actor_label: 'bot', text: 'Бот передал контакты агенту', at: at(60) });
  }
  if (l.agent_outcome) ev.push({ id: id++, action: 'agent_outcome', actor_id: l.agent_id, actor_name: l.agent?.name, text: `Итог агента: ${l.agent_outcome}`, at: at(200) });
  if (l.client_outcome) ev.push({ id: id++, action: 'client_outcome', actor_label: 'client', text: `Ответ клиента: ${l.client_outcome}`, at: at(210) });
  return ev;
}
leads.forEach((l) => { l.history = leadHistory(l); });

export const propertyEvents: Record<number, EventItem[]> = {};
properties.forEach((p) => {
  const ev: EventItem[] = [];
  let id = p.id * 100;
  const t = new Date(p.taken_at || iso(0)).getTime();
  const push = (action: string, text: string, h: number, actor?: number, label?: string) =>
    ev.push({ id: id++, action, text, actor_id: actor ?? null, actor_name: actor ? ref(actor)?.name : null, actor_label: label ?? null, at: new Date(t + h * hour).toISOString() });
  push('taken', 'Беру в работу', 0, p.agent_id ?? undefined);
  if (p.status !== 'taken') push('call', p.status === 'call_failed' ? 'Не дозвонился · бот напомнит через 2 часа' : 'Дозвонился', 1, p.agent_id ?? undefined);
  if (p.status === 'agent_marked') push('agent_marked', 'Ответил агент → в базу с пометкой', 1.2, p.agent_id ?? undefined);
  if (p.shoot_at) push('shoot_scheduled', `Съёмка назначена · ${p.dup_check === 'exists' ? '⚠️ Уже есть в базе' : '✅ Добавлено в базу'}`, 2, p.agent_id ?? undefined);
  if (p.media && p.media.length) push('media_uploaded', `Загружено ${p.media.length} фото`, 20, p.agent_id ?? undefined);
  if (['review', 'returned', 'published', 'rented'].includes(p.status)) push('submitted', 'Фото и видео сняты → на проверку', 20.5, p.agent_id ?? undefined);
  if (p.status === 'returned') push('returned', `Возвращено агенту: ${p.review_comment}`, 21, 2);
  if (['published', 'rented'].includes(p.status)) { push('approved', 'Одобрено оператором', 21, 2); push('published', 'Опубликовано в канале', 21.1, undefined, 'bot'); }
  propertyEvents[p.id] = ev;
});

export const deals: Deal[] = [
  { id: 500, lead_id: 129, property_id: 109, agent_id: 5, agent: ref(5), deal_type: 'rent', sub_type: 'long', is_exclusive: true, price: 900, commission_pct: 50, commission_total: 450, platform_amount: 45, agent_amount: 405, recruit_amount: 0, client_src_amount: 0, agent_level: 90, address: 'Костава 68', client_name: 'Дмитрий', client_phone: '+995 555 66 77 88', owner_name: 'Бесо', rent_from: dateOnly(-16 * day), rent_to: dateOnly(349 * day), status: 'confirmed', confirmed_at: iso(-15 * day), t14_date: dateOnly(-2 * day), t14_status: 'pending', created_at: iso(-16 * day), source: 'crm' },
  { id: 501, lead_id: 128, property_id: 108, agent_id: 3, agent: ref(3), deal_type: 'rent', sub_type: 'long', is_exclusive: false, price: 1600, commission_pct: 50, commission_total: 800, platform_amount: 240, agent_amount: 560, recruit_amount: 0, client_src_amount: 0, agent_level: 70, address: 'Чавчавадзе 1', client_name: 'Ольга', owner_name: 'Майя', rent_from: dateOnly(3 * day), rent_to: dateOnly(368 * day), status: 'pending', created_at: iso(-3 * hour), source: 'crm' },
  { id: 502, agent_id: 3, agent: ref(3), recruit_id: 7, recruit: ref(7), deal_type: 'rent', sub_type: 'long', is_exclusive: true, price: 1000, commission_pct: 50, commission_total: 500, platform_amount: 150, agent_amount: 300, recruit_pct: 10, recruit_amount: 50, client_src_amount: 0, agent_level: 70, address: 'Абашидзе 12', client_name: 'Kate', owner_name: 'Гиви', rent_from: dateOnly(-340 * day), rent_to: dateOnly(25 * day), status: 'confirmed', created_at: iso(-341 * day), source: 'import' },
  { id: 503, agent_id: 4, agent: ref(4), deal_type: 'rent', sub_type: 'long', is_exclusive: false, price: 700, commission_pct: 50, commission_total: 350, platform_amount: 175, agent_amount: 140, recruit_amount: 0, client_src: 'Instagram', client_src_amount: 35, agent_level: 50, address: 'Барнова 30', client_name: 'Tom', owner_name: 'Нино К.', rent_from: dateOnly(-358 * day), rent_to: dateOnly(6 * day), status: 'confirmed', created_at: iso(-360 * day), source: 'import' },
  { id: 504, agent_id: 6, agent: ref(6), deal_type: 'sale', is_exclusive: true, price: 145000, commission_pct: 3, commission_total: 4350, platform_amount: 1305, agent_amount: 3045, recruit_amount: 0, client_src_amount: 0, agent_level: 70, address: 'Горгасали 15', client_name: 'Ираклий', status: 'confirmed', rent_from: null, created_at: iso(-8 * day), source: 'crm' },
  { id: 505, agent_id: 5, agent: ref(5), deal_type: 'rent', sub_type: 'long', is_exclusive: true, price: 1300, commission_pct: 50, commission_total: 650, platform_amount: 65, agent_amount: 585, recruit_amount: 0, client_src_amount: 0, agent_level: 90, address: 'Палиашвили 40', client_name: 'Alex', rent_from: dateOnly(-364 * day), rent_to: dateOnly(1 * day), status: 'confirmed', created_at: iso(-365 * day), source: 'import' },
  { id: 506, agent_id: 3, agent: ref(3), deal_type: 'rent', sub_type: 'short', is_exclusive: false, price: 600, commission_pct: 50, commission_total: 300, platform_amount: 90, agent_amount: 210, recruit_amount: 0, client_src_amount: 0, agent_level: 70, address: 'Пекини 40', client_name: 'Lena', status: 'cancelled', created_at: iso(-20 * day), source: 'crm' },
];
deals.forEach((d) => {
  const dt = new Date(d.created_at || iso(0));
  d.month = `${dt.getMonth() + 1}/${dt.getFullYear()}`;
});

export const evaluations: Evaluation[] = [
  { id: 1, deal_id: 500, kind: 't0', author: 'agent', h2o_client: 3, h2o_owner: 3, h2o_process: 2, result: 'clean', comment: 'Всё прошло спокойно', created_at: iso(-16 * day) },
];

export const reports: Report[] = [
  { id: 1, kind: 'photo', author_id: 7, author: ref(7), recruit_id: 7, recruit: ref(7), mentor_id: 3, date: dateOnly(-1 * day), data: { recruit: 'Ника', mentor: 'Георгий', district_address: 'Сабуртало, Казбеги 24', ad_link_advertising: 'https://t.me/rent_tbilisi_ge/1200' }, created_at: iso(-1 * day) },
  { id: 2, kind: 'showing', author_id: 7, author: ref(7), recruit_id: 7, recruit: ref(7), mentor_id: 3, date: dateOnly(-1 * day), data: { recruit: 'Ника', mentor: 'Георгий', district_address: 'Ваке, Абашидзе 61', client_telegram: '@anna_tbs', result: 'Думает' }, created_at: iso(-1 * day) },
  { id: 3, kind: 'stickers', author_id: 8, author: ref(8), recruit_id: 8, recruit: ref(8), mentor_id: 5, date: dateOnly(-2 * day), data: { recruit: 'Саломе', mentor: 'Леван', district: 'Ваке', stickers_count: 40 }, created_at: iso(-2 * day) },
  { id: 4, kind: 'daily', author_id: 3, author: ref(3), date: dateOnly(-1 * day), data: { calling: 18, not_agreed: 9, agreed: 4, stickers: 0, photos: 2, showings: 3 }, created_at: iso(-1 * day) },
  { id: 5, kind: 'makefoto', author_id: 3, author: ref(3), date: dateOnly(1 * day), data: { agent: 'Георгий', address: 'Казбеги 24', owner: 'Манана', phone: '+995 599 100 100', date: dateOnly(1 * day) + ' 12:00' }, created_at: iso(-3 * hour), dup_check: 'added' },
  { id: 6, kind: 'general', author_id: 4, author: ref(4), date: dateOnly(-3 * day), data: { photo: 2, showing: 4, stickers: 0, photoAddr: 'Ваке, Ингороква 19', showingDist: 'Ваке' }, created_at: iso(-3 * day) },
];

export const ticker: TickerMessage[] = [
  { id: 1, text: 'Георгий закрыл эксклюзив в Ваке · $1 100', status: 'active' },
  { id: 2, text: 'План ноября: 40 сделок, уже 17', status: 'active' },
  { id: 3, text: 'Пятница 18:00 — общий созвон команды', status: 'active' },
];

export const sources: Source[] = [
  { id: 1, name: 'ss.ge', kind: 'ss', url: 'https://ss.ge', enabled: true, count: 412, last_run_at: iso(-40 * min) },
  { id: 2, name: 'myhome.ge', kind: 'myhome', url: 'https://myhome.ge', enabled: true, count: 388, last_run_at: iso(-40 * min) },
  { id: 3, name: 'Facebook группы', kind: 'facebook', enabled: false, count: 57, last_run_at: iso(-3 * day) },
  { id: 4, name: 'Telegram-каналы', kind: 'telegram', enabled: true, count: 140, last_run_at: iso(-2 * hour) },
];

export const moderation: Listing[] = [
  { ...listings[1], id: 'M1', ai_flags: { agent_suspect: true, reasons: ['Номер встречается в 9 объявлениях'] } },
  { ...listings[5], id: 'M2', ai_flags: { agent_suspect: true, duplicate: true, reasons: ['Фото совпадает с постом 1180'] } },
];

export const settings: Record<string, unknown> = {
  sla_operator_take_min: 5,
  sla_agent_slots_min: 30,
  sla_slots_to_client_min: 60,
  client_promise_min: 30,
  lead_target: 'group',
  deals_plan: 40,
  moderate_all: '0',
  dashboard_metrics: JSON.stringify(['deals', 'platform', 'active_objects', 'reposts', 'leaderboard', 'telegram_top', 'funnel', 'response_times']),
};
