// VITE_MOCK=1: «Посуточная аренда» in memory (backend api/daily_rent.py shapes; money = services/daily_rent.calc).
import { ApiError } from '../client';
import type { Calc, DrApartment, DrBooking, DrChannel, DrCleaning, DrExpense, DrStaff, Econ, Interval, Mix } from '../../features/dailyRent/api';

type Handler = (m: RegExpMatchArray, body: Record<string, unknown>, q: URLSearchParams) => unknown;
const RATE = 2.69;
let seq = 900;
const r2 = (x: number) => Math.round((x + Number.EPSILON) * 100) / 100;
const day = (n: number) => { const d = new Date(); d.setDate(d.getDate() + n); return d.toISOString().slice(0, 10); };

const apts: DrApartment[] = [
  ['Rustaveli Warm Corner', '7 Solomon Zaldastanishvili St', 'Sophiko', 'GE29NB0000000101904917', 'free'],
  ['Vake Nest', '8 Dimitri Arakishvili St', 'Natia', 'GE11TB7000000000000001', 'busy'],
  ['Postcard Tbilisi', '14 Betlemi St', 'Levan', '', 'clean'],
  ['House of Whispers', '22 Pavle Ingorokva St', 'Mari', '', 'free'],
].map(([name, address, owner, acc, status], i) => ({
  id: i + 1, legacy_id: String(i + 1), name, address, owner_name: owner, owner_contact: '+995 599 50 13 34', owner_account: acc || null,
  platform_pct: 20, owner_pct: 80, platforms: ['Airbnb', 'Booking'], status: status as DrApartment['status'], active: i < 3, rooms: 1 + (i % 2),
  notes: null, util_rates: {}, photos: [], price_night: [55, 70, 48, 60][i], currency: 'USD', description: null, district: null, report_lang: 'ru' as const, channels: [],
}));
const guests = ['Алена Терещенко', 'Balaian Taron', 'Di', 'Mark Weber', 'Ольга', 'Sara Kim', 'Giorgi', 'Lena Novak', 'Tom', 'Анна'];
const bookings: Omit<DrBooking, 'calc' | 'nights' | 'expenses'>[] = [];
const expenses: DrExpense[] = [];
guests.forEach((g, i) => {
  const plat = (['Airbnb', 'Booking', 'Airbnb', 'Booking', 'Другое'] as const)[i % 5];
  const ci = day(-26 + i * 4), co = day(-26 + i * 4 + 2 + (i % 3));
  const id = 100 + i;
  bookings.push({ id, apartment_id: (i % 3) + 1, platform: plat, guest_name: g, check_in: ci, check_out: co, gross: [149.19, 540, 220, 312.5, 180][i % 5] + i * 7,
    currency: plat === 'Airbnb' ? 'USD' : 'GEL', tax_pct: plat === 'Airbnb' ? 1 : 0, booking_fee: plat === 'Booking' ? 41.3 : null, platform_pct: 20,
    payment_status: i === 8 ? 'pending' : i === 7 ? 'partial' : 'received', paid_out: i < 5, paid_out_at: i < 5 ? day(-3) : null,
    assigned_staff: i % 2 ? 'Mike' : 'Manjary', comment: null });
  expenses.push({ id: ++seq, booking_id: id, category: 'cleaning', amount: 57.89, currency: 'GEL', payer: 'platform', comment: null, date: co, created_by: null });
  if (i % 3 === 0) expenses.push({ id: ++seq, booking_id: id, category: 'supplies', amount: 12, currency: 'USD', payer: 'owner', comment: 'Кофе, вода', date: co, created_by: null });
});
const staff: DrStaff[] = [{ id: 1, name: 'Mike', role: 'Cleaning', contact: '995511741008', active: true }, { id: 2, name: 'Manjary', role: 'Cleaning', contact: '@manjary1008', active: true }];
const cleanings: DrCleaning[] = [{ id: 1, apartment_id: 2, cleaner: 'Mike', date: day(-2), started_at: '11:00', finished_at: '13:30', hours: 2.5, hourly_rate: 16.7, amount: 41.75, notes: null }];
const blocks: (Interval & { status: string })[] = [
  { kind: 'block', id: 1, apartment_id: 1, start: day(6), end: day(11), origin: 'airbnb', reserved: true, summary: 'Reserved', channel_id: 1, status: 'active' },
  { kind: 'block', id: 2, apartment_id: 2, start: day(12), end: day(15), origin: 'manual', reserved: true, summary: 'ремонт', status: 'active' },
];
const channels: DrChannel[] = [{ id: 1, apartment_id: 1, channel: 'airbnb', import_url: 'https://www.airbnb.com/calendar/ical/123.ics?s=x', events: 3, last_status: 'ok', last_error: null,
  last_sync_at: new Date().toISOString(), export_url: 'https://admin.hge.ge/api/public/daily-rent/ical/4f1c…e2.ics' }];
