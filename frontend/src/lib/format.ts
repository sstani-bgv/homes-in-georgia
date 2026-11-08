import type { LeadStatus, PropertyStatus, Role } from '../api/types';

export const ROLE_LABEL: Record<Role, string> = {
  superadmin: 'Суперадмин', admin: 'Админ', operator: 'Оператор', agent: 'Агент', recruit: 'Рекрут',
};

export const PROPERTY_STATUS: Record<PropertyStatus, string> = {
  new: 'Новый', taken: 'Взят', call_failed: 'Не дозвонился', owner_confirmed: 'Собственник', agent_marked: 'Агент',
  shoot_scheduled: 'Съёмка', media_uploaded: 'Фото загружены', review: 'На проверке', returned: 'Возвращён',
  published: 'Опубликован', rented: 'Сдан', archived: 'Архив',
};
export const PROPERTY_BOARD: PropertyStatus[] = [
  'taken', 'call_failed', 'owner_confirmed', 'shoot_scheduled', 'media_uploaded', 'review', 'returned', 'published', 'agent_marked', 'rented',
];

export const LEAD_STATUS: Record<LeadStatus, string> = {
  new: 'Новая', operator_taken: 'У оператора', agent_assigned: 'Агент назначен', slots_sent: 'Время отправлено',
  time_chosen: 'Время выбрано', viewing_done: 'Просмотр', deal_pending: 'Сделка', rented: 'Сдано', refused: 'Отказ', thinking: 'Думает',
};
export const LEAD_BOARD: LeadStatus[] = [
  'new', 'operator_taken', 'agent_assigned', 'slots_sent', 'time_chosen', 'viewing_done', 'deal_pending', 'rented', 'refused', 'thinking',
];
/** operator stages: an agent's board starts at agent_assigned (backend api/funnel2.py OPERATOR_STAGES) */
export const OPERATOR_STAGES: LeadStatus[] = ['new', 'operator_taken'];
/** leads a contract is drawn up for: «Оформить договор» in the lead card and the picker in /contracts */
export const CONTRACT_STAGES: LeadStatus[] = ['time_chosen', 'viewing_done', 'thinking', 'deal_pending', 'rented'];

export const OUTCOME_LABEL: Record<string, string> = {
  rented: 'Снимает', refused: 'Не подошло', thinking: 'Думает', reschedule: 'Перенос',
};
export const REFUSAL_LABEL: Record<string, string> = { price: 'Цена', location: 'Расположение', condition: 'Состояние', other: 'Другое' };
/** why an object is in the archive (backend models.ARCHIVE_REASONS) */
export const ARCHIVE_REASON: Record<string, string> = {
  owner_refused: 'Собственник отказался', rented_by_us: 'Сдали мы', rented_by_owner: 'Сдал сам', not_actual: 'Неактуально', duplicate: 'Дубль', other: 'Другое',
};
/** «Запрос оператору» (stage 6): kinds and field names (backend services/change_requests.py, properties.LOCKED_AFTER_VERIFY) */
export const CR_KIND: Record<string, string> = { edit: 'Изменение полей', archive: 'В архив', transfer_owner: 'Передать собственника', delete: 'Удаление → архив' };
export const CR_FIELD: Record<string, string> = {
  owner_name: 'Собственник', owner_phone: 'Телефон собственника', address: 'Адрес', cadastral_code: 'Кадастр', source_url: 'Ссылка на объявление',
  is_exclusive: 'Эксклюзив', price: 'Цена', currency: 'Валюта', deposit: 'Залог', sale_price: 'Цена продажи',
  archive_reason: 'Причина архива', archive_comment: 'Комментарий', duplicate_of: 'Дубль объекта', owner_agent: 'Агент собственника',
};

export const SOURCE_LABEL: Record<string, string> = {
  ss: 'ss.ge', myhome: 'myhome.ge', livo: 'livo.ge', korter: 'korter.ge', facebook: 'Facebook', instagram: 'Instagram', threads: 'Threads', telegram: 'Telegram', agency_channel: 'Канал', direct: 'Напрямую',
  referral: 'Рекомендация', import: 'Импорт', deal: 'Из сделки', other: 'Другое',
};

