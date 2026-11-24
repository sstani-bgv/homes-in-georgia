// «Посуточная аренда»: types and calls of backend api/daily_rent.py (ApartFlow moved into the CRM).
import { http, uploadWithProgress } from '../../api/client';

export type Cur = 'USD' | 'GEL';
export type Platform = 'Airbnb' | 'Booking' | 'Другое';
export type PayStatus = 'received' | 'pending' | 'partial';
export type AptStatus = 'free' | 'busy' | 'clean' | 'tech';
export type Mix = Partial<Record<Cur, number>>;
export type Lang = 'ru' | 'en' | 'ka';
export type Reports = Record<Lang, string>;
export const LANGS: { value: Lang; label: string }[] = [{ value: 'ru', label: 'Русский' }, { value: 'en', label: 'English' }, { value: 'ka', label: 'ქართული' }];

export interface DrChannel {
  id: number; apartment_id: number; channel: 'airbnb' | 'booking' | 'other'; import_url: string | null; export_url: string;
  last_sync_at: string | null; last_status: string | null; last_error: string | null; events: number | null;
}
export interface DrApartment {
  id: number; legacy_id: string | null; name: string; address: string | null; owner_name: string | null; owner_contact: string | null;
  owner_account: string | null; platform_pct: number; owner_pct: number; platforms: Platform[]; status: AptStatus; active: boolean;
  rooms: number | null; notes: string | null; util_rates: Record<string, string | number>; photos: string[]; price_night: number | null;
  currency: string; description: string | null; district: string | null; report_lang: Lang; channels: DrChannel[];
}
export interface DrExpense {
  id: number; booking_id: number | null; category: string; amount: number | null; currency: Cur; payer: 'platform' | 'owner';
  comment: string | null; date: string | null; created_by: string | null;
}
export interface Calc {
  currency: Cur; gross: number; tax_pct: number; tax: number; after_tax: number; booking_fee: number; total_expenses: number; net: number;
  platform_pct: number; owner_pct: number; platform_base: number; owner_base: number; exp_platform: number; exp_owner: number;
  platform: number; owner: number; gross_usd: number; net_usd: number; platform_usd: number; owner_usd: number; costs_usd: number;
  expenses: { id: number; category: string; amount: number; currency: Cur; in_booking: number; payer: string; comment: string | null }[];
}
export interface DrBooking {
  id: number; apartment_id: number; platform: Platform; guest_name: string | null; check_in: string | null; check_out: string | null;
  gross: number | null; currency: Cur; tax_pct: number; booking_fee: number | null; platform_pct: number; payment_status: PayStatus;
  paid_out: boolean; paid_out_at: string | null; assigned_staff: string | null; comment: string | null; nights: number; calc: Calc;
  expenses: DrExpense[]; report?: string; reports?: Reports; report_lang?: Lang;
}
export interface Econ {
  stays: number; nights: number; gross_usd: number; net_usd: number; platform_usd: number; owner_usd: number; to_pay_usd: number;
  costs_usd: number; adr_usd: number; occupancy: number | null; gross: Mix; owner: Mix; to_pay: Mix;
}
export interface Payout { apartment: DrApartment; bookings: DrBooking[]; ready: Mix; waiting: Mix; ready_count: number }
export interface Summary { rate: number; total: Econ; apartments: { apartment: DrApartment; econ: Econ }[]; months: Record<string, number>; payouts: Payout[] }
export interface DrStaff { id: number; name: string; role: string | null; contact: string | null; active: boolean }
export interface DrCleaning {
  id: number; apartment_id: number; cleaner: string | null; date: string | null; started_at: string | null; finished_at: string | null;
  hours: number | null; hourly_rate: number | null; amount: number | null; notes: string | null;
}
export interface Interval {
  kind: 'stay' | 'block'; id: number; apartment_id: number; start: string; end: string; origin: string; guest?: string; platform?: string;
  channel_id?: number | null; reserved?: boolean; summary?: string; echo?: boolean;
}
export interface Conflict { key: string; apartment_id: number; start: string; end: string; a: Interval; b: Interval }
export interface CalendarData { items: Interval[]; conflicts: Conflict[]; unaccounted: Interval[] }
export interface JournalRow { id: number; entity: string; entity_id: string; action: string; actor: string | null; data: Record<string, unknown>; at: string }
export type AptFull = DrApartment & { bookings: DrBooking[]; econ: Econ; period_report: string; period_reports: Reports };
export interface Period { month_from?: string; month_to?: string }
/** «Сегодня» (api/daily_rent.py /today, services/daily_rent_auto.today) */
export interface TodayStay {
  id: number; apartment_id: number; apartment: string; guest: string; platform: Platform; check_in: string; check_out: string;
  nights: number; no_money: boolean; auto: boolean; code: string | null; cancelled_on_platform: boolean;
}
export interface TodayData {
  date: string; living: TodayStay[]; arrivals: TodayStay[]; departures: TodayStay[]; upcoming: TodayStay[]; no_money: TodayStay[];
  cleanings: { id: number; apartment_id: number; apartment: string; cleaner: string; notes: string; done: boolean }[];
  payouts_ready: Mix; load: { apartment_id: number; apartment: string; busy: number; occupancy: number }[];
  alerts: { kind: 'gap' | 'hidden_booked' | 'cancelled_with_money'; apartment_id: number; text: string }[];
}