const journal: { id: number; entity: string; entity_id: string; action: string; actor: string; data: Record<string, unknown>; at: string }[] = [];
journal.push(
  { id: 3, entity: 'dr_booking', entity_id: '104', action: 'update', actor: 'Давид', data: { changes: { paid_out: [false, true], paid_out_at: [null, day(-3)] } }, at: new Date(Date.now() - 3 * 864e5).toISOString() },
  { id: 2, entity: 'dr_expense', entity_id: '905', action: 'create', actor: 'tg:6014886676:kvirkvel1a', data: { after: { category: 'cleaning', amount: 57.89 } }, at: new Date(Date.now() - 5 * 864e5).toISOString() },
  { id: 1, entity: 'dr_booking', entity_id: '101', action: 'create', actor: 'import:sheets', data: { after: { guest_name: 'Balaian Taron' } }, at: '2026-11-11T19:58:09.222Z' },
);
const log = (entity: string, id: number, action: string, data: Record<string, unknown> = {}) =>
  journal.unshift({ id: ++seq, entity, entity_id: String(id), action, actor: 'Давид', data, at: new Date().toISOString() });

function calc(b: Omit<DrBooking, 'calc' | 'nights' | 'expenses'>): Calc {
  const cur = b.currency, gross = Number(b.gross) || 0, tax = r2(gross * b.tax_pct / 100), after = r2(gross - tax);
  const fee = b.platform === 'Booking' ? Number(b.booking_fee) || 0 : 0;
  const ex = expenses.filter((e) => e.booking_id === b.id && (e.amount || 0) > 0).map((e) => ({ id: e.id, category: e.category, amount: e.amount!, currency: e.currency,
    in_booking: e.currency === cur ? e.amount! : cur === 'USD' ? r2(e.amount! / RATE) : r2(e.amount! * RATE), payer: e.payer, comment: e.comment }));
  const total = r2(ex.reduce((s, e) => s + e.in_booking, 0)), net = r2(after - total - fee);
  const pp = b.platform_pct, pb = r2(net * pp / 100), ob = r2(net * (100 - pp) / 100);
  const ep = r2(ex.filter((e) => e.payer === 'platform').reduce((s, e) => s + e.in_booking, 0)), eo = r2(ex.filter((e) => e.payer === 'owner').reduce((s, e) => s + e.in_booking, 0));
  const plat = r2(pb + ep + fee), own = r2(ob + eo), usd = (v: number) => (cur === 'USD' ? v : r2(v / RATE));
  return { currency: cur, gross, tax_pct: b.tax_pct, tax, after_tax: after, booking_fee: fee, expenses: ex, total_expenses: total, net, platform_pct: pp, owner_pct: 100 - pp,
    platform_base: pb, owner_base: ob, exp_platform: ep, exp_owner: eo, platform: plat, owner: own, gross_usd: usd(gross), net_usd: usd(net), platform_usd: usd(plat),
    owner_usd: usd(own), costs_usd: usd(r2(total + tax + fee)) };
}
const nights = (b: { check_in: string | null; check_out: string | null }) => b.check_in && b.check_out ? Math.max(0, Math.round((+new Date(b.check_out) - +new Date(b.check_in)) / 864e5)) : 0;
const full = (b: Omit<DrBooking, 'calc' | 'nights' | 'expenses'>): DrBooking => ({ ...b, nights: nights(b), calc: calc(b), expenses: expenses.filter((e) => e.booking_id === b.id) });
function report(b: DrBooking) {
  const a = apts.find((x) => x.id === b.apartment_id)!, c = b.calc, S = (v: number) => (c.currency === 'GEL' ? '₾' : '$') + v.toLocaleString('ru-RU');
  return [`🏠 ${a.name}`, `📍 ${a.address}, Tbilisi`, '', `📅 Период: ${b.check_in} — ${b.check_out} (${b.nights} ноч.)`, `👤 Гость: ${b.guest_name}`, `📱 Платформа: ${b.platform}`, '',
    `💰 Пришло: ${S(c.gross)}`, c.total_expenses ? `🔧 Расходы: -${S(c.total_expenses)}` : null, '', `✅ Чистая прибыль: ${S(c.net)}`, '',
    `👤 Платформа (${c.platform_pct}%): ${S(c.platform)}`, `🏦 ${a.owner_name} (${c.owner_pct}%): ${S(c.owner)}`].filter((x) => x !== null).join('\n');
}
const live = () => bookings.map(full).sort((a, b) => String(b.check_in).localeCompare(String(a.check_in)));
const inPer = (b: DrBooking, q: URLSearchParams) => { const f = q.get('month_from'), t = q.get('month_to'), m = (b.check_in || '').slice(0, 7); return !f || (m >= f && m <= t!); };
function add(m: Mix, c: 'USD' | 'GEL', v: number) { if (v) m[c] = r2((m[c] || 0) + v); }
function econ(rows: DrBooking[], days?: number): Econ {
  const e: Econ = { stays: 0, nights: 0, gross_usd: 0, net_usd: 0, platform_usd: 0, owner_usd: 0, to_pay_usd: 0, costs_usd: 0, adr_usd: 0, occupancy: null, gross: {}, owner: {}, to_pay: {} };
  for (const b of rows) {
    const c = b.calc; e.stays++; e.nights += b.nights; e.gross_usd += c.gross_usd; e.net_usd += c.net_usd; e.platform_usd += c.platform_usd; e.owner_usd += c.owner_usd; e.costs_usd += c.costs_usd;
    add(e.gross, c.currency, c.gross); add(e.owner, c.currency, c.owner);
    if (!b.paid_out) { e.to_pay_usd += c.owner_usd; add(e.to_pay, c.currency, c.owner); }
  }
  e.adr_usd = e.nights ? r2(e.gross_usd / e.nights) : 0;
  e.occupancy = days ? Math.min(100, Math.round(e.nights / days * 100)) : null;
  return e;
}
const days = (q: URLSearchParams) => (q.get('month_from') === q.get('month_to') && q.get('month_from') ? 30 : q.get('month_from') ? 90 : undefined);
const apt = (id: number) => { const a = apts.find((x) => x.id === id); if (!a) throw new ApiError(404, 'Квартира не найдена'); return { ...a, channels: channels.filter((c) => c.apartment_id === id) }; };
const getB = (id: number) => { const b = bookings.find((x) => x.id === id); if (!b) throw new ApiError(404, 'Проживание не найдено'); return b; };
const ready = (b: DrBooking) => !b.paid_out && b.payment_status !== 'pending' && b.calc.owner > 0;