export const DISTRICTS = ['Ваке', 'Сабуртало', 'Ваке-Багеби', 'Мтацминда', 'Вере', 'Ортачала', 'Дидубе', 'Чугурети', 'Исани', 'Самгори', 'Глдани', 'Надзаладеви', 'Крцаниси', 'Дигоми', 'Старый город'];

export function money(v?: number | null, cur = 'USD'): string {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return '—';
  const s = Math.round(Number(v)).toLocaleString('ru-RU').replace(/,/g, ' ');
  if (cur === 'USD') return `$${s}`;
  if (cur === 'GEL') return `${s} ₾`;
  return `${s} ${cur}`;
}
export function num(v?: number | null): string {
  if (v === null || v === undefined) return '—';
  return Number(v).toLocaleString('ru-RU');
}

const pad = (n: number) => String(n).padStart(2, '0');
export function fmtDate(s?: string | null): string {
  if (!s) return '—';
  const d = new Date(s);
  if (Number.isNaN(d.getTime())) return s;
  return `${pad(d.getDate())}.${pad(d.getMonth() + 1)}.${d.getFullYear()}`;
}
export function fmtDateTime(s?: string | null): string {
  if (!s) return '—';
  const d = new Date(s);
  if (Number.isNaN(d.getTime())) return s;
  return `${pad(d.getDate())}.${pad(d.getMonth() + 1)} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
const WD = ['вс', 'пн', 'вт', 'ср', 'чт', 'пт', 'сб'];
export function fmtSlot(s: string): string {
  const d = new Date(s);
  const today = new Date();
  const tomorrow = new Date(Date.now() + 86400_000);
  const same = (a: Date, b: Date) => a.toDateString() === b.toDateString();
  const day = same(d, today) ? 'Сегодня' : same(d, tomorrow) ? 'Завтра' : `${WD[d.getDay()]} ${pad(d.getDate())}.${pad(d.getMonth() + 1)}`;
  return `${day} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
export function ago(s?: string | null): string {
  if (!s) return '—';
  const diff = (Date.now() - new Date(s).getTime()) / 1000;
  if (diff < 60) return 'только что';
  if (diff < 3600) return `${Math.floor(diff / 60)} мин назад`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} ч назад`;
  if (diff < 86400 * 7) return `${Math.floor(diff / 86400)} дн назад`;
  return fmtDate(s);
}
export function monthKey(d = new Date()): string { return `${d.getMonth() + 1}/${d.getFullYear()}`; }
const MONTHS = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь', 'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'];
export function monthLabel(key: string): string {
  const [m, y] = key.split('/').map(Number);
  return `${MONTHS[m - 1] || m} ${y}`;
}
export function lastMonths(n = 12): string[] {
  const out: string[] = [];
  const d = new Date();
  for (let i = 0; i < n; i++) { out.push(monthKey(new Date(d.getFullYear(), d.getMonth() - i, 1))); }
  return out;
}
export function bedroomsLabel(n?: number | null): string {
  if (n === null || n === undefined) return '';
  if (n === 0) return 'студия';
  return `${n} ${n === 1 ? 'спальня' : n < 5 ? 'спальни' : 'спален'}`;
}
export function initials(name?: string | null): string {
  if (!name) return '?';
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((p) => p[0]).join('').toUpperCase();
}
/** datetime-local value → ISO string with timezone */
export function localToIso(v: string): string { return new Date(v).toISOString(); }
export function isoToLocal(iso?: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
export function todayStr(): string { return new Date().toISOString().slice(0, 10); }
export function levelFor(n: number) { return n < 11 ? 50 : n < 101 ? 70 : 90; }
export function nextLevelAt(n: number) { return n < 11 ? 11 : n < 101 ? 101 : null; }

/** «Под этот объект подходят 4 активных клиента» (stage 10) */
export function demandLabel(n: number): string {
  const f = pluralForm(n);
  return `Под этот объект ${f === 0 ? 'подходит' : 'подходят'} ${n} ${['активный клиент', 'активных клиента', 'активных клиентов'][f]}`;
}

/** 0: 1/21/31…, 1: 2-4/22-24…, 2: the rest */
export function pluralForm(n: number): 0 | 1 | 2 {
  const m10 = n % 10, m100 = n % 100;
  return m10 === 1 && m100 !== 11 ? 0 : m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14) ? 1 : 2;
}