const P = '/daily-rent';
export const dr = {
  data: () => http.get<{ apartments: DrApartment[]; staff: DrStaff[]; rate: number }>(`${P}/data`),
  today: () => http.get<TodayData>(`${P}/today`),
  summary: (p: Period) => http.get<Summary>(`${P}/summary`, { ...p }),
  journal: (offset = 0) => http.get<{ items: JournalRow[]; total: number }>(`${P}/journal`, { limit: 100, offset }),
  apartment: (id: number, p: Period & { label?: string }) => http.get<AptFull>(`${P}/apartments/${id}`, { ...p }),
  createApt: (b: Partial<DrApartment>) => http.post<DrApartment>(`${P}/apartments`, b),
  updateApt: (id: number, b: Partial<DrApartment>) => http.patch<DrApartment>(`${P}/apartments/${id}`, b),
  deleteApt: (id: number) => http.del(`${P}/apartments/${id}`),
  upload: (id: number, file: File, onProgress: (f: number) => void) => {
    const fd = new FormData();
    fd.append('files', file);
    return uploadWithProgress<DrApartment>(`${P}/apartments/${id}/photos`, fd, onProgress);
  },
  orderPhotos: (id: number, keys: string[]) => http.put<DrApartment>(`${P}/apartments/${id}/photos/order`, { keys }),
  deletePhoto: (id: number, key: string) => http.del<DrApartment>(`${P}/apartments/${id}/photos?key=${encodeURIComponent(key)}`),
  bookings: (q: Period & { apartment_id?: number }) => http.get<{ items: DrBooking[] }>(`${P}/bookings`, { ...q }),
  defaults: (apartment_id: number, platform?: Platform) => http.get<Partial<DrBooking>>(`${P}/bookings/defaults`, { apartment_id, platform }),
  booking: (id: number) => http.get<DrBooking>(`${P}/bookings/${id}`),
  createBooking: (b: Partial<DrBooking>) => http.post<DrBooking>(`${P}/bookings`, b),
  updateBooking: (id: number, b: Partial<DrBooking>) => http.patch<DrBooking>(`${P}/bookings/${id}`, b),
  deleteBooking: (id: number) => http.del(`${P}/bookings/${id}`),
  pay: (b: { booking_ids?: number[]; apartment_id?: number; paid?: boolean }) => http.post<{ changed: number }>(`${P}/payouts`, b),
  addExpense: (b: Partial<DrExpense>) => http.post<DrBooking>(`${P}/expenses`, b),
  updateExpense: (id: number, b: Partial<DrExpense>) => http.patch<DrBooking>(`${P}/expenses/${id}`, b),
  deleteExpense: (id: number) => http.del<DrBooking>(`${P}/expenses/${id}`),
  cleanings: () => http.get<{ items: DrCleaning[] }>(`${P}/cleanings`),
  createCleaning: (b: Partial<DrCleaning>) => http.post<DrCleaning>(`${P}/cleanings`, b),
  deleteCleaning: (id: number) => http.del(`${P}/cleanings/${id}`),
  createStaff: (b: Partial<DrStaff>) => http.post<DrStaff>(`${P}/staff`, b),
  updateStaff: (id: number, b: Partial<DrStaff>) => http.patch<DrStaff>(`${P}/staff/${id}`, b),
  calendar: (start: string, end: string) => http.get<CalendarData>(`${P}/calendar`, { start, end }),
  createBlock: (b: { apartment_id: number; start: string; end: string; reason?: string; force?: boolean }) => http.post(`${P}/blocks`, b),
  cancelBlock: (id: number) => http.del(`${P}/blocks/${id}`),
  createChannel: (b: { apartment_id: number; channel: string; import_url?: string }) => http.post<DrChannel>(`${P}/channels`, b),
  updateChannel: (id: number, import_url: string) => http.patch<DrChannel>(`${P}/channels/${id}`, { import_url }),
  deleteChannel: (id: number) => http.del(`${P}/channels/${id}`),
  syncChannel: (id: number) => http.post<{ events?: number; error?: string; channel: DrChannel }>(`${P}/channels/${id}/sync`),
};