export function registerDailyRent(on: (method: string, path: string, h: Handler) => void) {
  const P = '/daily-rent';
  on('GET', `${P}/data`, () => ({ apartments: apts.map((a) => apt(a.id)), staff, rate: RATE }));
  on('GET', `${P}/summary`, (_m, _b, q) => {
    const rows = live().filter((b) => inPer(b, q));
    const months: Record<string, number> = {};
    for (const b of live()) if (b.check_in) months[b.check_in.slice(0, 7)] = r2((months[b.check_in.slice(0, 7)] || 0) + b.calc.gross_usd);
    const payouts = apts.map((a) => {
      const mine = live().filter((b) => b.apartment_id === a.id && !b.paid_out && b.calc.owner > 0);
      const rd: Mix = {}, wt: Mix = {};
      mine.forEach((b) => add(ready(b) ? rd : wt, b.currency, b.calc.owner));
      return { apartment: apt(a.id), bookings: mine, ready: rd, waiting: wt, ready_count: mine.filter(ready).length };
    }).filter((p) => p.bookings.length);
    const d = days(q);
    return { rate: RATE, total: econ(rows, d ? d * apts.filter((a) => a.active).length : undefined), months,
      apartments: apts.filter((a) => a.active).map((a) => ({ apartment: apt(a.id), econ: econ(rows.filter((b) => b.apartment_id === a.id), d) })), payouts };
  });
  on('GET', `${P}/journal`, () => ({ items: journal, total: journal.length }));
  on('GET', `${P}/apartments/:id`, (m, _b, q) => {
    const a = apt(Number(m[1])), rows = live().filter((b) => b.apartment_id === a.id), per = rows.filter((b) => inPer(b, q));
    const e = econ(per, days(q));
    const pr = [`🏠 ${a.name}`, `📅 Период: ${q.get('label') || 'За всё время'}`, '',
      ...per.map((b) => `• ${b.check_in} — ${b.check_out}, ${b.guest_name}, ${b.platform}\n   пришло ${b.calc.gross} · владельцу ${b.calc.owner}${b.paid_out ? ' ✓ выплачено' : ''}`),
      '', `Проживаний: ${e.stays} · ночей: ${e.nights}`, a.owner_account ? `\n🏦 Счёт: ${a.owner_account}` : ''].join('\n');
    return { ...a, bookings: rows, econ: e, period_report: pr, period_reports: { ru: pr, en: pr, ka: pr } };
  });
  on('POST', `${P}/apartments`, (_m, b) => { const a = { ...apts[0], ...b, id: ++seq, photos: [], channels: [] } as DrApartment; apts.push(a); log('dr_apartment', a.id, 'create'); return apt(a.id); });
  on('PATCH', `${P}/apartments/:id`, (m, b) => { Object.assign(apts.find((x) => x.id === Number(m[1]))!, b); log('dr_apartment', Number(m[1]), 'update', { changes: Object.fromEntries(Object.keys(b).map((k) => [k, ['…', b[k]]])) }); return apt(Number(m[1])); });
  on('POST', `${P}/apartments/:id/photos`, (m, b) => { const a = apts.find((x) => x.id === Number(m[1]))!; a.photos.push(...((b.__files as { url: string }[]) || []).map((f) => f.url)); return apt(a.id); });
  on('PUT', `${P}/apartments/:id/photos/order`, (m, b) => { apts.find((x) => x.id === Number(m[1]))!.photos = b.keys as string[]; return apt(Number(m[1])); });
  on('DELETE', `${P}/apartments/:id/photos`, (m, _b, q) => { const a = apts.find((x) => x.id === Number(m[1]))!; a.photos = a.photos.filter((k) => k !== q.get('key')); return apt(a.id); });
  on('GET', `${P}/bookings`, (_m, _b, q) => ({ items: live().filter((b) => inPer(b, q) && (!q.get('apartment_id') || b.apartment_id === Number(q.get('apartment_id')))) }));
  on('GET', `${P}/bookings/defaults`, (_m, _b, q) => { const p = q.get('platform') || 'Airbnb'; return { platform: p, currency: p === 'Airbnb' ? 'USD' : 'GEL', tax_pct: p === 'Airbnb' ? 1 : 0, platform_pct: 20, payment_status: 'received' }; });
  const reps = (f: DrBooking) => { const t = report(f); return { report: t, report_lang: 'ru' as const, reports: { ru: t, en: t, ka: t } }; };  // the mock does not translate
  on('GET', `${P}/bookings/:id`, (m) => { const b = full(getB(Number(m[1]))); return { ...b, ...reps(b) }; });
  on('POST', `${P}/bookings`, (_m, b) => { const x = { platform_pct: 20, paid_out: false, paid_out_at: null, ...b, id: ++seq } as DrBooking; bookings.push(x); log('dr_booking', x.id, 'create', { after: { guest_name: x.guest_name } }); const f = full(x); return { ...f, ...reps(f) }; });
  on('PATCH', `${P}/bookings/:id`, (m, b) => { Object.assign(getB(Number(m[1])), b); log('dr_booking', Number(m[1]), 'update', { changes: Object.fromEntries(Object.keys(b).map((k) => [k, ['…', b[k]]])) }); const f = full(getB(Number(m[1]))); return { ...f, ...reps(f) }; });
  on('DELETE', `${P}/bookings/:id`, (m) => { bookings.splice(bookings.indexOf(getB(Number(m[1]))), 1); log('dr_booking', Number(m[1]), 'delete'); return { deleted: Number(m[1]) }; });
  on('POST', `${P}/payouts`, (_m, b) => {
    const ids = (b.booking_ids as number[]) || live().filter((x) => x.apartment_id === b.apartment_id && ready(x)).map((x) => x.id);
    ids.forEach((id) => { const x = getB(id); x.paid_out = b.paid !== false; x.paid_out_at = x.paid_out ? day(0) : null; log('dr_booking', id, 'update', { changes: { paid_out: [!x.paid_out, x.paid_out] } }); });
    return { changed: ids.length };
  });
  on('POST', `${P}/expenses`, (_m, b) => { expenses.push({ id: ++seq, payer: 'platform', currency: 'GEL', category: 'other', comment: null, date: day(0), created_by: 'Давид', ...b } as DrExpense); const f = full(getB(Number(b.booking_id))); return { ...f, ...reps(f) }; });
  on('PATCH', `${P}/expenses/:id`, (m, b) => { const e = expenses.find((x) => x.id === Number(m[1]))!; Object.assign(e, b); const f = full(getB(e.booking_id!)); return { ...f, ...reps(f) }; });
  on('DELETE', `${P}/expenses/:id`, (m) => { const e = expenses.find((x) => x.id === Number(m[1]))!; expenses.splice(expenses.indexOf(e), 1); const f = full(getB(e.booking_id!)); return { ...f, ...reps(f) }; });
  on('GET', `${P}/cleanings`, () => ({ items: cleanings }));
  on('POST', `${P}/cleanings`, (_m, b) => { const c = { ...b, id: ++seq } as DrCleaning; cleanings.unshift(c); return c; });
  on('DELETE', `${P}/cleanings/:id`, (m) => { cleanings.splice(cleanings.findIndex((x) => x.id === Number(m[1])), 1); return {}; });
  on('POST', `${P}/staff`, (_m, b) => { const s = { active: true, ...b, id: ++seq } as DrStaff; staff.push(s); return s; });
  on('PATCH', `${P}/staff/:id`, (m, b) => Object.assign(staff.find((x) => x.id === Number(m[1]))!, b));
  on('GET', `${P}/calendar`, () => {
    const stays: Interval[] = live().filter((b) => b.check_in && b.check_out).map((b) => ({ kind: 'stay', id: b.id, apartment_id: b.apartment_id, start: b.check_in!, end: b.check_out!,
      origin: b.platform === 'Airbnb' ? 'airbnb' : b.platform === 'Booking' ? 'booking' : 'other', guest: b.guest_name || '', platform: b.platform }));
    const bl = blocks.filter((x) => x.status === 'active');
    const items = [...stays, ...bl];
    const conflicts = [];
    for (const s of stays) for (const x of bl) if (x.apartment_id === s.apartment_id && x.origin !== s.origin && x.start < s.end && s.start < x.end && x.origin !== 'manual')
      conflicts.push({ key: `${s.id}|${x.id}`, apartment_id: s.apartment_id, start: s.start > x.start ? s.start : x.start, end: s.end < x.end ? s.end : x.end, a: s, b: x });
    return { items, conflicts, unaccounted: bl.filter((x) => x.origin !== 'manual' && !stays.some((s) => s.apartment_id === x.apartment_id && s.origin === x.origin && s.start < x.end && x.start < s.end)) };
  });
  on('POST', `${P}/blocks`, (_m, b) => { const x = { kind: 'block' as const, id: ++seq, origin: 'manual', reserved: true, status: 'active', summary: String(b.reason || ''), ...b } as Interval & { status: string }; blocks.push(x); return x; });
  on('DELETE', `${P}/blocks/:id`, (m) => { blocks.find((x) => x.id === Number(m[1]))!.status = 'cancelled'; return {}; });
  on('POST', `${P}/channels`, (_m, b) => { const c = { id: ++seq, import_url: null, events: null, last_status: null, last_error: null, last_sync_at: null, export_url: `https://admin.hge.ge/api/public/daily-rent/ical/${seq}abc.ics`, ...b } as DrChannel; channels.push(c); return c; });
  on('PATCH', `${P}/channels/:id`, (m, b) => Object.assign(channels.find((x) => x.id === Number(m[1]))!, b));
  on('DELETE', `${P}/channels/:id`, (m) => { channels.splice(channels.findIndex((x) => x.id === Number(m[1])), 1); return {}; });
  on('POST', `${P}/channels/:id/sync`, (m) => { const c = channels.find((x) => x.id === Number(m[1]))!; Object.assign(c, { last_status: 'ok', events: 3, last_sync_at: new Date().toISOString() }); return { events: 3, channel: c }; });
}