// ---------------------------------------------------------------- words and money, as ApartFlow shows them

export const EXP_LABEL: Record<string, string> = {
  cleaning: 'Уборка', supplies: 'Расходники', repair: 'Ремонт', other: 'Прочее', telmico: '⚡ Telmico', gwp: '💧 GWP',
  energy: '🔥 Tbilisi Energy', internet: '🌐 Internet', tbilisi_clean: '🗑 Tbilisi Clean',
};
export const PAY_LABEL: Record<PayStatus, string> = { received: '✓ Получено', pending: '⏳ Ожидается', partial: '◑ Частично' };
export const STATUS_LABEL: Record<AptStatus, string> = { free: 'Свободно', busy: 'Занято', clean: 'Уборка', tech: 'Тех.' };
export const UTIL = [['gwp', '💧 GWP'], ['energy', '🔥 Tbilisi Energy'], ['telmico', '⚡ Telmico'], ['tbilisi_clean', '🗑 Tbilisi Clean'], ['internet', '🌐 Internet']] as const;
export const HOUR_RATE = 16.7;

/** ApartFlow's fN with the currency sign in front: $1 234,5 / ₾57,89 */
export function cm(v: number | null | undefined, cur: string = 'USD'): string {
  const n = Number(v) || 0;
  const s = Math.abs(n).toLocaleString('ru-RU', { minimumFractionDigits: n % 1 ? 2 : 0, maximumFractionDigits: 2 });
  return (n < 0 ? '-' : '') + (cur === 'GEL' ? '₾' : '$') + s;
}
export const usd = (v: number) => cm(Math.round(v), 'USD');
export const mix = (m: Mix) => (['USD', 'GEL'] as Cur[]).filter((c) => m[c]).map((c) => cm(m[c], c)).join(' + ') || '—';
export function nightsLabel(n: number) { return n === 1 ? '1 ночь' : n >= 2 && n <= 4 ? `${n} ночи` : `${n} ночей`; }
export function dm(s?: string | null) { return s ? new Date(s.slice(0, 10) + 'T12:00:00').toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: '2-digit' }) : '—'; }

export type PerKind = 'month' | '3m' | 'year' | 'all';
export function ymNow() { return new Date().toLocaleDateString('en-CA', { timeZone: 'Asia/Tbilisi' }).slice(0, 7); }
export function addMonths(ym: string, n: number) {
  const [y, m] = ym.split('-').map(Number);
  const d = new Date(y, m - 1 + n, 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
}
export function ymLabel(ym: string) {
  const s = new Date(ym + '-15T12:00:00').toLocaleDateString('ru-RU', { month: 'long', year: 'numeric' }).replace(/\s?г\.$/, '');
  return s[0].toUpperCase() + s.slice(1);
}
/** the period as the API wants it, and its label */
export function periodOf(k: PerKind, month: string): Period & { label: string } {
  const cur = ymNow();
  if (k === 'all') return { label: 'За всё время' };
  if (k === 'month') return { month_from: month, month_to: month, label: ymLabel(month) };
  const from = addMonths(cur, k === '3m' ? -2 : -11);
  return { month_from: from, month_to: cur, label: `${ymLabel(from)} — ${ymLabel(cur)}` };
}
