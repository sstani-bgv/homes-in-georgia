// Property page: stepper (C2–C4), operator review (B7a), the post generator + publish (B7b), and the full object card: gallery, parameters,
// owner, where it is published, its ads on other sites, deals, history. Used by /objects/:id and /m/properties/:id.
import { useActionDialog, useConfirm } from '../../components/ActionDialog';
import { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Link, useNavigate } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Archive, ArrowRightLeft, Camera, CheckCircle2, Copy, FileText, ChevronDown, Clock, ExternalLink, Globe, Home, Lock, Pencil, Phone, PhoneOff, Play, RotateCcw, Send, Sparkles, Trash2, Upload } from 'lucide-react';
import { api, type PublishProgress } from '../../api';
import { ApiError, itemsOf, mediaUrl, thumbUrl, withKeepalive } from '../../api/client';
import type { Media, PostFields, Property } from '../../api/types';
import { FIELD_FILTER, atLeast } from '../../api/types';
import { useAuth } from '../../auth/AuthContext';
import { AiFlagBadges, CopyPhone, DupCheck, ExclusiveConflictBadge, PropertyStatusBadge, Timeline } from '../../components/domain';
import { errText, useToast } from '../../components/toast';
import { SimilarList } from '../../components/Similar';
import { Badge, Button, Card, Checkbox, Empty, ErrorBox, Field, Input, KV, Lightbox, Loading, Modal, Seg, Select, Switch, Textarea, DistrictSelect } from '../../components/ui';
import { ARCHIVE_REASON, agentLabel, distLabel, districtLabel, SOURCE_LABEL, bedroomsLabel, demandLabel, fmtDate, fmtDateTime, formatPhone, isoToLocal, localToIso, money, phoneInput } from '../../lib/format';
import { DealStatusBadge } from '../../pages/Deals';
import { DupsGate, RequestRow } from './requests';
import { OwnerSuggest } from '../../components/OwnerSuggest';
import { MorePhones, PhonesField, cleanPhones, phoneRows, samePhones } from '../../components/PhonesField';
import { cadastralError, cadastralHint, cadastralInput, cadastralNorm } from '../../lib/cadastral';
import { keepAwake, pool, shrinkPhoto } from '../../lib/upload';
import { addMonths, isoDate, parseIsoDate } from '../../lib/dates';
import { Autosaver, type AutosaveState } from '../../lib/autosave';
import { AddressField, type Point } from '../../components/AddressField';
import { t, lx, LOCALE } from '../../lib/i18n';

const REQUEST_SENT = t('Изменение ушло оператору на проверку');

// backend properties.MAX_MEDIA / MIN_PHOTOS / ALBUM_PHOTOS: the site shows every photo, the Telegram album the first 9 + the video
const MAX_PHOTO = 40, MAX_VIDEO = 1, MIN_PHOTOS = 9, ALBUM_PHOTOS = 9;
const ALBUM_MAX = 10; // Telegram album limit
const UPLOAD_PART = 8 * 1024 * 1024; // files over 2 parts go up in parts (backend properties.upload_part)
/** The Telegram album of a pick in order: the first ALBUM_PHOTOS photos + the video, ≤ ALBUM_MAX (backend notify._album). */
export function albumOf<T>(ids: T[], isVideo: (id: T) => boolean): T[] {
  let ph = 0;
  return ids.filter((id) => isVideo(id) || ++ph <= ALBUM_PHOTOS).slice(0, ALBUM_MAX);
}
/** Why the object can't go to review / be published yet: fewer than MIN_PHOTOS photos (backend require_min_photos). */
export function fewPhotos(media: { kind: string }[]): string | null {
  const n = media.filter((m) => m.kind === 'photo').length;
  return n < MIN_PHOTOS ? t('Нужно минимум {min} фото, сейчас {n}', { min: MIN_PHOTOS, n }) : null;
}
export const ROOMS = ['Гостиная', 'Кухня', 'Спальня 1', 'Спальня 2', 'Спальня 3', 'Санузел', 'Балкон', 'Терраса', 'Вид из окна', 'Прихожая', 'Гардероб', 'Кабинет', 'Подъезд', 'Фасад'];
/** Room tags under a photo: Управление → Настройки (photo_rooms), the built-in list until the backend sends one. */
export const roomsOf = (p?: Property) => (p?.photo_rooms?.length ? p.photo_rooms : ROOMS);
// «Кабинет» is also the nav's «My account» in the shared dictionary: the room gets its own translation
const CUSTOM_ROOM = '\u0000custom';
const roomT = (r: string) => (r === 'Кабинет' ? lx(r, 'კაბინეტი', 'Study') : t(r));
const STEPS = ['Взят', 'Звонок', 'Собственник', 'Съёмка', 'Пост и фото', 'Проверка', 'Публикация'].map((s) => t(s));

function stepOf(p: Property): number {
  switch (p.status) {
    case 'new': return 0;
    case 'taken': case 'call_failed': return 1;
    case 'agent_marked': return 2;
    case 'owner_confirmed': return p.source_type === 'direct' ? 4 : 3; // walk-in owner: no shoot booking, the post is next
    case 'shoot_scheduled': case 'media_uploaded': case 'returned': return 4;
    case 'review': return 5;
    case 'published': case 'rented': return 7;
    default: return 0;
  }
}

function Stepper({ current }: { current: number }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current?.querySelector('.step.current') as HTMLElement | null;
    if (el && ref.current) ref.current.scrollLeft = el.offsetLeft - ref.current.clientWidth / 2 + el.clientWidth / 2;
  }, [current]);
  return (
    <div className="stepper" ref={ref}>
      {STEPS.map((s, i) => (
        <div key={s} className={`step ${i < current ? 'done' : i === current ? 'current' : ''}`}>
          <span className="dot">{i < current ? <CheckCircle2 size={16} /> : i + 1}</span>
          <span className="step-label">{s}</span>
        </div>
      ))}
    </div>
  );
}

function useProp(id: number) {
  const qc = useQueryClient();
  const toast = useToast();
  const set = (p: Property) => {
    qc.setQueryData(['property', id], (old: Property | undefined) => ({ ...(old || {}), ...p }));
    qc.invalidateQueries({ queryKey: ['property', id] });
    qc.invalidateQueries({ queryKey: ['property-history', id] });
    qc.invalidateQueries({ queryKey: ['property-post', id] });
    qc.invalidateQueries({ queryKey: ['properties'] });
  };
  return { qc, toast, set };
}

function OwnerPhone({ p }: { p: Property }) {
  const { user, isStaff } = useAuth();
  const phone = p.owner?.phone || p.owner_phone;
  // the backend hides it only from agents on someone else's object (can_see_owner); otherwise there is no number
  if (!phone) return <span className="muted">{!isStaff && p.agent?.id !== user?.id ? t('скрыт') : t('не указан')}</span>;
  const note = p.owner?.phones?.[0]?.note;
  return (
    <span className="col" style={{ display: 'inline-flex', gap: 0 }}>
      <span className="row gap-sm row-wrap" style={{ display: 'inline-flex' }}><a href={`tel:${phone.replace(/[^+\d]/g, '')}`} style={{ fontWeight: 700, whiteSpace: 'nowrap' }}>{formatPhone(phone)}</a>{note && <span className="muted" style={{ fontSize: 12 }}>{note}</span>}<CopyPhone phone={phone} /></span>
      <MorePhones phones={p.owner?.phones} />
    </span>
  );
}

/** «Взят» from the feed, nothing done yet: the object goes back when this runs out (worker job take_idle) */
function IdleTimer({ at }: { at: string }) {
  const [, tick] = useState(0);
  useEffect(() => { const id = setInterval(() => tick((x) => x + 1), 30_000); return () => clearInterval(id); }, []);
  const n = Math.max(0, Math.ceil((new Date(at).getTime() - Date.now()) / 60_000));
  const left = n >= 120 ? t('{n} ч', { n: Math.floor(n / 60) }) : t('{n} мин', { n });
  return <div className="alert alert-warn">⏳ {t('вернётся в «Новые объявления» через {left}, если собственник не подтвердит', { left })}</div>;
}

/** David 11-24: «Собственник закреплён за #Tag» — offer the agent a request to the operator to move that owner to him
 * (backend change_requests.request_owner); any other error: the toast as before. */
function useOwnerTaken(p: Property) {
  const { toast } = useProp(p.id);
  const confirm = useConfirm();
  return async (e: unknown, phone?: string) => {
    if (e instanceof ApiError && e.code === 'owner_taken' && phone) {
      if (await confirm(`${e.message}. ${t('Отправить оператору запрос передать этого собственника вам? Агента собственника тоже уведомим.')}`)) {
        try { await api.properties.ownerRequest(p.id, phone); toast(t('Запрос ушёл оператору')); } catch (x) { toast(errText(x), true); }
      }
      return;
    }
    toast(errText(e), true);
  };
}

function CallStep({ p }: { p: Property }) {
  const { toast, set } = useProp(p.id);
  const [reached, setReached] = useState(false);
  const [isOwner, setIsOwner] = useState<'owner' | 'agent'>('owner');
  const [name, setName] = useState(p.owner?.name || '');
  const [phones, setPhones] = useState(() => phoneRows(p.owner));  // David 11-18: the ad's number + the one given on the call
  const onTaken = useOwnerTaken(p);
  const fail = useMutation({ mutationFn: () => api.properties.call(p.id, false), onSuccess: (r) => { set(r); toast(t('Бот напомнит позвонить через 2 часа')); }, onError: (e) => toast(errText(e), true) });
  const save = useMutation({
    mutationFn: async () => {
      await api.properties.call(p.id, true);
      const list = cleanPhones(phones);
      return api.properties.owner(p.id, { is_owner: isOwner === 'owner', owner_name: name, owner_phone: list[0]?.phone, owner_phones: list });
    },
    onSuccess: (r) => { set(r); toast(isOwner === 'owner' ? t('Собственник подтверждён') : t('Объект в базе с пометкой «агент»')); },
    onError: (e) => onTaken(e, cleanPhones(phones)[0]?.phone),
  });
  return (
    <div className="col">
      <div className="row row-wrap">
        <Phone size={18} color="var(--brand)" />
        <span className="label">{t('Собственник')}</span>
        <b>{p.owner?.name || '—'}</b>
        <OwnerPhone p={p} />
        {p.call_attempts ? <Badge tone="grey">{t('попыток: {n}', { n: p.call_attempts })}</Badge> : null}
      </div>
      {p.idle_return_at && <IdleTimer at={p.idle_return_at} />}
      {p.status === 'call_failed' && p.next_call_at && <div className="alert alert-info">{t('Не дозвонились · бот напомнит в {at}', { at: fmtDateTime(p.next_call_at) })}</div>}
      <div className="alert alert-info" style={{ fontWeight: 500 }}>
        <Sparkles size={16} style={{ flex: 'none', marginTop: 2 }} />
        <span>{t('Подсказка: представьтесь, уточните, сдаёт ли собственник сам, свободна ли квартира и когда удобно провести съёмку. Спросите про животных и минимальный срок.')}</span>
      </div>
      {!reached ? (
        <div className="row row-wrap">
          <Button onClick={() => setReached(true)}><Phone size={16} />{t('Дозвонился')}</Button>
          <Button variant="secondary" loading={fail.isPending} onClick={() => fail.mutate()}><PhoneOff size={16} />{t('Не дозвонился')}</Button>
        </div>
      ) : (
        <div className="card card-pad col" style={{ background: 'var(--brand-tint)' }}>
          <span className="label">{t('Кто ответил')}</span>
          <Seg value={isOwner} onChange={setIsOwner} options={[{ value: 'owner', label: t('Собственник') }, { value: 'agent', label: t('Агент') }]} />
          <div className="form-grid">
            <Field label={t('Имя')} className="span2"><Input value={name} onChange={(e) => setName(e.target.value)} /></Field>
            <PhonesField value={phones} onChange={setPhones} />
          </div>
          {isOwner === 'agent' && <div className="muted" style={{ fontSize: 12 }}>{t('Объект останется в базе с пометкой «агент», а номер попадёт в список агентов.')}</div>}
          <div className="row"><Button loading={save.isPending} onClick={() => save.mutate()}>{t('Сохранить')}</Button><Button variant="ghost" onClick={() => setReached(false)}>{t('Отмена')}</Button></div>
        </div>
      )}
    </div>
  );
}

/** What the owner said, besides "yes": thinking (bot reminds to call back), rented it out himself, won't work with us. */
function OwnerOutcomes({ p }: { p: Property }) {
  const ask = useActionDialog();
  const { toast, set } = useProp(p.id);
  const [thinkOpen, setThinkOpen] = useState(false);
  const [until, setUntil] = useState(() => isoToLocal(new Date(Date.now() + 2 * 864e5).toISOString()));
  const [comment, setComment] = useState('');
  const done = (msg: string) => (r: Property) => { set(r); toast(msg); setThinkOpen(false); setComment(''); };
  const fail = (e: unknown) => toast(errText(e), true);
  const think = useMutation({ mutationFn: () => api.properties.thinking(p.id, localToIso(until)!, comment), onSuccess: done(t('Бот напомнит перезвонить')), onError: fail });
  const [rentedOpen, setRentedOpen] = useState(false);
  const refused = useMutation({ mutationFn: (c: string) => api.properties.refused(p.id, c), onSuccess: done(t('Объект вернулся в ленту: другой агент увидит, что вы звонили')), onError: fail });
  const refuse = async () => {
    const comment = await ask(t('Собственник не хочет работать. Комментарий для следующего агента (необязательно):'), { label: t('Комментарий') });
    if (comment !== null) refused.mutate(comment);
  };
  return (
    <div className="col gap-sm mt">
      {p.status !== 'shoot_scheduled' && p.status !== 'call_failed' && p.next_call_at && (
        <div className="alert alert-info">{t('Собственник думает · бот напомнит перезвонить {at}', { at: fmtDateTime(p.next_call_at) })}</div>
      )}
      <div className="row row-wrap gap-sm">
        {p.status !== 'shoot_scheduled' && <Button variant="secondary" size="sm" onClick={() => setThinkOpen(true)}><Clock size={14} />{t('Думает')}</Button>}
        <Button variant="secondary" size="sm" onClick={() => setRentedOpen(true)}><Home size={14} />{t('Сдал сам')}</Button>
        <Button variant="ghost" size="sm" loading={refused.isPending} onClick={refuse}><PhoneOff size={14} />{t('Не хочет работать')}</Button>
      </div>
      {rentedOpen && <OwnerLeaseModal p={p} onClose={() => setRentedOpen(false)} />}
      <Modal open={thinkOpen} onClose={() => setThinkOpen(false)} title={t('Собственник думает')}>
        <div className="col gap-lg">
          <Field label={t('Когда перезвонить')}><Input type="datetime-local" value={until} onChange={(e) => setUntil(e.target.value)} /></Field>
          <Field label={t('Комментарий')}><Input value={comment} placeholder={t('Например: есть свои клиенты, ждёт их ответ')} onChange={(e) => setComment(e.target.value)} /></Field>
          <div className="muted" style={{ fontSize: 12 }}>{t('Объект остаётся за вами. Если собственник согласится — назначайте съёмку, если сдаст сам — нажмите «Сдал сам».')}</div>
          <Button block disabled={!until} loading={think.isPending} onClick={() => think.mutate()}>{t('Сохранить')}</Button>
        </div>
      </Modal>
    </div>
  );
}

const dayIso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
// clamped to month end (31 Aug + 6 → 28/29 Feb), the same rule as the contract wizard's end date
const plusMonths = (iso: string, m: number) => { const a = parseIsoDate(iso); return a ? isoDate(addMonths(a, m)) : ''; };

/** «Сдано с / по» of «Собственник сдал»: both required, «по» after «с»; +6 / +12 мес from the start (Stanislav 11-16) */
function useLease(p?: Property) {
  const [from, setFrom] = useState(() => p?.rent_from?.slice(0, 10) || dayIso(new Date()));
  const [to, setTo] = useState(p?.rent_to?.slice(0, 10) || '');
  const ok = !!from && !!to && to > from;
  const fields = (
    <>
      <div className="form-grid">
        <Field label={t('Сдано с')}><Input type="date" value={from} onChange={(e) => setFrom(e.target.value)} /></Field>
        <Field label={t('Сдано по')} hint={from && to && to <= from ? t('«Сдано по» должно быть позже «Сдано с»') : undefined}><Input type="date" min={from || undefined} value={to} onChange={(e) => setTo(e.target.value)} /></Field>
      </div>
      <div className="row gap-sm">
        <Button variant="secondary" size="sm" disabled={!from} onClick={() => setTo(plusMonths(from, 6))}>{t('+6 мес')}</Button>
        <Button variant="secondary" size="sm" disabled={!from} onClick={() => setTo(plusMonths(from, 12))}>{t('+12 мес')}</Button>
      </div>
    </>
  );
  return { ok, fields, lease: { rent_from: from, rent_to: to } };
}

/** «Сдал сам» on a working object, or the dates of an object archived «Собственник сдал» without them (datesOnly). */
function OwnerLeaseModal({ p, onClose, datesOnly }: { p: Property; onClose: () => void; datesOnly?: boolean }) {
  const { toast, set } = useProp(p.id);
  const { ok, fields, lease } = useLease(datesOnly ? p : undefined);
  const [price, setPrice] = useState('');
  const [comment, setComment] = useState('');
  const m = useMutation({
    mutationFn: () => datesOnly ? api.properties.update(p.id, lease) : api.properties.rentedSelf(p.id, { ...lease, price: price || undefined, comment: comment || undefined }),
    onSuccess: (r) => { set(r); toast(datesOnly ? t('Даты аренды сохранены') : t('Отмечено: собственник сдал сам')); onClose(); },
    onError: (e) => toast(errText(e), true),
  });
  return (
    <Modal open onClose={onClose} title={datesOnly ? t('Даты аренды') : t('Собственник сдал')}>
      <div className="col gap-lg">
        {fields}
        {!datesOnly && <>
          <Field label={t('Цена, $ (необязательно)')}><Input inputMode="numeric" value={price} onChange={(e) => setPrice(e.target.value.replace(/\D/g, ''))} /></Field>
          <Field label={t('Комментарий')}><Input value={comment} onChange={(e) => setComment(e.target.value)} /></Field>
        </>}
        {!datesOnly && <div className="muted" style={{ fontSize: 12 }}>{t('Объект уйдёт в архив. Перед окончанием аренды бот напомнит позвонить собственнику, в календаре будет «Освобождается».')}</div>}
        <Button block disabled={!ok} loading={m.isPending} onClick={() => m.mutate()}>{t('Сохранить')}</Button>
      </div>
    </Modal>
  );
}

function ShootStep({ p }: { p: Property }) {
  const { toast, set } = useProp(p.id);
  const [at, setAt] = useState(isoToLocal(p.shoot_at));
  const [dup, setDup] = useState<string | null>(p.dup_check || null);
  const m = useMutation({
    mutationFn: () => api.properties.shoot(p.id, localToIso(at)),
    onSuccess: (r) => { setDup(r.dup_check || null); set(r); toast(t('Съёмка назначена')); },
    onError: (e) => toast(errText(e), true),
  });
  return (
    <div className="col">
      <div className="row row-wrap" style={{ alignItems: 'flex-end' }}>
        <Field label={t('Дата и время съёмки')}><Input type="datetime-local" value={at} onChange={(e) => setAt(e.target.value)} /></Field>
        <Button disabled={!at} loading={m.isPending} onClick={() => m.mutate()}><Camera size={16} />{t('Назначить съёмку')}</Button>
      </div>
      <DupCheck value={dup} />
    </div>
  );
}

type GenForm = {
  is_exclusive: boolean; district: string; address: string; price: string; bedrooms: string; area: string; floor: string;
  total_floors: string; owner_name: string; owner_phone: string; owner_id?: number; post_fields: PostFields; point: Point | null;
};
const BEDROOM_CHIPS: Record<string, string> = { 0: t('Студия'), 1: '1', 2: '2', 3: '3', 4: '4', 5: '5+' };
// the old generator's sale block (backend SALE_AMENITIES): its own type order, beds up to 6+, only these amenities
export const SALE_TYPES = ['Apartment', 'House', 'Land', 'Commercial', 'Retail', 'Business', 'Cafe', 'Restaurant', 'BeautySalon', 'Warehouses', 'Manufacture', 'Duplex', 'Townhouse'];
// the site's sale sections (hge.ge services): resale, new buildings, commercial for sale
const SALE_SERVICE: Record<string, string> = { sale: t('Вторичка'), new: t('Новостройка'), commercial: t('Коммерция') };
export const SALE_BEDROOM_CHIPS: Record<string, string> = { ...BEDROOM_CHIPS, 5: '5', 6: '6+' };
export const SALE_AMENITIES = ['Balcony', 'Terrace', 'Courtyard', 'ParkingPlace', 'Conditioner', 'Dishwasher', 'Oven', 'Stove', 'Bath', 'Shower', 'Elevator', 'Fireplace'];
const AMENITY_FIXED = ['Elevator', 'Oven', 'ParkingPlace', 'Conditioner']; // backend FIXED_AMENITIES: ✖️ in a rent post when absent
const str = (v: unknown) => (v === null || v === undefined ? '' : String(v));
const genOf = (p: Property): GenForm => ({
  is_exclusive: !!p.is_exclusive, district: str(p.district), address: str(p.address), price: str(p.price), bedrooms: str(p.bedrooms),
  area: str(p.area), floor: str(p.floor), total_floors: str(p.total_floors),
  owner_name: str(p.owner?.name), owner_phone: str(p.owner?.phone), post_fields: { deal: 'Rent', ...(p.post_fields || {}) },
  // the agent's point, else the origin ad's pin to check (saved only once he moves / picks it)
  point: p.lat != null && p.lng != null ? { lat: p.lat, lng: p.lng } : p.listing?.lat != null && p.listing?.lng != null ? { lat: p.listing.lat, lng: p.listing.lng } : null,
});
/** what changed in the generator since the last save, in PATCH /properties shape */
function genDiff(a: GenForm, b: GenForm): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  const num = (x: string) => (x.trim() === '' ? null : Number(x));
  (['district', 'address'] as const).forEach((k) => { if (a[k] !== b[k]) out[k] = b[k].trim() || null; });
  (['price', 'bedrooms', 'area', 'floor', 'total_floors'] as const).forEach((k) => { if (a[k] !== b[k]) out[k] = num(b[k]); });
  if (a.point?.lat !== b.point?.lat || a.point?.lng !== b.point?.lng) { out.lat = b.point?.lat ?? null; out.lng = b.point?.lng ?? null; }
  if (a.is_exclusive !== b.is_exclusive) out.is_exclusive = b.is_exclusive;
  if (b.owner_id && b.owner_id !== a.owner_id) out.owner_id = b.owner_id;
  else if (a.owner_name !== b.owner_name || a.owner_phone !== b.owner_phone) { out.owner_name = b.owner_name; out.owner_phone = b.owner_phone; }
  if (JSON.stringify(a.post_fields) !== JSON.stringify(b.post_fields)) out.post_fields = b.post_fields;
  return out;
}

/** The caption as Telegram shows it: `[text](url)` becomes a link (notify.post_html does the same on publish). */
const postPreview = (t: string) => t.split(/(\[[^\]]+\]\([^)\s]+\))/g).map((x, i) => {
  const m = x.match(/^\[([^\]]+)\]\(([^)\s]+)\)$/);
  return m ? <a key={i} href={m[2]} target="_blank" rel="noreferrer">{m[1]}</a> : x;
});

/** One-of tag group as chips (the old generator's buttons); a tap on the picked one clears it. */
export function Chips({ options, value, onChange }: { options: Record<string, string>; value?: string; onChange: (v?: string) => void }) {
  return (
    <div className="row row-wrap gap-sm">
      {Object.entries(options).map(([v, l]) => (
        <button key={v} type="button" className={`chip ${value === v ? 'active' : ''}`} aria-pressed={value === v}
          onClick={() => onChange(value === v ? undefined : v)}>{value === v ? <CheckCircle2 size={14} /> : null}{l}</button>
      ))}
    </div>
  );
}

/** The agent's step after the shoot: the agency's post generator («Заполни текст для публикации») on one page, in its
 * order — place, flat, amenities, price and terms, owner — then photos and videos, then the post text it builds.
 * Every change is saved (debounced) and the post below is rebuilt by the backend; «Отправить на модерацию» only when
 * nothing required is missing (backend properties.missing_fields). */
function PostGeneratorStep({ p, canEdit }: { p: Property; canEdit: boolean }) {
  const confirm = useConfirm();
  const { toast, set, qc } = useProp(p.id);
  const [f, setF] = useState<GenForm>(() => genOf(p));
  const [metroM, setMetroM] = useState<number | null>(null);  // the auto-picked station's distance, shown by the select
  const fileRef = useRef<HTMLInputElement>(null);
  const exRef = useRef<HTMLDivElement>(null);
  useEffect(() => { if (f.is_exclusive && !p.is_exclusive) exRef.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }); }, [f.is_exclusive]); // eslint-disable-line react-hooks/exhaustive-deps
  const media = p.media || [];
  const checklist = p.checklist || {};
  // one save at a time, the confirmed form moves only on success (lib/autosave); a failed one stays dirty with a retry
  const [saveState, setSaveState] = useState<AutosaveState>({ saving: false, error: null });
  const latest = useRef({ set, toast });
  latest.current = { set, toast };
  const [ctl] = useState(() => new Autosaver<GenForm>(f, {
    diff: genDiff,
    send: (d, keepalive) => {
      const go = () => api.properties.update(p.id, d as Parameters<typeof api.properties.update>[1]);
      return keepalive ? withKeepalive(go) : go();
    },
    // the answer is merged into the query cache only; the form keeps what the user typed meanwhile (it never reloads from p)
    onSaved: (r) => latest.current.set(r as Property),
    onError: (e) => latest.current.toast(errText(e), true),
    onState: setSaveState,
  }));
  ctl.update(f);
  const flush = () => ctl.flush();
  useEffect(() => {
    if (!canEdit) return;
    const t = setTimeout(() => { flush().catch(() => undefined); }, f.is_exclusive !== ctl.confirmed.is_exclusive ? 0 : 700); // the exclusive toggle at once: its signing link comes from the save
    return () => clearTimeout(t);
  }, [f]); // eslint-disable-line react-hooks/exhaustive-deps
  // what is still in the 700 ms debounce goes out when the tab hides / the page goes away / the step unmounts
  useEffect(() => {
    if (!canEdit) return;
    const away = () => { if (document.visibilityState === 'hidden') ctl.flush({ keepalive: true }).catch(() => undefined); };
    const hide = () => { ctl.flush({ keepalive: true }).catch(() => undefined); };
    document.addEventListener('visibilitychange', away);
    window.addEventListener('pagehide', hide);
    return () => { document.removeEventListener('visibilitychange', away); window.removeEventListener('pagehide', hide); hide(); };
  }, [canEdit, ctl]);
  const upd = <K extends keyof GenForm>(k: K, v: GenForm[K]) => setF((x) => ({ ...x, [k]: v }));
  const pf = f.post_fields;
  const setPf = (k: string, v: unknown) => setF((x) => ({ ...x, post_fields: { ...x.post_fields, [k]: v === '' || v === undefined ? undefined : v } }));
  // the deposit shows the price (empty = as price, backend post_text); a deposit the agent typed equal to the old price
  // keeps following it, any other one he set stays
  const setPrice = (v: string) => setF((x) => {
    const dep = x.post_fields.deposit;
    const follow = dep !== undefined && String(dep) === x.price;
    return { ...x, price: v, post_fields: follow ? { ...x.post_fields, deposit: v === '' ? undefined : Number(v) } : x.post_fields };
  });
  const toggleMany = (k: 'amenities' | 'periods', a: string) => { const cur = pf[k] || []; setPf(k, cur.includes(a) ? cur.filter((x) => x !== a) : [...cur, a]); };
  const sale = pf.deal === 'Sale';
  const one = (k: string) => POST_ONE[k][1];
  const AMENITY_ALL = Object.keys(POST_MANY.amenities[1]);
  const AMENITY_REST = AMENITY_ALL.filter((a) => !AMENITY_FIXED.includes(a));

  const nPhoto = media.filter((m) => m.kind === 'photo').length;
  const nVideo = media.filter((m) => m.kind === 'video').length;
  const full = nPhoto >= MAX_PHOTO && nVideo >= MAX_VIDEO;
  const [queue, setQueue] = useState<{ name: string; state: 'wait' | 'prep' | 'up' | 'done' | 'err'; pct?: number; msg?: string; mb?: number }[]>([]);
  const busy = queue.some((q) => q.state === 'wait' || q.state === 'prep' || q.state === 'up');
  useEffect(() => (busy ? keepAwake() : undefined), [busy]);
  const doneN = queue.filter((q) => q.state === 'done').length;
  /** The ones that fit MAX_PHOTO photos + 1 video go up 3 at a time, photos resized first, the video last (the photos
   * are done in seconds, the video's 100+ MB then has the whole uplink); one request per file, a dropped connection
   * is retried twice. */
  const uploadFiles = async (picked: File[]) => {
    let ph = nPhoto, vd = nVideo;
    const isVideo = (x: File) => x.type.startsWith('video/') || /\.(mp4|mov|m4v|webm|avi|mkv)$/i.test(x.name);
    const fit = picked.filter((x) => (isVideo(x) ? ++vd <= MAX_VIDEO : ++ph <= MAX_PHOTO)).sort((a, b) => Number(isVideo(a)) - Number(isVideo(b)));
    if (fit.length < picked.length) toast(t('Не добавлено {n}: у объекта максимум {p} фото и {v} видео', { n: picked.length - fit.length, p: MAX_PHOTO, v: MAX_VIDEO }), true);
    if (!fit.length) return;
    setQueue(fit.map((x) => ({ name: x.name, state: 'wait', mb: x.size >= 20e6 ? Math.round(x.size / 1e6) : undefined })));
    const mark = (i: number, ch: Partial<(typeof queue)[number]>) => setQueue((q) => q.map((x, j) => (j === i ? { ...x, ...ch } : x)));
    let failed = 0;
    const got: number[][] = [];  // the new media ids per picked file
    await pool(fit, 3, async (x, i) => {
      mark(i, { state: 'prep' });
      try {
        const small = await shrinkPhoto(x);
        // a big file (an iPhone video) in 8 MB parts: one 2-3 minute request over a phone used to die midway (Irakli 11-18),
        // now a drop costs one part, retried, and the server says where to go on from
        const sendParts = async (): Promise<Property & { uploaded_ids?: number[] }> => {
          const uid = crypto.randomUUID().replace(/-/g, '');
          let off = 0, fails = 0;
          for (;;) {
            const end = Math.min(off + UPLOAD_PART, small.size), from = off;
            mark(i, { state: 'up' });
            try {
              const r = await api.properties.uploadMediaPart(p.id, small.slice(from, end), { upload_id: uid, name: small.name, offset: from, total: small.size },
                (f) => mark(i, { pct: Math.round(((from + f * (end - from)) / small.size) * 100) }));
              fails = 0;
              if ('received' in r) { off = r.received; continue; }
              return r;
            } catch (e) {
              if (e instanceof ApiError && e.code === 'upload_gap') { off = Number(e.data?.have ?? 0); continue; }
              const dropped = e instanceof ApiError && (e.status === 0 || e.code === 'upload_broken' || e.status >= 502);
              if (!dropped || ++fails > 6) throw e;
              mark(i, { state: 'prep', msg: t('связь прервалась, повторяем…') });
              await new Promise((ok) => setTimeout(ok, 2000 * fails));
            }
          }
        };
        const send = async (tries: number): Promise<Property & { uploaded_ids?: number[] }> => {
          if (small.size > 2 * UPLOAD_PART) return sendParts();
          mark(i, { state: 'up', pct: 0 });
          try {
            return await api.properties.uploadMediaProgress(p.id, small, (f) => mark(i, { pct: Math.round(f * 100) }));
          } catch (e) {
            if (!(e instanceof ApiError && (e.status === 0 || e.code === 'upload_broken')) || tries >= 2) throw e;
            mark(i, { state: 'prep', msg: t('связь прервалась, повторяем…') });
            await new Promise((ok) => setTimeout(ok, 3000 * (tries + 1)));
            return send(tries + 1);
          }
        };
        const r = await send(0);
        got[i] = r.uploaded_ids || [];
        set(r);
        mark(i, { state: 'done', pct: 100 });
      } catch (e) { failed++; mark(i, { state: 'err', msg: errText(e) }); }
    });
    // the server numbers files in the order they finish (3 go up at once): put them back in the order they were picked
    const newIds = got.flat();
    if (newIds.length > 1) {
      try {
        const fresh = shotsOf(await api.properties.get(p.id));
        const cur = [...fresh.filter((m) => m.kind !== 'video'), ...fresh.filter((m) => m.kind === 'video')];
        const isNew = new Set(newIds), kind = new Map(cur.map((m) => [Number(m.id), m.kind]));
        const queueOf = (k: string | undefined) => newIds.filter((id) => kind.get(id) === k);
        const q = new Map<string | undefined, number[]>();
        const ids = cur.map((m) => Number(m.id)).map((id) => {
          if (!isNew.has(id)) return id;
          if (!q.has(kind.get(id))) q.set(kind.get(id), queueOf(kind.get(id)));
          return q.get(kind.get(id))!.shift()!;
        });
        if (ids.some((id, j) => id !== Number(cur[j].id))) set(await api.properties.mediaOrder(p.id, ids));
      } catch { /* the order stays as uploaded; the agent can drag it */ }
    }
    refresh();
    if (!failed) { toast(fit.length > 1 ? t('Загружено файлов: {n}', { n: fit.length }) : t('Файл загружен')); setQueue([]); }
  };
  const refresh = () => qc.invalidateQueries({ queryKey: ['property', p.id] });
  const del = useMutation({ mutationFn: (mid: number) => api.properties.deleteMedia(p.id, mid), onSuccess: refresh });
  const order = useMutation({ mutationFn: (ids: number[]) => api.properties.mediaOrder(p.id, ids), onSuccess: set, onError: (e) => { toast(errText(e), true); refresh(); } });
  const room = useMutation({ mutationFn: ([mid, r]: [number, string | null]) => api.properties.mediaRoom(p.id, mid, r), onSuccess: refresh, onError: (e) => toast(errText(e), true) });
  const check = useMutation({ mutationFn: (c: Record<string, boolean>) => api.properties.checklist(p.id, c), onSuccess: set });
  const confirmOther = useConfirm();
  const askRoom = useActionDialog();
  // «Свой вариант…» under a photo: the agent's own tag, saved on that photo only (not added to the settings list)
  const pickRoom = async (id: number, r: string | null) => {
    const v = r === CUSTOM_ROOM ? (await askRoom(t('Своя категория для этого фото'), { label: t('Категория'), required: true }))?.trim().slice(0, 60) : r;
    if (v !== undefined && v !== '') room.mutate([id, v]);
  };
  const submit = useMutation({
    mutationFn: async () => {
      await flush();
      try {
        return await api.properties.submit(p.id);
      } catch (e) {  // the owner's other flat in the same house (Irakli 11-22): the agent confirms, the operator sees it
        if (!(e instanceof ApiError) || e.code !== 'object_exists') throw e;
        if (!(await confirmOther(`${e.message}. ${t('Это другая квартира того же собственника? Тогда отправим, а оператор увидит вашу отметку.')}`))) throw e;
        return api.properties.submit(p.id, true);
      }
    },
    onSuccess: (r) => { set(r); toast(t('Отправлено на модерацию')); }, onError: (e) => toast(errText(e), true),
  });
  const post = useQuery({ queryKey: ['property-post', p.id], queryFn: () => api.properties.post(p.id) });
  const rooms = roomsOf(p);
  const done = rooms.filter((r) => checklist[r] || media.some((m) => m.room === r)).length;
  const roomsOn = p.room_picker !== false;
  const missing = p.missing || [];
  const dirty = Object.keys(genDiff(ctl.confirmed, f)).length > 0 || saveState.saving;
  const failed = !!saveState.error && !saveState.saving && dirty;
  const ro = !canEdit;

  return (
    <div className="col gap-lg">
      {p.status === 'returned' && p.review_comment && <div className="alert alert-warn">{t('Оператор вернул:')} {p.review_comment}</div>}
      {p.shoot_at && <div className="muted">{t('Съёмка:')} <b>{fmtDateTime(p.shoot_at)}</b></div>}
      {p.status === 'shoot_scheduled' && <DupCheck value={p.dup_check} />}
      <fieldset disabled={ro} className="col gap-lg" style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}>
        <div className="row row-between row-wrap gap-sm">
          <span className="label">{t('Заполни текст для публикации')}</span>
          <Seg value={pf.deal || 'Rent'} onChange={(d) => setPf('deal', d)} options={[{ value: 'Rent', label: t('🏠 Аренда') }, { value: 'Sale', label: t('💼 Продажа') }]} />
        </div>

        <div className="gen-block">
          <span className="gen-title">{t('1 · Основное')}</span>
          <Switch checked={f.is_exclusive} onChange={(v) => upd('is_exclusive', v)} label="❗️ Exclusive listing" />
          {f.is_exclusive && <div ref={exRef} className="outcome-card"><ExclusiveContract p={p} canEdit={canEdit} nested /></div>}
          <Field label={t('Описание (необязательно)')}><Input value={pf.headline || ''} placeholder={t('Например: No one lived')} onChange={(e) => setPf('headline', e.target.value)} /></Field>
          <Field label={t('Район')}><DistrictSelect value={f.district} onChange={(v) => upd('district', v)} /></Field>
          <Field label={t('🚇 Метро')} hint={metroM != null && pf.metro ? t('≈ {d} от точки на карте', { d: distLabel(metroM) }) : undefined}>
            <Select value={pf.metro || ''} placeholder="—" onChange={(e) => { setMetroM(null); setPf('metro', e.target.value); }} options={opts(one('metro'))} /></Field>
          <div className="field"><span className="label">{t('Адрес')}</span>
            <AddressField value={f.address} onChange={(v) => upd('address', v)} point={f.point} onPoint={(v) => upd('point', v)} disabled={ro}
              onFacts={(r) => { setMetroM(r.metro_m); setF((x) => ({ ...x, district: r.district || x.district, post_fields: { ...x.post_fields, metro: r.metro || undefined } })); }} /></div>
        </div>

        <div className="gen-block">
          <span className="gen-title">{t('2 · Объект')}</span>
          <Field group label={t('Тип')}><Chips options={sale ? Object.fromEntries(SALE_TYPES.map((t) => [t, one('prop_type')[t]])) : one('prop_type')} value={pf.prop_type} onChange={(v) => setPf('prop_type', v)} /></Field>
          <Field group label={t('Спальни')}><Chips options={sale ? SALE_BEDROOM_CHIPS : BEDROOM_CHIPS} value={f.bedrooms} onChange={(v) => upd('bedrooms', v ?? '')} /></Field>
          <Field group label={t('Здание')}><Chips options={one('building')} value={pf.building} onChange={(v) => setPf('building', v)} /></Field>
          {sale && <Field group label={t('Состояние')}><Chips options={one('condition')} value={pf.condition} onChange={(v) => setPf('condition', v)} /></Field>}
          {sale && <Field group label={t('Раздел на сайте')}><Chips options={SALE_SERVICE} value={pf.service || 'sale'} onChange={(v) => setPf('service', v ?? 'sale')} /></Field>}
          {sale && pf.service === 'new' && <Field label={t('Сдача дома')} hint={t('Например: Q4 2027')}><Input value={pf.completion || ''} onChange={(e) => setPf('completion', e.target.value)} /></Field>}
          <div className="form-grid">
            <Field label={t('Площадь (м²)')}><Input type="number" inputMode="decimal" value={f.area} onChange={(e) => upd('area', e.target.value)} /></Field>
            <Field group label={t('Этаж / Всего')}>
              <div className="row gap-sm" style={{ flexWrap: 'nowrap' }}>
                <Input type="number" inputMode="numeric" aria-label={t('Этаж')} value={f.floor} onChange={(e) => upd('floor', e.target.value)} />
                <span className="muted">/</span>
                <Input type="number" inputMode="numeric" aria-label={t('Всего этажей')} value={f.total_floors} onChange={(e) => upd('total_floors', e.target.value)} />
              </div>
            </Field>
            {!sale && <Field label={t('Жильцы макс.')}><Input type="number" inputMode="numeric" min={1} value={pf.tenants ?? ''} onChange={(e) => setPf('tenants', e.target.value === '' ? undefined : Number(e.target.value))} /></Field>}
          </div>
          <Field group label={t('Дизайн')}><Chips options={one('design')} value={pf.design} onChange={(v) => setPf('design', v)} /></Field>
          <Field group label={t('Ванные комнаты')}><Chips options={one('bathrooms')} value={pf.bathrooms} onChange={(v) => setPf('bathrooms', v)} /></Field>
          <Field group label={t('Отопление')}><Chips options={one('heating')} value={pf.heating} onChange={(v) => setPf('heating', v)} /></Field>
          {((sale ? [[t('Удобства'), SALE_AMENITIES]] : [[t('✅/✖️ в посте'), AMENITY_FIXED], [t('Остальные (только ✅ если есть)'), AMENITY_REST]]) as [string, string[]][]).map(([label, keys]) => (
            <Field group key={label} label={label}>
              <div className="row row-wrap gap-sm">
                {keys.map((a) => {
                  const on = (pf.amenities || []).includes(a);
                  return <button key={a} type="button" className={`chip ${on ? 'active' : ''}`} aria-pressed={on} onClick={() => toggleMany('amenities', a)}>{on ? <CheckCircle2 size={14} /> : null}{POST_MANY.amenities[1][a]}</button>;
                })}
              </div>
            </Field>
          ))}
          {!sale && <Field group label={t('Питомцы')}><Chips options={one('pets')} value={pf.pets} onChange={(v) => setPf('pets', v)} /></Field>}
        </div>

        <div className="gen-block">
          <span className="gen-title">{t('3 · Цена')}</span>
          {sale ? <Field label={t('Цена ($)')} hint={t('Диапазон цены (#Price…) ставится в пост сам по цене')}><Input type="number" inputMode="numeric" value={pf.sale_price ?? ''}
            onChange={(e) => { const v = e.target.value; setF((x) => ({ ...x, price: v, post_fields: { ...x.post_fields, sale_price: v === '' ? undefined : Number(v) } })); }} /></Field> : <>
            <div className="form-grid">
              <Field label={t('Цена ($)')}><Input type="number" inputMode="numeric" value={f.price} onChange={(e) => setPrice(e.target.value)} /></Field>
              <Field label={t('Депозит ($)')} hint={t('Равен цене, можно изменить. 0 — без депозита')}><Input type="number" inputMode="numeric" min={0} value={pf.deposit ?? f.price} onChange={(e) => setPf('deposit', e.target.value === '' ? undefined : Number(e.target.value))} /></Field>
            </div>
            <Field group label={t('Срок аренды')}>
              <div className="row row-wrap gap-sm">
                {Object.entries(POST_MANY.periods[1]).map(([a, l]) => {
                  const on = (pf.periods || []).includes(a);
                  return <button key={a} type="button" className={`chip ${on ? 'active' : ''}`} aria-pressed={on} onClick={() => toggleMany('periods', a)}>{on ? <CheckCircle2 size={14} /> : null}{l}</button>;
                })}
              </div>
            </Field>
          </>}
        </div>

        <div className="gen-block">
          <span className="gen-title">{t('4 · Команда')}</span>
          <div className="muted" style={{ fontSize: 13 }}>{t('Агент:')} <b>{agentLabel(p.agent)}</b> {t('— тег агента ставится в пост сам')}</div>
          <div className="form-grid">
            <Field label={t('Имя собственника')}><Input value={f.owner_name} onChange={(e) => setF((x) => ({ ...x, owner_name: e.target.value, owner_id: undefined }))} /></Field>
            <Field label={t('Телефон собственника')}><Input type="tel" inputMode="tel" value={f.owner_phone} onChange={(e) => { const v = phoneInput(e); setF((x) => ({ ...x, owner_phone: v, owner_id: undefined })); }} /></Field>
          </div>
          {!p.owner && !f.owner_id && (f.owner_name || f.owner_phone) && (
            <OwnerSuggest query={f.owner_phone || f.owner_name} onPick={(o) => setF((x) => ({ ...x, owner_id: o.id, owner_name: o.name || '', owner_phone: o.phone || '' }))} />
          )}
        </div>
      </fieldset>


      <div className="gen-block">
        <div className="row row-between row-wrap gap-sm">
          <span className="gen-title">{t('Фото и видео')}</span>
          <span className="media-count num">{t('Фото')} {nPhoto}/{MAX_PHOTO} · {t('Видео')} {nVideo}/{MAX_VIDEO}</span>
        </div>
        {canEdit && (
          <>
            <input ref={fileRef} type="file" accept="image/*,video/*" multiple hidden onChange={(e) => { const fl = Array.from(e.target.files || []); e.target.value = ''; if (fl.length) uploadFiles(fl); }} />
            <Button block loading={busy} disabled={full} onClick={() => fileRef.current?.click()}><Upload size={16} />{full ? t('Загружено максимум файлов') : t('Загрузить фото и видео')}</Button>
            <div className="muted" style={{ fontSize: 12 }}>
              {full ? t('Чтобы заменить файл, удалите его.')
                : `${t('От {min} до {p} фото и {v} видео — выберите в галерее сразу все.', { min: MIN_PHOTOS, p: MAX_PHOTO, v: MAX_VIDEO })}${roomsOn ? t(' Комнату под фото указывать необязательно.') : ''}`}
              {' '}{t('В пост Telegram уходят первые {n} фото и видео, на сайте — все фото. Порядок — перетаскиванием.', { n: ALBUM_PHOTOS })}
            </div>
            {queue.length > 0 && (
              <div className="upload-queue">
                <div className="row row-between"><b>{t('Загружено {n} из {total}', { n: doneN, total: queue.length })}</b>{busy && <span className="muted">{t('не закрывайте страницу и не сворачивайте браузер')}</span>}</div>
                <progress className="uq-total" max={queue.length} value={doneN} />
                {queue.map((q, i) => (
                  <div key={i} className={`uq uq-${q.state}`}>
                    <div className="row row-between gap-sm">
                      <span className="uq-name">{q.name}</span>
                      <span className="uq-state">{q.state === 'wait' ? t('в очереди') : q.state === 'prep' ? q.msg || t('сжимаем…') : q.state === 'up' ? `${q.pct ?? 0}%${q.mb ? ` · ${t('{n} МБ', { n: q.mb })}` : ''}` : q.state === 'done' ? t('готово') : q.msg || t('ошибка')}</span>
                    </div>
                    {(q.state === 'up' || q.state === 'done') && <progress max={100} value={q.pct ?? 0} />}
                  </div>
                ))}
                {!busy && <Button size="sm" variant="ghost" onClick={() => setQueue([])}>{t('Скрыть')}</Button>}
              </div>
            )}
          </>
        )}
        <MediaGrid p={p} grouped onOrder={canEdit ? (ids) => order.mutate(ids.map(Number)) : undefined} onDelete={canEdit ? (id) => del.mutate(Number(id)) : undefined} onRoom={canEdit && roomsOn ? pickRoom : undefined} />
        {roomsOn && <>
          <div className="row row-between"><span className="label">{t('Что снято · необязательно')}</span><span className="muted num" style={{ fontWeight: 700 }}>{done}/{rooms.length}</span></div>
          <div className="row row-wrap gap-sm">
            {rooms.map((r) => {
              const on = !!checklist[r] || media.some((m) => m.room === r);
              return <button key={r} type="button" className={`chip ${on ? 'active' : ''}`} disabled={!canEdit} onClick={() => check.mutate({ ...checklist, [r]: !checklist[r] })}>{on ? <CheckCircle2 size={14} /> : null}{roomT(r)}</button>;
            })}
          </div>
        </>}
      </div>

      <div className="gen-block">
        <div className="row row-between"><span className="gen-title">{t('5 · Превью поста')}</span>{failed
          ? <Button size="sm" variant="ghost" style={{ color: 'var(--accent)' }} onClick={() => { flush().catch(() => undefined); }}><RotateCcw size={14} />{t('не сохранено — повторить')}</Button>
          : dirty && <span className="faint" style={{ fontSize: 12 }}>{t('сохраняем…')}</span>}</div>
        {post.isLoading ? <Loading /> : <div className="post-preview">{postPreview(post.data?.text || '—')}</div>}
        <div className="muted" style={{ fontSize: 12 }}>{t('Пост собирается из полей выше. Оператор проверит его, при необходимости поправит и опубликует.')}</div>
      </div>

      {canEdit && (
        <>
          {missing.length > 0 && !dirty && <div className="alert alert-warn" style={{ display: 'block' }}>{t('Чтобы отправить, заполните:')} <b>{missing.join(', ')}</b></div>}
          {fewPhotos(media) && <div className="alert alert-warn" style={{ display: 'block' }}>{fewPhotos(media)}</div>}
          <Button size="lg" variant="ok" disabled={!!fewPhotos(media) || missing.length > 0 || dirty} loading={submit.isPending}
            onClick={async () => {
              if (await confirm(t('Отправить объект на модерацию? Пост, фото и видео больше нельзя будет менять.'))) submit.mutate();
            }}>
            <Send size={18} />{t('Отправить на модерацию')}
          </Button>
        </>
      )}
    </div>
  );
}

export type Shot = Pick<Media, 'kind' | 'room'> & { id: number | string; src: string };
const shotsOf = (p: Property): Shot[] => (p.media || []).map((m) => ({ id: m.id, kind: m.kind, room: m.room, src: m.url || m.path }));

/** off / onToggle: the post picker — a checkbox per tile puts the file in the post or leaves it out (dimmed). */
export function MediaGrid({ p, items, grouped, onDelete, onRoom, onOrder, off, onToggle }: { p?: Property; items?: Shot[]; grouped?: boolean; onDelete?: (id: Shot['id']) => void; onRoom?: (id: number, room: string | null) => void; onOrder?: (ids: Shot['id'][]) => void; off?: Set<Shot['id']>; onToggle?: (id: Shot['id']) => void }) {
  const all = items || (p ? shotsOf(p) : []);
  // photos first, then videos: the lightbox walks them in that order, and a saved order is this one (the album's)
  const [local, setLocal] = useState<Shot[] | null>(null); // the order just set, until the server's comes back
  const ids = all.map((m) => m.id).join(',');
  useEffect(() => setLocal(null), [p?.media, ids]);
  const media = local || [...all.filter((m) => m.kind !== 'video'), ...all.filter((m) => m.kind === 'video')];
  const [open, setOpen] = useState<number | null>(null);
  // drag-and-drop on pointer events (mouse and touch): a mouse drags after a few px; a finger after a long press
  // (a plain swipe still scrolls). The tile moves within its group; the order is saved once, on drop.
  const [drag, setDrag] = useState<{ from: number; over: number; x: number; y: number; w: number } | null>(null);
  const press = useRef<{ i: number; x: number; y: number; id: number; touch: boolean; timer?: number; el: HTMLElement } | null>(null);
  const dragged = useRef(false);  // swallow the click (lightbox) that ends a drag
  const move = (from: number, to: number) => {
    if (!onOrder || to < 0 || to >= media.length || from === to || media[from].kind !== media[to].kind) return;
    const next = [...media];
    next.splice(to, 0, next.splice(from, 1)[0]);
    setLocal(next);
    onOrder(next.map((m) => m.id));
  };
  useEffect(() => {  // while dragging, a finger must not scroll the page (touchmove has to be non-passive for that)
    if (!drag) return;
    const stop = (e: TouchEvent) => e.preventDefault();
    document.addEventListener('touchmove', stop, { passive: false });
    return () => document.removeEventListener('touchmove', stop);
  }, [!!drag]);  // eslint-disable-line react-hooks/exhaustive-deps
  const start = (x: number, y: number) => {
    const s = press.current!;
    dragged.current = true;
    navigator.vibrate?.(15);
    setDrag({ from: s.i, over: s.i, x, y, w: s.el.getBoundingClientRect().width });
  };
  const overAt = (x: number, y: number, from: number) => {
    const el = document.elementFromPoint(x, y)?.closest<HTMLElement>('[data-mi]');
    const j = el ? Number(el.dataset.mi) : -1;
    return j >= 0 && media[j].kind === media[from].kind ? j : null;
  };
  const capture = (el: HTMLElement, id: number) => { try { el.setPointerCapture(id); } catch { /* pointer already gone */ } };
  const end = () => { if (press.current?.timer) clearTimeout(press.current.timer); press.current = null; };
  const handlers = (i: number) => !onOrder ? {} : {
    onPointerDown: (e: React.PointerEvent<HTMLDivElement>) => {
      if (e.button !== 0 || (e.target as HTMLElement).closest('button, select, label, input')) return;
      dragged.current = false;
      const touch = e.pointerType !== 'mouse';
      press.current = { i, x: e.clientX, y: e.clientY, id: e.pointerId, touch, el: e.currentTarget };
      if (touch) press.current.timer = window.setTimeout(() => press.current && start(press.current.x, press.current.y), 350);
    },
    onPointerMove: (e: React.PointerEvent<HTMLDivElement>) => {
      const s = press.current;
      if (!s || s.id !== e.pointerId) return;
      if (!drag) {
        const far = Math.hypot(e.clientX - s.x, e.clientY - s.y) > (s.touch ? 10 : 5);
        if (s.touch) { if (far) end(); return; }  // moved before the long press: it's a scroll
        if (!far) return;
        capture(s.el, e.pointerId);
        start(e.clientX, e.clientY);
        return;
      }
      if (!s.el.hasPointerCapture(e.pointerId)) capture(s.el, e.pointerId);
      const over = overAt(e.clientX, e.clientY, drag.from);
      setDrag({ ...drag, x: e.clientX, y: e.clientY, over: over ?? drag.over });
    },
    onPointerUp: () => { if (drag) move(drag.from, drag.over); setDrag(null); end(); },
    onPointerCancel: () => { setDrag(null); end(); },
    onDragStart: (e: React.DragEvent) => e.preventDefault(),  // no native image drag (it would cancel the pointer)
    onContextMenu: (e: React.MouseEvent) => { if (press.current?.touch || drag) e.preventDefault(); },  // long press ≠ menu
    onKeyDown: (e: React.KeyboardEvent) => {  // keyboard fallback: ← / → move the focused tile
      if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
      e.preventDefault();
      move(i, i + (e.key === 'ArrowLeft' ? -1 : 1));
    },
  };
  // while dragging the grid shows the order it would drop into; the dragged tile's slot is the placeholder
  const shown = drag ? (() => { const n = [...media]; n.splice(drag.over, 0, n.splice(drag.from, 1)[0]); return n; })() : media;
  if (!media.length) return <div className="muted" style={{ fontSize: 13 }}>{t('Файлов пока нет')}</div>;
  const tile = (m: Shot, i: number) => {
    const ghost = drag !== null && shown[i] === media[drag.from];
    return (
    <div className={`ph ${ghost ? 'drop-slot' : ''} ${onOrder ? 'sortable' : ''} ${off?.has(m.id) ? 'ph-off' : ''}`} key={m.id} data-mi={i} tabIndex={onOrder ? 0 : undefined}
      aria-label={onOrder ? t('Файл: перетащите или ← → чтобы поменять порядок') : undefined}
      onClick={() => { if (dragged.current) { dragged.current = false; return; } setOpen(media.indexOf(m)); }} style={{ cursor: onOrder ? 'grab' : 'zoom-in' }}
      {...handlers(i)}>
      {onOrder && <span className="num">{i - (m.kind === 'video' ? shown.findIndex((x) => x.kind === 'video') : 0) + 1}</span>}
      {m.kind === 'video' ? <>
        <video src={`${mediaUrl(m.src)}#t=0.1`} muted playsInline preload="metadata" style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
        <span className="video-badge"><Play size={12} fill="currentColor" />{t('Видео')}</span>
      </> : <img src={thumbUrl(m.src)} alt={m.room || ''} loading="lazy" />}
      {onRoom && m.kind !== 'video' ? (
        <span className="room-pick" onClick={(e) => e.stopPropagation()}>
          <span className={`badge badge-blue ${m.room ? '' : 'no-room'}`}>{m.room ? roomT(m.room) : t('Комната?')}<ChevronDown size={12} /></span>
          <select value={m.room || ''} aria-label={t('Комната')} onChange={(e) => onRoom(Number(m.id), e.target.value || null)}>
            <option value="">{t('Не указана')}</option>
            {(m.room && !roomsOf(p).includes(m.room) ? [...roomsOf(p), m.room] : roomsOf(p)).map((r) => <option key={r} value={r}>{roomT(r)}</option>)}
            <option value={CUSTOM_ROOM}>{t('Свой вариант…')}</option>
          </select>
        </span>
      ) : m.room && m.kind !== 'video' && <span className="room badge badge-blue">{roomT(m.room)}</span>}
      {onToggle && (
        <label className="pick-box" onClick={(e) => e.stopPropagation()} title={off?.has(m.id) ? t('Не в посте') : t('В посте')}>
          <input type="checkbox" checked={!off?.has(m.id)} onChange={() => onToggle(m.id)} aria-label={t('В посте')} />
        </label>
      )}
      {onDelete && <button className="del btn btn-danger icon-btn btn-sm" style={{ width: 28, height: 28 }} onClick={(e) => { e.stopPropagation(); onDelete(m.id); }} aria-label={t('Удалить')}><Trash2 size={14} /></button>}
    </div>
    );
  };
  const dm = drag && media[drag.from];
  const nPhoto = media.findIndex((m) => m.kind === 'video') === -1 ? media.length : media.findIndex((m) => m.kind === 'video');
  return (
    <>
      {open !== null && <Lightbox items={media} index={open} onIndex={setOpen} onClose={() => setOpen(null)} />}
      {grouped ? <>
        {nPhoto > 0 && <><span className="label">{t('Фото')} · {nPhoto}</span><div className="photo-strip">{shown.slice(0, nPhoto).map((m, i) => tile(m, i))}</div></>}
        {nPhoto < media.length && <><span className="label">{t('Видео')} · {media.length - nPhoto}</span><div className="photo-strip">{shown.slice(nPhoto).map((m, i) => tile(m, nPhoto + i))}</div></>}
      </> : <div className="photo-strip">{shown.map(tile)}</div>}
      {drag && dm && createPortal(
        <div className="drag-ghost" style={{ width: drag.w, height: drag.w, left: drag.x - drag.w / 2, top: drag.y - drag.w / 2 }}>
          {dm.kind === 'video' ? <video src={`${mediaUrl(dm.src)}#t=0.1`} muted playsInline preload="metadata" /> : <img src={thumbUrl(dm.src)} alt="" />}
        </div>, document.body)}
    </>
  );
}

/** «Найти копии» (David 11-22): agents' copies of the object on myhome / ss.ge / korter — by its district, street,
 * rooms and area, and by the photos of its linked ad; a few minutes, then «Где ещё выложен» shows them. */
function MyhomeCopies({ p }: { p: Property }) {
  const qc = useQueryClient();
  const toast = useToast();
  const run = useMutation({
    mutationFn: () => api.properties.myhomeCopies(p.id),
    onSuccess: () => {
      toast(t('Ищем копии на myhome, ss.ge и korter — через пару минут список обновится'));
      for (const ms of [75_000, 180_000, 300_000]) setTimeout(() => { qc.invalidateQueries({ queryKey: ['similar'] }); qc.invalidateQueries({ queryKey: ['property', p.id] }); }, ms);
    },
    onError: (e) => toast(errText(e), true),
  });
  return <Button variant="secondary" size="sm" loading={run.isPending} onClick={() => run.mutate()}>{t('Найти копии')}</Button>;
}

/** «Сравнить по ссылке» (David 11-22): an ad of a competitor / an owner on myhome / ss.ge / livo / korter scored against the object. */
function CompareLink({ p }: { p: Property }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [url, setUrl] = useState('');
  const run = useMutation({
    mutationFn: () => api.properties.compareLink(p.id, url.trim()),
    onSuccess: () => {  // the ad is the object's now (David 11-23): the card lists it, the dup search runs with its photos
      setUrl('');
      qc.invalidateQueries({ queryKey: ['property', p.id] });
      qc.invalidateQueries({ queryKey: ['similar'] });
      setTimeout(() => qc.invalidateQueries({ queryKey: ['similar'] }), 60_000);
      setTimeout(() => qc.invalidateQueries({ queryKey: ['similar'] }), 180_000);
    },
    onError: (e) => toast(errText(e), true),
  });
  const r = run.data;
  const verdict = r?.level === 'same' ? t('дубль / почти точно') : r?.level === 'maybe' ? t('вероятный, нужна проверка') : t('скорее другой объект');
  const facts = r ? [r.address, r.area ? `${r.area} м²` : null, r.floor != null ? `${t('этаж')} ${r.floor}` : null,
    r.bedrooms != null ? `${t('спален')}: ${r.bedrooms}` : null, r.price_usd ? `$${r.price_usd}` : null].filter(Boolean).join(' · ') : '';
  return (
    <div style={{ marginTop: 12, borderTop: '1px solid var(--border, #e5e7eb)', paddingTop: 12 }}>
      <div className="muted" style={{ fontSize: 13, marginBottom: 6 }}>{t('Сравнить с объявлением по ссылке (myhome, ss.ge, livo, korter)')}</div>
      <div style={{ display: 'flex', gap: 8 }}>
        <div style={{ flex: 1 }}><Input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://www.myhome.ge/pr/…" /></div>
        <Button variant="secondary" size="sm" loading={run.isPending} disabled={!url.trim()} onClick={() => run.mutate()}>{t('Сравнить')}</Button>
      </div>
      {r && (
        <div style={{ marginTop: 10, fontSize: 14 }}>
          <div><b>{r.pct ?? 0}% · {verdict}</b> · <a href={r.url} target="_blank" rel="noreferrer">{t('Открыть объявление')}</a></div>
          {facts && <div className="muted" style={{ marginTop: 4 }}>{facts}</div>}
          {r.why && <div style={{ marginTop: 4 }}>{r.why}</div>}
          {!!r.photos?.length && <div style={{ display: 'flex', gap: 6, marginTop: 6 }}>{r.photos.map((src) => <img key={src} src={src} alt="" style={{ width: 72, height: 54, objectFit: 'cover', borderRadius: 6 }} />)}</div>}
          <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>{r.linked
            ? t('Объявление привязано к объекту («Объявления на других сайтах»). По его фото ищем все копии — через пару минут они появятся в «Похожих».')
            : t('Это объявление уже привязано к объекту #{n}', { n: r.other_object ?? '' })}</div>
        </div>
      )}
    </div>
  );
}

/** Own photos/videos, or the origin ad's photos until the shoot. */
function Gallery({ p }: { p: Property }) {
  const own = shotsOf(p);
  const fromAd = (p.listing?.photos || []).map((src, i): Shot => ({ id: `l${i}`, kind: 'photo', src }));
  const toast = useToast();
  const { user } = useAuth();
  const staffOnly = atLeast(user, 'operator');  // Irakli 11-22: superadmin, admin, operators
  const [busy, setBusy] = useState(false);
  const originals = async () => {  // the photos as uploaded, without the channel watermark: for myhome / ss by hand
    setBusy(true);
    try {
      const a = document.createElement('a');
      a.href = URL.createObjectURL(await api.properties.originals(p.id));
      a.download = `object_${p.id}_originals.zip`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 4000);
    } catch (e) { toast(errText(e), true); } finally { setBusy(false); }
  };
  return (
    <Card label={t('Фото и видео')} title={own.length ? `${t('Файлы объекта')} · ${own.length}` : fromAd.length ? t('Фото из объявления') : t('Галерея')}
      actions={staffOnly && own.length ? <div className="row row-wrap gap-sm">
        {own.some((x) => x.kind === 'photo') && <Button variant="secondary" size="sm" loading={busy} onClick={originals}>{t('Скачать оригиналы')}</Button>}
        {/* David 11-24: a video over 50 MB can't go to the originals chat (bot limit) — the operators take it here */}
        {own.filter((x) => x.kind === 'video').map((v, i, vs) => (
          <a key={v.id} className="btn btn-secondary btn-sm" href={mediaUrl(v.src)} download={`object_${p.id}_video${vs.length > 1 ? `_${i + 1}` : ''}.mp4`}>
            {t('Скачать видео')}{vs.length > 1 ? ` ${i + 1}` : ''}</a>))}
      </div> : undefined}>
      <MediaGrid items={own.length ? own : fromAd} />
    </Card>
  );
}

const PUB_STATUS: Record<string, [string, 'ok' | 'grey' | 'warn']> = { published: [t('опубликован'), 'ok'], removed: [t('снят'), 'grey'], draft: [t('черновик'), 'warn'] };

/** «Где опубликован»: our posts per channel. Only the TG channel exists for now; site / IG / FB rows come later. */
function Publications({ p }: { p: Property }) {
  const pubs = (p.publications || []).filter((x) => x.channel === 'tg');
  return (
    <Card label={t('Каналы')} title={t('Где опубликован')}>
      {!pubs.length ? <div className="muted" style={{ fontSize: 13 }}>{t('Пока нигде не опубликован')}</div> : pubs.map((x) => (
        <div className="list-row" key={x.id}>
          <div className="grow">
            <div style={{ fontWeight: 700 }}>{t('Telegram-канал')}</div>
            <div className="muted" style={{ fontSize: 12 }}>{fmtDateTime(x.status === 'removed' ? x.removed_at : x.published_at)}</div>
            {x.warning && <div className="alert alert-warn" style={{ fontSize: 12, marginTop: 4 }}>⚠️ {x.warning}. {t('Добавьте бота админом с правами публикации и удаления.')}</div>}
            {x.delete_error && <div className="alert alert-warn" style={{ fontSize: 12, marginTop: 4 }}>⚠️ {t('Не удалось удалить пост в канале — удалите его вручную')}</div>}
          </div>
          <Badge tone={PUB_STATUS[x.status]?.[1] || 'grey'}>{PUB_STATUS[x.status]?.[0] || x.status}</Badge>
          {x.url && <a className="btn btn-secondary btn-sm" href={x.url} target="_blank" rel="noreferrer"><ExternalLink size={14} />{t('Пост')}</a>}
        </div>
      ))}
    </Card>
  );
}

/** «Объявления на других сайтах»: the outside ads of this flat. Links only for staff and the object's agent. */
function Ads({ p }: { p: Property }) {
  const ads = p.listings || [];
  return (
    <Card label={t('Дубли')} title={t('Объявления на других сайтах')}>
      {!ads.length ? <div className="muted" style={{ fontSize: 13 }}>{t('Других объявлений не найдено')}</div> : ads.map((a) => (
        <div className="list-row" key={a.listing_id}>
          <div className="grow">
            <div className="row gap-sm" style={{ fontWeight: 700 }}>{SOURCE_LABEL[a.source || ''] || a.source || '—'}{a.origin && <Badge tone="grey">{t('первоисточник')}</Badge>}</div>
            <div className="muted" style={{ fontSize: 12 }}>{[money(a.price_usd), a.first_seen ? t('с {d}', { d: fmtDate(a.first_seen) }) : null].filter(Boolean).join(' · ')}</div>
          </div>
          {a.url ? <a className="btn btn-secondary btn-sm" href={a.url} target="_blank" rel="noreferrer"><ExternalLink size={14} />{t('Открыть')}</a> : null}
        </div>
      ))}
    </Card>
  );
}

const srcName = (s?: string | null) => SOURCE_LABEL[s || ''] || s || t('другой сайт');

/** An exclusive still advertised on its linked site ads (Stanislav 11-24: above «Похожие объекты», where the ads are listed
 * with «Попросить снять» / «Проверено: снято»): its publication stays closed until the operator checks them. */
function ExclusiveHangs({ p }: { p: Property }) {
  const sites = [...new Set((p.copies || []).filter((a) => a.blocking).map((a) => a.where))];
  if (!sites.length) return null;
  return <div className="alert alert-warn" style={{ fontSize: 13 }}>{t('Эксклюзив висит на {s} — публикация закрыта до «Проверено: снято». Задача агенту: попросить собственника снять объявление.', { s: sites.join(', ') })}</div>;
}

/** Stage 7: an object typed in a deal form; its deal is confirmed only after the operator checks it here. */
function NeedsCheck({ p }: { p: Property }) {
  const { isStaff } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const m = useMutation({
    mutationFn: () => api.properties.verify(p.id),
    onSuccess: (r) => { qc.setQueryData(['property', p.id], r); qc.invalidateQueries({ queryKey: ['deal'] }); toast(t('Объект проверен · сделку можно подтвердить')); },
    onError: (e) => toast(errText(e), true),
  });
  return (
    <div className="alert alert-warn">
      <span className="grow">{t('Новый объект из сделки — проверить: адрес, собственник и телефон, кадастр и ссылку.')} {isStaff ? t('Сделка по нему ждёт этой проверки.') : t('Оператор проверит его перед подтверждением сделки.')}</span>
      {isStaff && <Button size="sm" variant="ok" loading={m.isPending} onClick={() => m.mutate()}><CheckCircle2 size={14} />{t('Объект проверен')}</Button>}
    </div>
  );
}

/** Stage 10: active clients this object fits; owner first: after the call «Показать объект клиентам»
 * opens it to clients with the ad's photos and sends it to them (at most 10, never twice to one client). */
function Demand({ p }: { p: Property }) {
  const confirm = useConfirm();
  const { isStaff } = useAuth();
  const { qc, toast, set } = useProp(p.id);
  const { data: d } = useQuery({ queryKey: ['property-demand', p.id], queryFn: () => api.properties.demand(p.id) });
  const m = useMutation({
    mutationFn: () => api.properties.fastTrack(p.id),
    onSuccess: (r) => {
      set(r);
      qc.invalidateQueries({ queryKey: ['property-demand', p.id] });
      toast(r.sent ? t('Объект отправлен клиентам: {n}', { n: r.sent }) : t('Объект открыт клиентам · отправлять сейчас некому'));
    },
    onError: (e) => toast(errText(e), true),
  });
  if (!d || (!d.count && !d.fast_track_at)) return null;
  return (
    <Card label={t('Спрос')} title={d.count ? demandLabel(d.count) : t('Подходящих активных клиентов сейчас нет')}>
      <div className="muted" style={{ fontSize: 13 }}>{t('По анкетам клиентов: район, бюджет, спальни, животные, срок. Активные: писали боту за {n} дней, сделки ещё нет.', { n: d.active_days })}</div>
      {d.fast_track_at ? (
        <div className="alert alert-ok mt">{t('Собственник согласен · объект показан клиентам {at} · отправлено: {n}', { at: fmtDateTime(d.fast_track_at), n: d.pushed })}</div>
      ) : d.can_fast_track && (
        <div className="col gap-sm mt">
          <Button variant="ok" loading={m.isPending} onClick={async () => { if (await confirm(t('Собственник согласен показать квартиру клиентам? Объект уйдёт подходящим клиентам в бот.'))) m.mutate(); }}>
            <Send size={16} />{t('Показать объект клиентам ({n})', { n: d.count })}
          </Button>
          <div className="muted" style={{ fontSize: 12 }}>{t('Нажимайте, когда собственник согласился на показы: объект уйдёт в бот подходящим клиентам с фото из объявления')}</div>
        </div>
      )}
      {d.clients.length > 0 && (
        <div className="mt">
          {d.clients.map((c, i) => {
            const name = isStaff && c.id ? c.name || (c.username ? `@${c.username}` : `${t('Клиент')} #${c.id}`) : `${t('Клиент')} ${i + 1}`;
            const body = (
              <>
                <div className="grow">
                  <div style={{ fontWeight: 600 }}>{name}</div>
                  <div className="muted" style={{ fontSize: 12 }}>{c.profile || t('анкета без условий')}</div>
                </div>
                {c.sent && <Badge tone="ok">{t('отправлено')}</Badge>}
              </>
            );
            // client cards are staff only (/clients/:id); agents see anonymous rows
            return isStaff && c.id
              ? <Link className="list-row" key={c.id} to={`/clients/${c.id}`} style={{ color: 'inherit' }}>{body}</Link>
              : <div className="list-row" key={c.id ?? i}>{body}</div>;
          })}
        </div>
      )}
    </Card>
  );
}

/** «Сдан» (David 2026-11-10): for an object whose deal is not in the CRM (the old base) the agent types the tenant and the
 * lease dates by hand; the end goes to the calendar, the agent is reminded 30 / 7 / 1 days before. */
function Lease({ p }: { p: Property }) {
  const qc = useQueryClient();
  const toast = useToast();
  const init = () => ({ rent_from: p.rent_from || '', rent_to: p.rent_to || '', tenant_name: p.tenant_name || '', tenant_phone: p.tenant_phone || '', tenant_tg: p.tenant_tg || '' });
  const [f, setF] = useState(init);
  const [edit, setEdit] = useState(false);
  useEffect(() => setF(init()), [p.id, p.rent_to, p.tenant_name]); // eslint-disable-line react-hooks/exhaustive-deps
  const save = useMutation({
    mutationFn: () => api.properties.update(p.id, f),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['property', p.id] }); qc.invalidateQueries({ queryKey: ['properties'] }); setEdit(false); toast(f.rent_to ? t('Сохранено: окончание аренды в календаре, напомним за месяц') : t('Сохранено')); },
    onError: (e) => toast(errText(e), true),
  });
  if ((p as { locked?: boolean }).locked || (p.deals?.length && !p.rent_to)) return null; // a deal in the CRM already has the tenant and the dates
  const set = (k: keyof ReturnType<typeof init>) => (e: React.ChangeEvent<HTMLInputElement>) => setF({ ...f, [k]: e.target.value });
  const has = p.rent_to || p.tenant_name;
  if (!edit) return (
    <Card label={t('Аренда')} title={t('Сдан')}>
      {has ? (
        <KV items={[[t('Срок'), p.rent_from || p.rent_to ? `${p.rent_from ? fmtDate(p.rent_from) : '…'} – ${p.rent_to ? fmtDate(p.rent_to) : '…'}` : '—'],
          [t('Арендатор'), [p.tenant_name, p.tenant_phone, p.tenant_tg].filter(Boolean).join(' · ') || '—']]} />
      ) : <div className="muted" style={{ fontSize: 13 }}>{t('Нет данных о сдаче. Если объект сдан не через CRM (например, из старой базы), добавьте клиента и срок: окончание договора попадёт в календарь, за месяц придёт напоминание.')}</div>}
      <div className="mt"><Button size="sm" variant="secondary" onClick={() => setEdit(true)}><Pencil size={14} />{has ? t('Изменить') : t('Добавить')}</Button></div>
    </Card>
  );
  return (
    <Card label={t('Аренда')} title={t('Сдан')}>
      <div className="form-grid">
        <Field label={t('Сдан с')}><Input type="date" value={f.rent_from} onChange={set('rent_from')} /></Field>
        <Field label={t('Договор до')}><Input type="date" value={f.rent_to} onChange={set('rent_to')} /></Field>
        <Field label={t('Арендатор')}><Input value={f.tenant_name} onChange={set('tenant_name')} placeholder={t('Имя')} /></Field>
        <Field label={t('Телефон')}><Input value={f.tenant_phone} onChange={(e) => setF({ ...f, tenant_phone: phoneInput(e) })} inputMode="tel" placeholder="+995 5XX XXX XXX" /></Field>
        <Field label="Telegram"><Input value={f.tenant_tg} onChange={set('tenant_tg')} placeholder="@username" /></Field>
      </div>
      <div className="row mt">
        <Button loading={save.isPending} onClick={() => save.mutate()}>{t('Сохранить')}</Button>
        <Button variant="ghost" onClick={() => { setF(init()); setEdit(false); }}>{t('Отмена')}</Button>
      </div>
    </Card>
  );
}

function Deals({ p, compact }: { p: Property; compact?: boolean }) {
  if (!p.deals?.length) return null;
  return (
    <Card label={t('Сделки')} title={`${t('Сделки по объекту')} · ${p.deals.length}`}>
      {p.deals.map((d) => (
        <Link className="list-row" key={d.id} to={compact ? `/m/deals/${d.id}` : `/deals/${d.id}`} style={{ color: 'inherit' }}>
          <div className="grow">
            <div style={{ fontWeight: 700 }}>{t('Сделка')} #{d.id} · {money(d.price)}</div>
            <div className="muted" style={{ fontSize: 12 }}>{[d.agent, d.rent_from ? `${fmtDate(d.rent_from)} – ${fmtDate(d.rent_to)}` : fmtDate(d.created_at)].filter(Boolean).join(' · ')}</div>
          </div>
          <DealStatusBadge s={d.status} />
        </Link>
      ))}
    </Card>
  );
}

const CAPTION_MAX = 1024; // Telegram caption limit for a post with media

/** The post generator in the card (replaces the agency's HTML generator): the caption built by the backend from the
 * object (post_text), editable; the files to post (drag to sort, the checkbox puts one in or out, a tap opens it); one
 * «Опубликовать» to the checked targets — «На сайт» and / or «В Telegram» (Stanislav 11-15). */
function PostComposer({ p }: { p: Property }) {
  const { toast, set } = useProp(p.id);
  const draft = useQuery({ queryKey: ['property-post', p.id], queryFn: () => api.properties.post(p.id) });
  // a published object: the target it is not on yet is checked; the first publication: both
  const [toSite, setToSite] = useState(true);
  const [toTg, setToTg] = useState(true);
  const files = p.media || [];
  const [order, setOrder] = useState<number[]>(() => files.map((m) => m.id));
  const [off, setOff] = useState<Set<number>>(() => new Set());
  const byId = new Map(files.map((m) => [m.id, m]));
  const all = [...order.filter((id) => byId.has(id)), ...files.filter((m) => !order.includes(m.id)).map((m) => m.id)];
  const ids = [...all.filter((id) => byId.get(id)!.kind !== 'video'), ...all.filter((id) => byId.get(id)!.kind === 'video')];  // the grid's order
  const isVid = (id: number) => byId.get(id)!.kind === 'video';
  const picked = albumOf(ids.filter((id) => !off.has(id)), isVid);
  const shown = new Set(ids.filter((id) => !picked.includes(id)));  // unchecked in the grid: switched off or past the 9th photo
  const few = fewPhotos(files);
  const text = draft.data?.text ?? '';  // Stanislav 11-23: no free text — the post is built from the object's fields only
  const tags = [...new Set(text.match(/#[\p{L}\p{N}_]+/gu) || [])];
  const blocked = !!p.exclusive_conflict?.length;
  const publish = useMutation({
    mutationFn: () => api.properties.publish(p.id, { media_ids: picked, channel: 'tg', tg: toTg, site: toSite }),
    onMutate: () => { lastStep.current = {}; setFailed(null); },
    onSuccess: (r) => { set(r); toast(toTg ? t('Опубликовано: агенту ушла ссылка на пост') : t('Опубликовано на сайте')); },
    onError: (e) => { setFailed(t('Не получилось: {step} — {error}', { step: stepText(lastStep.current), error: errText(e) })); toast(errText(e), true); },
  });
  // Stanislav 11-16: the operator sees what the publish is doing now (a 4K video takes a while), polled every second
  const lastStep = useRef<PublishProgress>({});
  const [failed, setFailed] = useState<string | null>(null);
  const prog = useQuery({
    queryKey: ['publish-progress', p.id], queryFn: () => api.properties.publishProgress(p.id),
    enabled: publish.isPending, refetchInterval: 1000, gcTime: 0,
  });
  if (publish.isPending && prog.data?.step) lastStep.current = prog.data;
  const toggle = (id: number) => setOff((o) => {
    const n = new Set(o);
    if (picked.includes(id)) { n.add(id); return n; }
    n.delete(id);
    if (!albumOf(ids.filter((x) => !n.has(x)), isVid).includes(id)) { toast(t('В пост Telegram уходят {n} фото и видео: снимите галочку с другого фото', { n: ALBUM_PHOTOS }), true); return o; }
    return n;
  });
  const shots: Shot[] = ids.map((id) => { const m = byId.get(id)!; return { id, kind: m.kind, room: m.room, src: m.url || m.path }; });
  if (draft.isLoading) return <Loading />;
  if (draft.error) return <ErrorBox error={draft.error} />;
  return (
    <div className="col">
      <Field label={t('Текст поста')} hint={t('Собирается из параметров объекта: поменять — «Править» в «Параметрах», пост перевыложится сам.')}>
        <div className="outcome-card" style={{ whiteSpace: 'pre-wrap', fontSize: 13, lineHeight: 1.45, maxHeight: 360, overflow: 'auto' }}>{text}</div>
      </Field>
      <div className="row row-between row-wrap gap-sm">
        <div className="row row-wrap gap-sm">
          {tags.map((t) => <Badge key={t} tone="grey">{t}</Badge>)}
        </div>
        <span className={picked.length && text.length > CAPTION_MAX ? '' : 'faint'} style={{ fontSize: 12, fontWeight: 700, color: picked.length && text.length > CAPTION_MAX ? 'var(--red)' : undefined }}>
          {text.length}{picked.length ? ` / ${CAPTION_MAX}` : ''}
        </span>
      </div>
      {picked.length > 0 && text.length > CAPTION_MAX && <div className="alert alert-warn">{t('Подпись к альбому длиннее {n} символов: Telegram обрежет конец.', { n: CAPTION_MAX })}</div>}
      <div>
        <div className="row row-between"><span className="label">{t('Фото и видео в посте')}</span><span className="muted num" style={{ fontWeight: 700 }}>{picked.length}/{albumOf(ids, isVid).length}</span></div>
        <div className="muted" style={{ fontSize: 12, margin: '4px 0 8px' }}>{t('Перетащите, чтобы поменять порядок (на телефоне — долгое нажатие); галочка — файл в посте; нажмите, чтобы открыть. Первый файл — обложка, на фото ставится водяной знак.')}</div>
        {!ids.length ? <div className="muted" style={{ fontSize: 13 }}>{t('Файлов нет: уйдёт текстовый пост')}</div>
          : <MediaGrid items={shots} grouped off={shown} onToggle={(id) => toggle(Number(id))} onOrder={(next) => setOrder(next.map(Number))} />}
      </div>
      {few && <div className="alert alert-warn">{few}</div>}
      {blocked && <div className="alert alert-warn">{t('Эксклюзив ещё висит на других сайтах: публикация закрыта до «Проверено: снято».')}</div>}
      <PublishTargets site={toSite} tg={toTg} onSite={setToSite} onTg={setToTg} />
      <Button variant="ok" size="lg" disabled={blocked || !!few || (!toSite && !toTg) || (toTg && !text.trim())} loading={publish.isPending} onClick={() => publish.mutate()}>
        <Send size={18} />{t('Опубликовать')}
      </Button>
      {publish.isPending && <div className="alert alert-info" role="status">{stepText(prog.data?.step ? prog.data : lastStep.current)}</div>}
      {failed && !publish.isPending && <div className="alert alert-warn" role="alert">{failed}</div>}
    </div>
  );
}

/** One step of the running publish (backend notify.publish_post / properties.publish → app/progress.py). */
function stepText(s: PublishProgress): string {
  switch (s.step) {
    case 'files': return t('Скачиваем фото и видео из хранилища…');
    case 'originals': return t('Отправляем оригиналы в Telegram ({chat})…', { chat: s.chat || '' });
    case 'video': return t('Готовим видео {n} из {of}…', { n: s.n || 1, of: s.of || 1 });
    case 'photos': return t('Ставим водяной знак на фото…');
    case 'post': return t('Отправляем пост в Telegram ({chat})…', { chat: s.chat || '' });
    case 'site': return t('Публикуем на сайт…');
    default: return t('Начинаем публикацию…');
  }
}

/** «На сайт» / «В Telegram» over the «Опубликовать» button (rent, sale and cars alike). */
export function PublishTargets({ site, tg, onSite, onTg, tgLocked }: { site: boolean; tg: boolean; onSite: (v: boolean) => void; onTg: (v: boolean) => void; tgLocked?: string }) {
  return (
    <div className="col gap-sm">
      <span className="label">{t('Куда публикуем')}</span>
      <div className="row row-wrap gap">
        <Checkbox checked={site} onChange={onSite}><Globe size={16} />{t('На сайт hge.ge')}</Checkbox>
        <Checkbox checked={tg && !tgLocked} disabled={!!tgLocked} onChange={onTg} title={tgLocked}><Send size={16} />{t('В Telegram')}</Checkbox>
      </div>
      {tgLocked && <span className="faint" style={{ fontSize: 12 }}>{tgLocked}</span>}
      {!site && !tg && <span className="faint" style={{ fontSize: 12 }}>{t('Отметьте хотя бы одно')}</span>}
    </div>
  );
}

/** Exclusive object: the owner signs the exclusivity confirmation by the link (/sign/<token>, pages/ExclusiveSign) and the
 * PDF lands here by itself; or the agent uploads one signed elsewhere. Without it «Отправить на модерацию» is blocked
 * (backend submit_for_review). The PDF is not a post file. */
function ExclusiveContract({ p, canEdit, nested }: { p: Property; canEdit: boolean; nested?: boolean }) {
  const { toast, set } = useProp(p.id);
  const ref = useRef<HTMLInputElement>(null);
  const c = p.exclusive_contract;
  const waived = !c && p.no_esign?.status === 'approved';
  const link = p.exclusive_sign_token ? `${window.location.origin}/sign/${p.exclusive_sign_token}` : '';
  const up = useMutation({ mutationFn: (file: File) => api.properties.uploadContract(p.id, file), onSuccess: (r) => { set(r); toast(t('Договор загружен')); }, onError: (e) => toast(errText(e), true) });
  const copy = () => (navigator.clipboard ? navigator.clipboard.writeText(link || '') : Promise.reject()).then(() => toast(t('Ссылка скопирована')), () => toast(t('Не удалось скопировать'), true));
  const share = `https://t.me/share/url?url=${encodeURIComponent(link || '')}&text=${encodeURIComponent('გთხოვთ, დაადასტუროთ ექსკლუზიური თანამშრომლობა Rent in Tbilisi-სთან და მოაწეროთ ხელი ბმულზე.\nПодтвердите, пожалуйста, эксклюзивное сотрудничество с Rent in Tbilisi и подпишите по ссылке.')}`;
  return (
    <div className={nested ? 'col gap-sm' : 'gen-block'}>
      <span className={nested ? 'label' : 'gen-title'}>{t('Эксклюзивный договор')}</span>
      {c ? <div className="alert alert-ok">{t('Договор подписан {at}', { at: fmtDateTime(c.created_at) })} · <a href={mediaUrl(c.url)} target="_blank" rel="noreferrer"><FileText size={14} /> {t('скачать PDF')}</a></div>
        : waived ? null : <div className="alert alert-warn">{t('Нет договора: без подписанного PDF объект нельзя отправить оператору')}</div>}
      {!c && <NoEsign p={p} canEdit={canEdit} />}
      {canEdit && !waived && <>
        {!link && !c && !p.is_exclusive && <div className="muted" style={{ fontSize: 12 }}>{t('Сохраняем — ссылка для собственника появится через секунду…')}</div>}
        {link && !c && <>
          <div className="muted" style={{ fontSize: 12 }}>{t('Отправьте собственнику ссылку: он заполнит данные и распишется, PDF появится здесь сам. Обновите карточку после подписи.')}</div>
          <div className="row row-wrap gap-sm">
            <Button onClick={copy}><Copy size={16} />{t('Скопировать ссылку')}</Button>
            <a className="btn" href={share} target="_blank" rel="noreferrer"><Send size={16} />{t('Отправить собственнику')}</a>
          </div>
        </>}
        <input ref={ref} type="file" accept="application/pdf" hidden onChange={(e) => { const fl = e.target.files?.[0]; if (fl) up.mutate(fl); e.target.value = ''; }} />
        <Button block loading={up.isPending} onClick={() => ref.current?.click()}><Upload size={16} />{c ? t('Заменить PDF договора') : t('Или загрузить подписанный PDF')}</Button>
      </>}
      <OwnerDoc p={p} canEdit={canEdit} kind="registry_extract" label={t('Выписка из Публичного реестра')} accept="application/pdf,.pdf" />
      <OwnerDoc p={p} canEdit={canEdit} kind="owner_id_doc" label={t('Паспорт / ID собственника')} accept="image/jpeg,image/png,image/heic,image/heif,.heic,.heif,application/pdf,.pdf" />
    </div>
  );
}

const NO_ESIGN_PRESETS = ['собственник за границей', 'не пользуется телефоном', 'подписал на бумаге'];

/** «Без электронной подписи» (Irakli 11-18): the owner can't sign by the link → the agent asks the operator, with a
 * reason and optionally a scan / photo of a paper contract. Approved = the contract step is done (backend
 * properties.contract_done); rejected = the step is back with the operator's comment. The operator decides in
 * «Запросы» or on this card (RequestRow); his own request goes to another operator. */
function NoEsign({ p, canEdit }: { p: Property; canEdit: boolean }) {
  const { toast, set } = useProp(p.id);
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const w = p.no_esign;
  const send = useMutation({
    mutationFn: () => api.properties.noEsign(p.id, reason.trim(), file),
    onSuccess: (r) => { set(r); setOpen(false); setReason(''); setFile(null); toast(t('Запрос ушёл оператору')); },
    onError: (e) => toast(errText(e), true),
  });
  const who = (x?: { name: string | null } | null) => x?.name || '—';
  const scan = w?.scan_url ? <> · <a href={mediaUrl(w.scan_url)} target="_blank" rel="noreferrer"><FileText size={14} /> {t('скан договора')}</a></> : null;
  return (
    <div className="col gap-sm">
      {w?.status === 'approved' && <div className="alert alert-ok"><span><CheckCircle2 size={14} /> {w.decided_by ? t('Без электронной подписи · подтвердил оператор {who} {at}', { who: who(w.decided_by), at: fmtDateTime(w.decided_at) })
        : t('Без электронной подписи · подтверждено системой {at}: дублей нет', { at: fmtDateTime(w.decided_at) })}<br />
        <span className="muted">{t('Причина:')} {w.reason} · {t('запросил {who}', { who: who(w.requested_by) })}</span>{scan}</span></div>}
      {w?.status === 'pending' && <div className="alert alert-info"><span><Clock size={14} /> {t('Без электронной подписи: ждёт решения оператора')}<br />
        <span className="muted">{t('Причина:')} {w.reason} · {who(w.requested_by)} · {fmtDateTime(w.created_at)}</span>{scan}</span></div>}
      {w?.status === 'pending' && !w.dups && <div className="muted" style={{ fontSize: 12 }}>{t('Ищем дубли: если их нет, эксклюзив подтвердится сам')}</div>}
      {w?.status !== 'rejected' && w?.dups?.state !== 'clear' && <DupsGate d={w?.dups} />}
      {w?.status === 'rejected' && <div className="alert alert-warn"><span>{t('Оператор отклонил «без электронной подписи»')}{w.decided_by ? ` (${who(w.decided_by)})` : ''}: <b>{w.comment || '—'}</b></span></div>}
      {canEdit && w?.status !== 'approved' && w?.status !== 'pending' && (open ? (
        <div className="col gap-sm outcome-card">
          <span className="label">{t('Без электронной подписи')}</span>
          <div className="muted" style={{ fontSize: 12 }}>{t('Если собственник не может подписать по ссылке — объясните оператору почему. После его подтверждения шаг договора будет пройден.')}</div>
          <div className="row row-wrap gap-sm">
            {NO_ESIGN_PRESETS.map((x) => <Button key={x} size="sm" variant={reason === x ? 'primary' : 'secondary'} onClick={() => setReason(x)}>{t(x)}</Button>)}
          </div>
          <Textarea rows={2} value={reason} placeholder={t('Причина для оператора')} onChange={(e) => setReason(e.target.value)} />
          <div className="row row-wrap gap-sm" style={{ fontSize: 13 }}>
            <input ref={fileRef} type="file" hidden accept="image/jpeg,image/png,image/heic,image/heif,.heic,.heif,application/pdf,.pdf" onChange={(e) => { setFile(e.target.files?.[0] || null); e.target.value = ''; }} />
            <Button size="sm" variant="secondary" onClick={() => fileRef.current?.click()}><Upload size={14} />{file ? t('Заменить') : t('Скан договора')}</Button>
            <span className="muted" style={{ fontSize: 12, minWidth: 0, overflowWrap: 'anywhere' }}>{file ? file.name : t('бумажный договор, фото или PDF — необязательно')}</span>
          </div>
          <div className="row row-wrap gap-sm">
            <Button loading={send.isPending} disabled={!reason.trim()} onClick={() => send.mutate()}><Send size={16} />{t('Отправить оператору')}</Button>
            <Button variant="ghost" onClick={() => setOpen(false)}>{t('Отмена')}</Button>
          </div>
        </div>
      ) : <Button variant="secondary" block onClick={() => setOpen(true)}><FileText size={16} />{w?.status === 'rejected' ? t('Запросить снова: без электронной подписи') : t('Без электронной подписи')}</Button>)}
    </div>
  );
}

/** Irakli 11-18, David 11-23 (any live object): what the duplicate search found (backend similar.scan_exclusive): the count, and the
 * strong ones (a shared photo, the owner's phone, the cadastral code); the full list is «Похожие объекты» below. */
function DupScan({ p }: { p: Property }) {
  const s = p.duplicate_scan;
  const qc = useQueryClient();
  useEffect(() => { if (s?.at) qc.invalidateQueries({ queryKey: ['similar', `p:${p.id}`] }); }, [s?.at, p.id, qc]);  // a new search → the list below again
  if (!s) return null;
  if (s.n === undefined) return <div className="alert alert-info"><Clock size={14} /> {t('Ищем дубли и копии везде: в базе, на сайтах и у конкурентов…')}</div>;
  if (!s.n) return <div className="alert alert-ok"><CheckCircle2 size={14} /> {t('Поиск дублей: ничего не найдено')}{s.at ? ` · ${fmtDateTime(s.at)}` : ''}</div>;
  return (
    <div className={`alert ${s.strong?.length ? 'alert-warn' : 'alert-info'}`}>
      <span>
        <b>{t('Найдены возможные дубли: {n}', { n: s.n })}</b>{s.at ? <span className="muted"> · {fmtDateTime(s.at)}</span> : null}
        {!!s.strong?.length && <>
          <br />{p.is_exclusive ? t('Сильные совпадения — агент и оператор получили уведомление:') : t('Сильные совпадения:')}
          {s.strong.slice(0, 5).map((x) => <div key={x.key} style={{ fontSize: 13 }}>• {x.where}{x.property_id ? ` #${x.property_id}` : ''}{x.address ? ` · ${x.address}` : ''}: {x.reasons.join(', ')}</div>)}
        </>}
      </span>
    </div>
  );
}

/** The owner's private document (the sign page uploads both; the agent can for a paper contract). Only staff and the
 * object's agent get owner_docs (and the file itself) from the backend; for others it's just «нет». */
function OwnerDoc({ p, canEdit, kind, label, accept }: { p: Property; canEdit: boolean; kind: 'owner_id_doc' | 'registry_extract'; label: string; accept: string }) {
  const { toast, set } = useProp(p.id);
  const ref = useRef<HTMLInputElement>(null);
  const d = p.owner_docs?.[kind];
  const up = useMutation({ mutationFn: (file: File) => api.properties.uploadOwnerDoc(p.id, kind, file), onSuccess: (r) => { set(r); toast(t('Загружено')); }, onError: (e) => toast(errText(e), true) });
  return (
    <div className="row row-wrap gap-sm">
      {d ? <span><CheckCircle2 size={14} color="var(--ok, #1a7f4b)" /> {label} · <a href={mediaUrl(d.url)} target="_blank" rel="noreferrer"><FileText size={14} /> {t('открыть')}</a></span>
        : <span className="muted">{label}: {t('нет')}</span>}
      {kind === 'registry_extract' && p.registry_app && <a className="faint" style={{ fontSize: 12 }} href={p.registry_app.url} target="_blank" rel="noreferrer">{t('заявка NAPR №{n}', { n: p.registry_app.reg_number })}{p.registry_app.date ? ` ${t('от {d}', { d: new Date(p.registry_app.date).toLocaleDateString(LOCALE) })}` : ''}</a>}
      {canEdit && <>
        <input ref={ref} type="file" accept={accept} hidden onChange={(e) => { const fl = e.target.files?.[0]; if (fl) up.mutate(fl); e.target.value = ''; }} />
        <Button size="sm" variant="secondary" loading={up.isPending} onClick={() => ref.current?.click()}><Upload size={14} />{d ? t('Заменить') : t('Загрузить')}</Button>
      </>}
    </div>
  );
}

function ReviewStep({ p }: { p: Property }) {
  const { toast, set } = useProp(p.id);
  const [comment, setComment] = useState('');
  const missing = roomsOf(p).slice(0, 6).filter((r) => !(p.checklist || {})[r] && !(p.media || []).some((m) => m.room === r));
  const back = useMutation({
    mutationFn: () => api.properties.review(p.id, false, comment),
    onSuccess: (r) => { set(r); toast(t('Возвращено агенту')); },
    onError: (e) => toast(errText(e), true),
  });
  return (
    <div className="col">
      {p.is_exclusive && <ExclusiveContract p={p} canEdit={false} />}
      <span className="label">{t('Автопроверки')}</span>
      <div className="col gap-sm">
        <div className="row gap-sm"><AiFlagBadges flags={p.ai_flags} />{!p.ai_flags?.owner && !p.ai_flags?.agent_suspect && !p.ai_flags?.duplicate && <span className="muted">{t('Меток ИИ нет')}</span>}</div>
        <DupCheck value={p.dup_check} />
        {p.room_picker !== false && (missing.length ? <div className="alert alert-info">{t('Комнаты не отмечены (необязательно):')} {missing.map(roomT).join(', ')}</div> : <div className="alert alert-ok">{t('Все основные комнаты отмечены')}</div>)}
        {p.owner?.is_agent && <div className="alert alert-warn">{t('Телефон собственника отмечен как агентский')}</div>}
      </div>
      <div className="divider" />
      <PostComposer p={p} />
      <div className="divider" />
      <Field label={t('Вернуть агенту')} hint={t('Агент получит комментарий в Telegram и снова сможет загрузить фото')}><Textarea value={comment} onChange={(e) => setComment(e.target.value)} placeholder={t('Что переснять или поправить')} /></Field>
      <div className="row"><Button variant="danger" disabled={!comment.trim()} loading={back.isPending} onClick={() => back.mutate()}>{t('Вернуть агенту')}</Button></div>
    </div>
  );
}

/** A published object with a post (Stanislav 11-24): one «Редактировать пост» — the object's form, its «Обновить пост»
 * saves the changes and reposts (nothing changed: just reposts, «поднять пост»). No post: «Вернуть публикацию». */
function Republish({ p, onEdit }: { p: Property; onEdit: () => void }) {
  const { toast, set } = useProp(p.id);
  const left = p.reposts_left;
  const again = useMutation({
    mutationFn: () => api.properties.publish(p.id, { channel: 'tg', tg: true, site: !p.site_hidden }),
    onSuccess: (r) => { set(r); toast(t('Пост выложен заново наверх канала, старый удалён')); },
    onError: (e) => toast(errText(e), true),
  });
  if (!p.post_url) return null;
  return (
    <div className="row row-wrap gap-sm">
      <Button variant="secondary" onClick={onEdit}><Pencil size={16} />{t('Редактировать пост')}</Button>
      {/* David 11-24: «апдейт» — the same post up the channel; agents 3 a day (backend REPOST_LIMIT) */}
      <Button variant="secondary" loading={again.isPending} disabled={left === 0} onClick={() => again.mutate()}
        title={t('Пост выйдет заново наверху канала, старый удалится')}>
        <RotateCcw size={16} />{t('Поднять пост наверх')}{left != null ? ` · ${t('осталось {n} из {m} сегодня', { n: left, m: p.reposts_limit ?? 1 })}` : ''}
      </Button>
    </div>
  );
}

/** «Скидка»: the new price and ❗️#Discount in the post; the post is reposted by itself (backend apply_update → tg_repost). */
function DiscountModal({ p, open, onClose }: { p: Property; open: boolean; onClose: () => void }) {
  const { toast, set } = useProp(p.id);
  const [price, setPrice] = useState('');
  const sale = p.post_fields?.deal === 'Sale';
  const currentPrice = (sale && p.post_fields?.sale_price) || p.price;
  const m = useMutation({
    mutationFn: () => {
      const nextPrice = Number(price);
      const post_fields = { ...p.post_fields, discount: true, ...(sale ? { sale_price: nextPrice } : {}) };
      return api.properties.update(p.id, { price: nextPrice, post_fields });
    },
    onSuccess: (r) => { set(r); toast(r.change_request ? t('Новая цена ушла оператору на проверку') : r.post_url ? t('Цена снижена · пост с #Discount перевыкладывается') : t('Цена снижена · в посте будет #Discount')); onClose(); },
    onError: (e) => toast(errText(e), true),
  });
  const ok = Number(price) > 0 && Number(price) < Number(currentPrice || Infinity);
  return (
    <Modal open={open} onClose={onClose} title={t('Скидка')}>
      <div className="col gap-lg">
        <Field label={t('Новая цена, $')} hint={t('Сейчас {price}. В посте появится ❗️#Discount', { price: money(currentPrice, p.currency || 'USD') })}><Input inputMode="numeric" value={price} onChange={(e) => setPrice(e.target.value.replace(/\D/g, ''))} /></Field>
        {(p.locked_fields || []).length > 0 && <div className="alert alert-info" style={{ fontSize: 13 }}>{t('Объект проверен оператором: новая цена уйдёт оператору, после подтверждения пост перевыложится сам.')}</div>}
        <Button block disabled={!ok} loading={m.isPending} onClick={() => m.mutate()}>{t('Снизить цену')}</Button>
      </div>
    </Modal>
  );
}

/** A published object's actions: discount, pause, the deal, rented by the owner. Pause and the owner's are the archive with a reason. */
function PublishedActions({ p, onArchive }: { p: Property; onArchive: (reason: string) => void }) {
  const [discount, setDiscount] = useState(false);
  return (
    <>
      <div className="row row-wrap gap-sm">
        <Button variant="secondary" size="sm" onClick={() => setDiscount(true)}>{t('% Скидка')}</Button>
        <Button variant="secondary" size="sm" onClick={() => onArchive('paused')}><Clock size={14} />{t('Приостановить')}</Button>
        {/* Stanislav 11-16: instead of «Сдали мы» — the deal form with this object; its confirm turns the object «Сдано» */}
        <Link className="btn btn-secondary btn-sm" to={`/deals/new?property_id=${p.id}`}><CheckCircle2 size={14} />{t('Оформить сделку')}</Link>
        <Button variant="secondary" size="sm" onClick={() => onArchive('rented_by_owner')}><Home size={14} />{t('Собственник сдал')}</Button>
      </div>
      <DiscountModal p={p} open={discount} onClose={() => setDiscount(false)} />
    </>
  );
}

/** «Снять с публикации» / «Вернуть публикацию» (Stanislav 11-24): a modal with a checkbox per place — the site hge.ge
 * and the Telegram channel. Off from every place it is in = «Снят с публикации» (archive, backend unpublish); from one
 * = only there (site / unpost), the status stays. Back: the places it is missing from (site on / publish tg). */
function SiteButton({ p }: { p: Property }) {
  const [mode, setMode] = useState<'off' | 'on' | null>(null);
  const [only, setOnly] = useState<'site' | 'tg' | null>(null);
  const onSite = !p.site_hidden, onTg = !!p.post_url;
  return <>
    {onSite && <a className="btn btn-secondary" href={`https://hge.ge/listing/o${p.id}`} target="_blank" rel="noreferrer"><Globe size={16} />{t('На сайте')}</a>}
    {(onSite || onTg) && <UnpublishMenu onSite={onSite} onTg={onTg} onPick={(k) => { setOnly(k); setMode('off'); }} />}
    {(!onSite || !onTg) && <Button variant="ok" onClick={() => { setOnly(null); setMode('on'); }}><RotateCcw size={16} />{t('Вернуть публикацию')}</Button>}
    {mode && <PlacesModal p={p} off={mode === 'off'} only={only} onClose={() => setMode(null)} />}
  </>;
}

/** «Снять с публикации ▾» (Stanislav 11-24: «боюсь нажимать»): a menu that says where from; each item asks to confirm. */
function UnpublishMenu({ onSite, onTg, onPick }: { onSite: boolean; onTg: boolean; onPick: (k: 'site' | 'tg' | null) => void }) {
  const ref = useRef<HTMLDetailsElement>(null);
  const pick = (k: 'site' | 'tg' | null) => { if (ref.current) ref.current.open = false; onPick(k); };
  const item = (k: 'site' | 'tg' | null, head: string, sub: string) => (
    <button type="button" className="btn btn-ghost" style={{ justifyContent: 'flex-start', textAlign: 'left', height: 'auto', padding: '8px 12px' }} onClick={() => pick(k)}>
      <span className="col"><b>{head}</b><span className="muted" style={{ fontSize: 12, fontWeight: 400 }}>{sub}</span></span>
    </button>
  );
  return (
    <details ref={ref} style={{ position: 'relative' }}>
      <summary className="btn btn-ghost" style={{ listStyle: 'none' }}>{t('Снять с публикации')}<ChevronDown size={16} /></summary>
      <div className="card col" style={{ position: 'absolute', zIndex: 20, top: '100%', left: 0, minWidth: 300, padding: 6, marginTop: 4, boxShadow: '0 8px 24px rgba(0,0,0,.12)' }}>
        {onSite && onTg && item('site', t('Только с сайта hge.ge'), t('Пост в канале и статус останутся'))}
        {onSite && onTg && item('tg', t('Только из Telegram-канала'), t('Сайт и статус останутся'))}
        {item(null, onSite && onTg ? t('Отовсюду (снять объект)') : onSite ? t('С сайта (снять объект)') : t('Из канала (снять объект)'), t('В архив «Снят с публикации», можно вернуть'))}
      </div>
    </details>
  );
}

function PlacesModal({ p, off, only, onClose }: { p: Property; off: boolean; only?: 'site' | 'tg' | null; onClose: () => void }) {
  const { toast, set } = useProp(p.id);
  const has = { site: !p.site_hidden, tg: !!p.post_url };
  const places = (['site', 'tg'] as const).filter((k) => has[k] === off);
  const [pick, setPick] = useState<Record<string, boolean>>(only ? { site: only === 'site', tg: only === 'tg' } : { site: true, tg: true });
  const chosen = places.filter((k) => pick[k]);
  const m = useMutation({
    mutationFn: async () => {
      if (off) {
        if (chosen.length === places.length) return api.properties.unpublish(p.id);
        return chosen[0] === 'site' ? api.properties.site(p.id, false) : api.properties.unpost(p.id);
      }
      if (chosen.includes('tg')) return api.properties.publish(p.id, { channel: 'tg', tg: true, site: has.site || chosen.includes('site') });
      return api.properties.site(p.id, true);
    },
    onSuccess: (r) => {
      set(r); onClose();
      toast(off ? (chosen.length === places.length ? t('Снято с публикации · вернуть — «Опубликовать снова» в архиве')
        : chosen[0] === 'site' ? t('Снято с сайта · канал и статус без изменений') : t('Пост удалён из канала · сайт и статус без изменений'))
        : t('Публикация возвращена'));
    },
    onError: (e) => toast(errText(e), true),
  });
  const label = { site: t('Сайт hge.ge'), tg: t('Telegram-канал') };
  return (
    <Modal open onClose={onClose} title={off ? t('Снять с публикации') : t('Вернуть публикацию')}>
      <div className="col gap-lg">
        <span className="muted">{off ? t('Откуда снять объект:') : t('Куда вернуть объект:')}</span>
        <div className="col gap-sm">{places.map((k) => <Checkbox key={k} checked={pick[k]} onChange={(x) => setPick({ ...pick, [k]: x })}>{label[k]}</Checkbox>)}</div>
        {off && chosen.length > 0 && <div className="alert alert-info" style={{ fontSize: 13 }}>{chosen.length === places.length
          ? t('Объект уйдёт отовсюду и из бота, в архив «Снят с публикации». Вернуть — «Опубликовать снова».')
          : chosen[0] === 'site' ? t('Объект пропадёт с сайта, пост в канале и статус останутся.') : t('Пост удалится из канала, на сайте и в статусе всё останется.')}</div>}
        {!off && chosen.includes('tg') && <div className="alert alert-info" style={{ fontSize: 13 }}>{t('В канал выйдет новый пост из параметров объекта.')}</div>}
        <div className="modal-actions"><Button block disabled={!chosen.length} loading={m.isPending} onClick={() => m.mutate()}>{t('Подтвердить')}</Button><Button block variant="ghost" onClick={onClose}>{t('Отмена')}</Button></div>
      </div>
    </Modal>
  );
}

/** «Снять с паузы» / «Опубликовать снова» (unpublished): published again with a new post; «Снова в работу» for
 * «Собственник сдал» (back to «Взят», call the owner); republish: a rented object published again right away (staff). */
export function Resume({ p, republish }: { p: Property; republish?: boolean }) {
  const { toast, set } = useProp(p.id);
  const work = !republish && !['paused', 'unpublished'].includes(p.archive_reason || '');
  const m = useMutation({ mutationFn: () => api.properties.resume(p.id, republish), onSuccess: (r) => { set(r); toast(work ? t('Объект снова в работе') : t('Объект снова опубликован')); }, onError: (e) => toast(errText(e), true) });
  const label = republish || p.archive_reason === 'unpublished' ? t('Опубликовать снова') : p.archive_reason === 'paused' ? t('Снять с паузы')
    : p.status === 'rented' ? t('Вернуть в работу') : t('Снова в работу');
  return <Button size="sm" loading={m.isPending} onClick={() => m.mutate()}><RotateCcw size={14} />{label}</Button>;
}


/** «Сменить агента собственника…» (staff): the owner and his live objects move to another agent, with a history event. */
function OwnerTransfer({ p, request }: { p: Property; request?: boolean }) {
  const { toast, qc } = useProp(p.id);
  const [open, setOpen] = useState(false);
  const [to, setTo] = useState('');
  const [reason, setReason] = useState('');
  const agents = useQuery({ queryKey: ['users', 'field'], queryFn: () => api.users.list({ role: FIELD_FILTER, active: true }), enabled: open });
  const m = useMutation({
    mutationFn: async () => { await (request ? api.properties.transferRequest(p.id, Number(to), reason) : api.owners.transfer(p.owner!.id, Number(to), reason)); },
    onSuccess: () => { ['property', 'property-history', 'property-post'].forEach((k) => qc.invalidateQueries({ queryKey: [k] })); toast(request ? REQUEST_SENT : t('Собственник передан агенту')); setOpen(false); },
    onError: (e) => toast(errText(e), true),
  });
  const options = itemsOf(agents.data).filter((u) => u.id !== p.owner?.agent?.id).map((u) => ({ value: u.id, label: agentLabel(u) }));
  return (
    <>
      <Button variant="secondary" size="sm" className="mt" onClick={() => setOpen(true)}><ArrowRightLeft size={14} />{request ? t('Запрос: сменить агента…') : t('Сменить агента собственника…')}</Button>
      <Modal open={open} onClose={() => setOpen(false)} title={t('Сменить агента собственника')}>
        <div className="col gap-lg">
          <div className="muted" style={{ fontSize: 13 }}>{t('Сейчас: {agent}. Вместе с собственником перейдут его объекты в работе у прежнего агента. Передача запишется в историю.', { agent: agentLabel(p.owner?.agent) })}</div>
          {request && <div className="alert alert-info" style={{ fontSize: 13 }}>{t('Передачу проверит оператор: запрос уйдёт в «Запросы».')}</div>}
          <Field label={t('Новый агент')}><Select value={to} placeholder="—" onChange={(e) => setTo(e.target.value)} options={options} /></Field>
          <Field label={t('Причина')}><Input value={reason} placeholder={t('Например: агент ушёл из агентства')} onChange={(e) => setReason(e.target.value)} /></Field>
          <Button block disabled={!to} loading={m.isPending} onClick={() => m.mutate()}>{request ? t('Отправить запрос') : t('Передать')}</Button>
        </div>
      </Modal>
    </>
  );
}

// the old generator's tag groups (backend services/properties.py POST_ONE / POST_MANY), stored without '#'
const opts = (o: Record<string, string>) => Object.entries(o).map(([value, label]) => ({ value, label }));
export const metroLabel = (tag?: string | null) => (tag ? POST_ONE.metro[1][tag] || tag : '');
export const POST_ONE: Record<string, [string, Record<string, string>]> = {
  prop_type: [t('Тип'), { Apartment: t('Квартира'), House: t('Дом'), Commercial: t('Коммерция'), Duplex: t('Дуплекс'), Townhouse: t('Таунхаус'), Land: t('Земля'), Retail: t('Ритейл'), Business: t('Бизнес'), Cafe: t('Кафе'), Restaurant: t('Ресторан'), BeautySalon: t('Салон красоты'), Warehouses: t('Склад'), Manufacture: t('Производство') }],
  metro: [t('Метро'), { LibertySquare: t('Площадь Свободы'), Rustaveli: t('Руставели'), Marjanishvili: t('Марджанишвили'), StationSquare: t('Вокзальная площадь'), Tsereteli: t('Церетели'), Gotsiridze: t('Гоциридзе'), Nadzaladevi: t('Надзаладеви'), Didube: t('Дидубе'), Grmagele: t('Грмагеле'), Guramishvili: t('Гурамишвили'), Sarajishvili: t('Сараджишвили'), AhmeteliTheatre: t('Театр Ахметели'), STUniversity: t('Госуниверситет'), VazhaPshavela: t('Важа-Пшавела'), Delisi: t('Делиси'), TCUniversity: t('Техуниверситет'), MCUniversity: t('Медуниверситет'), AvlabariMetro: t('Авлабари'), IsaniMetro: t('Исани'), '300Aragveli': t('300 Арагвели'), SamgoriMetro: t('Самгори'), Varketili: t('Варкетили') }],
  building: [t('Дом'), { NewBuilding: t('Новостройка'), OldBuilding: t('Старый фонд'), HistoricalBuilding: t('Исторический'), Reconstruction: t('Реконструкция') }],
  design: [t('Дизайн'), { New: t('Новый'), White: t('Белый'), Grey: t('Серый'), Yellow: t('Жёлтый'), Mixed: t('Смешанный'), Old: t('Старый'), Retro: t('Ретро'), UnderRepair: t('В ремонте') }],
  condition: [t('Состояние (продажа)'), { BlackFrame: t('Чёрный каркас'), WhiteFrame: t('Белый каркас'), GreenFrame: t('Зелёный каркас'), FullyRenovated: t('С ремонтом') }],
  heating: [t('Отопление'), { CentralHeating: t('Центральное'), GasHeating: t('Газовое'), ElectricalHeating: t('Электрическое'), ACHeating: t('Кондиционер'), Fireplace: t('Камин') }],
  bathrooms: [t('Санузлов'), { '1Bathroom': '1', '2Bathroom': '2', '3Bathroom': '3', '4Bathroom': '4' }],
  pets: [t('Животные'), { Allowed: t('Можно'), NotAllowed: t('Нельзя'), ByAgreement: t('По договорённости') }],
};
export const POST_MANY: Record<string, [string, Record<string, string>]> = {
  amenities: [t('Удобства'), { Balcony: t('Балкон'), Terrace: t('Терраса'), Courtyard: t('Двор'), WiFi: 'Wi-Fi', TV: t('ТВ'), SmartTV: 'Smart TV', Bath: t('Ванна'), Shower: t('Душ'), Dishwasher: t('Посудомойка'), Stove: t('Плита'), Microwave: t('Микроволновка'), WashingMachine: t('Стиральная машина'), VacuumCleaner: t('Пылесос'), Fireplace: t('Камин'), Elevator: t('Лифт'), Oven: t('Духовка'), ParkingPlace: t('Парковка'), Conditioner: t('Кондиционер') }],
  periods: [t('Сроки аренды'), { '1Day': t('1 день'), '1Month': t('1 мес'), '2Month': t('2 мес'), '3Month': t('3 мес'), '4Month': t('4 мес'), '5Month': t('5 мес'), '6Month': t('6 мес'), '9Month': t('9 мес'), '12Month': t('12 мес') }],
};
const RENT_ONLY = ['pets', 'periods', 'tenants', 'deposit'];

/** «Параметры» rows for the filled post fields. */
function postFieldRows(f?: PostFields | null): [string, React.ReactNode][] {
  if (!f) return [];
  const rows: [string, React.ReactNode][] = [];
  if (f.deal) rows.push([t('Сделка'), f.deal === 'Sale' ? t('продажа') : t('аренда')]);
  for (const [k, [label, o]] of Object.entries(POST_ONE)) { const x = (f as Record<string, string>)[k]; if (x) rows.push([label, o[x] || x]); }
  for (const [k, [label, o]] of Object.entries(POST_MANY)) { const x = (f as Record<string, string[]>)[k]; if (x?.length) rows.push([label, x.map((a) => o[a] || a).join(', ')]); }
  if (f.tenants) rows.push([t('Жильцов'), t('до {n}', { n: f.tenants })]);
  if (f.deposit !== undefined) rows.push([t('Залог'), f.deposit ? money(f.deposit) : t('без залога')]);
  if (f.sale_price) rows.push([t('Цена продажи'), money(f.sale_price)]);
  if (f.headline) rows.push([t('Заголовок'), f.headline]);
  return rows;
}

/** The post fields the agent fills (the old HTML generator's groups): one-of selects, amenity / term checkboxes, numbers. */
function PostFieldsForm({ value: f, onChange, price }: { value: PostFields; onChange: (f: PostFields) => void; price?: number | null }) {
  const sale = f.deal === 'Sale';
  const set = (k: string, x: unknown) => onChange({ ...f, [k]: x === '' || x === null ? undefined : x });
  const num = (s: string) => (s === '' ? undefined : Number(s));
  const toggle = (k: string, a: string) => { const cur = (f as Record<string, string[]>)[k] || []; set(k, cur.includes(a) ? cur.filter((x) => x !== a) : [...cur, a]); };
  return (
    <div className="col gap-lg">
      <div className="row row-between row-wrap gap-sm">
        <span className="label">{t('Поля поста')}</span>
        <Seg value={f.deal || 'Rent'} onChange={(d) => set('deal', d)} options={[{ value: 'Rent', label: t('Аренда') }, { value: 'Sale', label: t('Продажа') }]} />
      </div>
      <div className="form-grid">
        {Object.entries(POST_ONE).filter(([k]) => (sale ? !RENT_ONLY.includes(k) : k !== 'condition')).map(([k, [label, o]]) => (
          <Field key={k} label={label}><Select value={(f as Record<string, string>)[k] || ''} placeholder="—" onChange={(e) => set(k, e.target.value)} options={opts(o)} /></Field>
        ))}
        {sale ? <Field label={t('Цена продажи, $')}><Input type="number" value={f.sale_price ?? ''} onChange={(e) => set('sale_price', num(e.target.value))} /></Field> : (
          <>
            <Field label={t('Жильцов, до')}><Input type="number" min={1} value={f.tenants ?? ''} onChange={(e) => set('tenants', num(e.target.value))} /></Field>
            <Field label={t('Залог, $')} hint={t('Равен цене, можно изменить. 0 — без залога')}><Input type="number" min={0} value={f.deposit ?? price ?? ''} onChange={(e) => set('deposit', num(e.target.value))} /></Field>
          </>
        )}
        <Field label={t('Заголовок')} className="span2"><Input value={f.headline || ''} placeholder={t('Например: No one lived')} onChange={(e) => set('headline', e.target.value)} /></Field>
      </div>
      {Object.entries(POST_MANY).filter(([k]) => !(sale && RENT_ONLY.includes(k))).map(([k, [label, o]]) => (
        <div key={k}>
          <span className="label">{label}</span>
          {k === 'amenities' && !sale && <div className="muted" style={{ fontSize: 12 }}>{t('Лифт, духовка, парковка, кондиционер без галочки уйдут в пост с ✖️')}</div>}
          <div className="row row-wrap gap-sm mt">
            {opts(o).map(({ value: a, label: l }) => {
              const on = ((f as Record<string, string[]>)[k] || []).includes(a);
              return <button key={a} type="button" role="checkbox" aria-checked={on} className={`chip ${on ? 'active' : ''}`} onClick={() => toggle(k, a)}>{on ? <CheckCircle2 size={14} /> : null}{l}</button>;
            })}
          </div>
        </div>
      ))}
    </div>
  );
}

function EditModal({ p, open, onClose, repost }: { p: Property; open: boolean; onClose: () => void; repost?: boolean }) {
  const { toast, set } = useProp(p.id);
  type Form = Partial<Property> & { owner_name?: string; owner_phone?: string; owner_id?: number | null };
  const [f, setF] = useState<Form>({});
  const v: Form = { ...p, owner_name: p.owner?.name || '', owner_phone: p.owner?.phone || '', ...f };
  const upd = <K extends keyof Form>(k: K, val: Form[K]) => setF((x) => ({ ...x, [k]: val }));
  const [reason, setReason] = useState('');
  const save = useMutation({
    mutationFn: () => api.properties.update(p.id, { ...f, reason: reason || undefined }),
    onSuccess: (r) => { set(r); toast(r.change_request ? REQUEST_SENT : t('Сохранено')); onClose(); setF({}); setReason(''); },
    onError: (e) => toast(errText(e), true),
  });
  // «Сохранить и обновить пост» (staff): the changes and a fresh post. An agent's changes go to the operator (David 11-24),
  // the post follows his approval by itself (backend properties.split_post)
  const toOperator = !!p.post_moderated;
  const again = useMutation({
    mutationFn: async () => {
      if (toOperator) return api.properties.update(p.id, { ...f, reason: reason || undefined });
      if (Object.keys(f).length) await api.properties.update(p.id, { ...f, reason: reason || undefined });
      return api.properties.publish(p.id, { channel: 'tg', tg: true, site: !p.site_hidden });
    },
    onSuccess: (r) => { set(r); toast(toOperator ? (r.change_request ? t('Изменения ушли оператору · после подтверждения пост обновится сам') : t('Сохранено')) : t('Сохранено · пост перевыложен, старый удалён')); onClose(); setF({}); setReason(''); },
    onError: (e) => toast(errText(e), true),
  });
  // after the operator's verification these fields go to «Запросы» (backend properties.LOCKED_AFTER_VERIFY)
  const locked = new Set(p.locked_fields || []);
  const lockHint = (k: string) => (locked.has(k) ? t('Изменение уйдёт оператору на проверку') : undefined);
  const numOrNull = (s: string) => (s === '' ? null : Number(s));
  const where = useQuery({ queryKey: ['property-elsewhere', p.id], queryFn: () => api.properties.elsewhere(p.id), enabled: open && !!v.is_exclusive });
  const found = where.data?.items || [];
  return (
    <Modal open={open} onClose={onClose} title={repost ? t('Редактировать пост') : t('Поля объекта')}>
      {repost && <div className="alert alert-info" style={{ fontSize: 13, marginBottom: 12 }}>{toOperator ? t('Пост собирается из этих полей. Изменения уйдут оператору на проверку, после подтверждения пост в канале обновится сам.') : t('Пост собирается из этих полей. «Сохранить и обновить пост» сохранит изменения и выложит пост заново (старый удалится, сайт обновится).')}</div>}
      <div className="form-grid">
        {(p.missing || []).length > 0 && <div className="alert alert-warn span2" style={{ fontSize: 13, display: 'block' }}>{t('Для отправки оператору не хватает:')} <b>{(p.missing || []).join(', ')}</b></div>}
        <Field label={t('Район')}><DistrictSelect value={v.district} onChange={(x) => upd('district', x)} /></Field>
        <Field label={t('Адрес')} hint={lockHint('address')}><Input value={v.address || ''} onChange={(e) => upd('address', e.target.value)} /></Field>
        <Field label={t('Цена, $')} hint={lockHint('price')}><Input type="number" value={v.price ?? ''} onChange={(e) => upd('price', numOrNull(e.target.value))} /></Field>
        <Field label={t('Спальни')}><Input type="number" value={v.bedrooms ?? ''} onChange={(e) => upd('bedrooms', numOrNull(e.target.value))} /></Field>
        <Field label={t('Площадь, м²')}><Input type="number" value={v.area ?? ''} onChange={(e) => upd('area', numOrNull(e.target.value))} /></Field>
        <Field label={t('Этаж')}><Input type="number" value={v.floor ?? ''} onChange={(e) => upd('floor', numOrNull(e.target.value))} /></Field>
        <Field label={t('Этажей в доме')}><Input type="number" value={v.total_floors ?? ''} onChange={(e) => upd('total_floors', numOrNull(e.target.value))} /></Field>
        <Field label={t('Собственник')} hint={lockHint('owner_name')}><Input value={v.owner_name || ''} onChange={(e) => upd('owner_name', e.target.value)} /></Field>
        <Field label={t('Телефон собственника')} hint={lockHint('owner_phone')}><Input type="tel" inputMode="tel" value={v.owner_phone || ''} onChange={(e) => upd('owner_phone', phoneInput(e))} /></Field>
        {!p.owner && (f.owner_name !== undefined || f.owner_phone !== undefined) && !f.owner_id && (
          <div className="span2"><OwnerSuggest query={f.owner_phone || f.owner_name || ''}
            onPick={(o) => setF((x) => ({ ...x, owner_id: o.id, owner_name: o.name || '', owner_phone: o.phone || '' }))} /></div>
        )}
        <Field label={t('Кадастровый код')} className="span2" hint={cadastralHint(v.cadastral_code, p.cadastral_code, lockHint('cadastral_code'))}><Input value={v.cadastral_code || ''} placeholder="01.14.12.003.045" onChange={(e) => upd('cadastral_code', cadastralInput(e))} onBlur={() => { if (f.cadastral_code) upd('cadastral_code', cadastralNorm(f.cadastral_code)); }} /></Field>
        <Field label={t('Описание')} className="span2"><Textarea value={v.description || ''} onChange={(e) => upd('description', e.target.value)} /></Field>
        <div className="span2 col gap-sm">
          <Switch checked={!!v.is_exclusive} onChange={(x) => upd('is_exclusive', x)} label={t('Эксклюзив')} />
          {locked.has('is_exclusive') && <span className="muted" style={{ fontSize: 12 }}>{lockHint('is_exclusive')}</span>}
          {v.is_exclusive && (where.isLoading ? <span className="muted" style={{ fontSize: 12 }}>{t('Проверяем другие сайты…')}</span>
            : found.length ? (
              <div className="alert alert-warn col gap-sm" style={{ fontSize: 13 }}>
                <span><b>{t('Этот объект найден ещё на: {s}.', { s: [...new Set(found.map((a) => srcName(a.source)))].join(', ') })}</b> {t('Попросите собственника снять объявления, иначе эксклюзив не опубликовать.')}</span>
                {found.map((a) => (
                  <span key={a.listing_id} className="muted">
                    {srcName(a.source)} · {a.verdict === 'linked' ? t('привязано к объекту') : a.verdict === 'same' ? t('тот же объект') : t('похож: {r}', { r: a.reasons.join(', ') })}
                    {a.url && <> · <a href={a.url} target="_blank" rel="noreferrer">{t('открыть')}</a></>}
                  </span>
                ))}
              </div>
            ) : where.data ? <div className="alert alert-ok" style={{ fontSize: 13 }}>{t('На других сайтах этот объект не найден')}</div> : null)}
        </div>
      </div>
      <div className="divider" />
      <PostFieldsForm value={v.post_fields || {}} price={v.price} onChange={(pf) => upd('post_fields', pf)} />
      {locked.size > 0 && (
        <div className="col gap-sm mt-lg">
          <div className="alert alert-info" style={{ fontSize: 13 }}>{t('Объект проверен оператором: собственник и телефон, адрес, кадастр, ссылка, эксклюзив, цена и залог меняются только через «Запрос оператору». Остальное сохранится сразу.')}</div>
          <Field label={t('Причина изменения')} hint={t('Оператор увидит её в запросе')}><Input value={reason} placeholder={t('Например: собственник сменил номер')} onChange={(e) => setReason(e.target.value)} /></Field>
        </div>
      )}
      <div className="modal-actions mt-lg">
        {repost
          ? <><Button block loading={again.isPending} disabled={!!cadastralError(v.cadastral_code, p.cadastral_code) || (toOperator && !Object.keys(f).length)} onClick={() => again.mutate()}>{toOperator ? <><Send size={16} />{t('Отправить оператору')}</> : <><RotateCcw size={16} />{t('Сохранить и обновить пост')}</>}</Button>
            <Button block variant="ghost" onClick={onClose}>{t('Отмена')}</Button></>
          : <div className="row"><Button loading={save.isPending} disabled={!!cadastralError(v.cadastral_code, p.cadastral_code)} onClick={() => save.mutate()}>{t('Сохранить')}</Button><Button variant="ghost" onClick={onClose}>{t('Отмена')}</Button></div>}
      </div>
    </Modal>
  );
}

/** The owner card's «Изменить»: name / phone of the owner (the contact is on the owner, so his other objects show it too).
 * A new number that is another owner's in the base: link the object to him or keep this owner separate (backend
 * properties._set_owner_contact). After verification an agent's change is a «Запрос оператору». */
function OwnerEditModal({ p, open, onClose }: { p: Property; open: boolean; onClose: () => void }) {
  const { toast, set } = useProp(p.id);
  const [name, setName] = useState(p.owner?.name || '');
  const [phones, setPhones] = useState(() => phoneRows(p.owner));
  const phone = phones[0]?.phone || '';
  const [link, setLink] = useState<'link' | 'separate'>('link');
  const [reason, setReason] = useState('');
  const onTaken = useOwnerTaken(p);
  const digits = (s?: string | null) => (s || '').replace(/\D/g, '').replace(/^995/, '').replace(/^0/, '');
  const changed = digits(phone) !== digits(p.owner?.phone);
  const [q, setQ] = useState('');
  useEffect(() => { const h = setTimeout(() => setQ(changed && digits(phone).length >= 9 ? phone : ''), 300); return () => clearTimeout(h); }, [phone, changed]);
  const found = useQuery({ queryKey: ['owners', 'same-phone', q], queryFn: () => api.owners.list(q, 'all'), enabled: !!q });
  // agents get only full-number matches with the phone hidden; staff get substring matches: compare the digits
  const other = q ? itemsOf(found.data).find((o) => o.id !== p.owner?.id && (!o.phone || [o.phone, ...(o.phones || []).map((r) => r.phone)].some((x) => digits(x) === digits(q)))) : undefined;
  const locked = (p.locked_fields || []).includes('owner_phone');
  const save = useMutation({
    mutationFn: () => api.properties.update(p.id, other && link === 'link' ? { owner_id: other.id, reason: reason || undefined }
      : { owner_name: name, owner_phone: cleanPhones(phones)[0]?.phone, owner_phones: samePhones(phones, phoneRows(p.owner)) ? undefined : cleanPhones(phones),
          owner_separate: !!other || undefined, reason: reason || undefined }),
    onSuccess: (r) => { set(r); toast(r.change_request ? REQUEST_SENT : t('Сохранено')); onClose(); },
    onError: (e) => onTaken(e, cleanPhones(phones)[0]?.phone),
  });
  return (
    <Modal open={open} onClose={onClose} title={t('Контакт собственника')}>
      <div className="form-grid">
        <Field label={t('Имя собственника')} className="span2"><Input value={name} onChange={(e) => setName(e.target.value)} /></Field>
        <PhonesField value={phones} onChange={setPhones} label={t('Телефон собственника')} />
      </div>
      {other && (
        <div className="alert alert-warn col gap-sm mt" style={{ fontSize: 13 }}>
          <span>{t('Этот номер уже есть в базе: {name}', { name: other.name || '—' })}{other.address ? ` · ${other.address}` : ''}</span>
          <Seg value={link} onChange={setLink} options={[{ value: 'link', label: t('Привязать к нему') }, { value: 'separate', label: t('Оставить отдельным') }]} />
        </div>
      )}
      <div className="muted mt" style={{ fontSize: 12 }}>{t('Контакт меняется у собственника — и во всех его объектах. В посты контакты не попадают.')}</div>
      {locked && (
        <div className="col gap-sm mt">
          <div className="alert alert-info" style={{ fontSize: 13 }}>{t('Объект проверен оператором: изменение уйдёт ему на проверку')}</div>
          <Field label={t('Причина изменения')}><Input value={reason} placeholder={t('Например: собственник сменил номер')} onChange={(e) => setReason(e.target.value)} /></Field>
        </div>
      )}
      <div className="row mt-lg"><Button loading={save.isPending} disabled={!name.trim() && !cleanPhones(phones).length} onClick={() => save.mutate()}>{t('Сохранить')}</Button><Button variant="ghost" onClick={onClose}>{t('Отмена')}</Button></div>
    </Modal>
  );
}

/** «В архив» with a mandatory reason; «Дубль» deletes the object (its leads and deals move to the original). After verification the agent's archive is a request. */
function ArchiveModal({ p, open, onClose, preset = '' }: { p: Property; open: boolean; onClose: () => void; preset?: string }) {
  const confirm = useConfirm();
  const { toast, set } = useProp(p.id);
  const [reason, setReason] = useState(preset);
  const [comment, setComment] = useState('');
  const [dup, setDup] = useState('');
  const [price, setPrice] = useState('');
  const owner = reason === 'rented_by_owner';
  const L = useLease();
  const nav = useNavigate();
  const m = useMutation({
    mutationFn: () => api.properties.archive(p.id, { reason, comment: comment || undefined, duplicate_of: reason === 'duplicate' ? Number(dup.replace('#', '')) || null : null,
      ...(owner ? { ...L.lease, price: price || undefined } : {}) }),
    onSuccess: (r) => {
      if (r.archive_reason === 'duplicate' && r.duplicate_of && !r.change_request) {  // the duplicate is deleted: go to the original
        toast(t('Дубль удалён')); onClose();
        nav(`${window.location.pathname.startsWith('/m/') ? '/m/properties' : '/objects'}/${r.duplicate_of}`);
        return;
      }
      set(r); toast(r.change_request ? REQUEST_SENT : t('Объект в архиве')); onClose();
    },
    onError: (e) => toast(errText(e), true),
  });
  const ok = !!reason && (reason !== 'other' || !!comment.trim()) && (reason !== 'duplicate' || !!dup.trim()) && (!owner || L.ok);
  return (
    <Modal open={open} onClose={onClose} title={preset ? ARCHIVE_REASON[preset] : t('В архив')}>
      <div className="col gap-lg">
        <Field label={t('Причина')}><Select value={reason} placeholder="—" onChange={(e) => setReason(e.target.value)} options={Object.entries(ARCHIVE_REASON).filter(([value]) => value !== 'idle_return').map(([value, label]) => ({ value, label }))} /></Field>
        {reason === 'duplicate' && <Field label={t('Дубль объекта №')} hint={t('Номер объекта, который остаётся в базе. Этот объект будет удалён, его заявки, показы и сделки перейдут туда')}><Input inputMode="numeric" value={dup} placeholder="#123" onChange={(e) => setDup(e.target.value)} /></Field>}
        {owner && <>{L.fields}<Field label={t('Цена, $ (необязательно)')}><Input inputMode="numeric" value={price} onChange={(e) => setPrice(e.target.value.replace(/\D/g, ''))} /></Field></>}
        <Field label={reason === 'other' ? t('Комментарий (обязательно)') : t('Комментарий')}><Input value={comment} onChange={(e) => setComment(e.target.value)} /></Field>
        {(p.locked_fields || []).length > 0 && <div className="alert alert-info" style={{ fontSize: 13 }}>{t('Объект проверен оператором: архивация уйдёт оператору на проверку.')}</div>}
        <Button block disabled={!ok} loading={m.isPending} onClick={async () => { if (reason !== 'duplicate' || await confirm(t('Объект будет удалён как дубль. Это нельзя отменить'))) m.mutate(); }}><Archive size={16} />{(p.locked_fields || []).length ? t('Отправить запрос') : preset ? t('Подтвердить') : t('В архив')}</Button>
      </div>
    </Modal>
  );
}

/** The archived «Собственник сдал»: the lease (the flat frees up then), or «укажите даты аренды» for one without dates */
function OwnerLeaseNote({ p, canWork }: { p: Property; canWork: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <div className={`alert ${p.rent_to ? 'alert-info' : 'alert-warn'}`}>
      <span>{p.rent_to
        ? t('Сдано собственником {from} – {to} · освобождается {to}', { from: p.rent_from ? fmtDate(p.rent_from) : '…', to: fmtDate(p.rent_to) })
        : <b>{t('Укажите даты аренды: без них квартира потеряется, когда освободится')}</b>}</span>
      {canWork && <Button size="sm" variant="secondary" onClick={() => setOpen(true)}><Pencil size={14} />{p.rent_to ? t('Изменить даты') : t('Указать даты')}</Button>}
      {open && <OwnerLeaseModal p={p} datesOnly onClose={() => setOpen(false)} />}
    </div>
  );
}

export function PropertyDetail({ id, compact }: { id: number; compact?: boolean }) {
  const { user, isStaff } = useAuth();
  // an exclusive's duplicate search runs in the worker (~30 s): the card looks again until it is done
  const { data: p, isLoading, error } = useQuery({ queryKey: ['property', id], queryFn: () => api.properties.get(id),
    refetchInterval: (q) => (q.state.data?.duplicate_scan?.pending ? 10_000 : false) });
  const hist = useQuery({ queryKey: ['property-history', id], queryFn: () => api.properties.history(id) });
  const [edit, setEdit] = useState<boolean | 'repost'>(false);
  const [ownerEdit, setOwnerEdit] = useState(false);
  const [archiving, setArchiving] = useState<string | false>(false);
  const step = useMemo(() => (p ? stepOf(p) : 0), [p]);

  if (isLoading) return <Loading />;
  // another agent's / the platform's object: the API answers 404 without saying whether it exists (11-16)
  if (error instanceof ApiError && error.status === 404)
    return <Empty icon={<Lock size={32} />} text={t('Нет доступа к объекту')}><div className="muted" style={{ fontSize: 13 }}>{t('Объект закреплён за другим агентом или не найден.')}</div></Empty>;
  if (error || !p) return <ErrorBox error={error} />;
  const mine = p.agent_id === user?.id || p.agent?.id === user?.id;
  const canWork = mine || isStaff;

  // «Объект вручную»: the owner came himself, no call and no shoot booking: straight to the post generator
  const walkIn = p.status === 'owner_confirmed' && p.source_type === 'direct';
  let action: React.ReactNode = null;
  if (['taken', 'call_failed'].includes(p.status)) action = canWork ? <CallStep p={p} /> : null;
  else if (p.status === 'owner_confirmed' && !walkIn) action = canWork ? <ShootStep p={p} /> : null;
  else if (walkIn || ['shoot_scheduled', 'media_uploaded', 'returned'].includes(p.status)) action = <PostGeneratorStep key={p.id} p={p} canEdit={canWork} />;
  else if (p.status === 'review') action = isStaff ? <ReviewStep p={p} /> : <div className="alert alert-info">{t('Отправлено оператору · фото и чек-лист закрыты. Оператор проверит и опубликует, обычно до 1 часа. Если нужно что-то переснять, объект вернётся к вам с комментарием.')}</div>;
  else if (p.status === 'agent_marked') action = <div className="alert alert-info">{t('Ответил агент · объект остаётся в базе с пометкой «агент»')}</div>;
  else if (['published', 'rented'].includes(p.status)) action = (
    <div className="col">
      <div className="alert alert-ok">{t('Опубликовано {at}', { at: fmtDateTime(p.published_at) })}</div>
      {p.status === 'published' && (
        <div className="row row-wrap gap-sm">
          <span className="label">{t('Где опубликован')}</span>
          <Badge tone={p.site_hidden ? 'grey' : 'ok'}>{p.site_hidden ? t('не на сайте') : t('на сайте hge.ge')}</Badge>
          <Badge tone={p.post_url ? 'ok' : 'grey'}>{p.post_url ? t('в Telegram') : t('нет поста в Telegram')}</Badge>
        </div>
      )}
      <div className="row row-wrap">
        {p.post_url && <a className="btn" href={p.post_url} target="_blank" rel="noreferrer"><ExternalLink size={16} />{t('Пост в канале')}</a>}
        {canWork && p.status === 'published' && <SiteButton p={p} />}
      </div>
      {canWork && p.status === 'published' && <PublishedActions p={p} onArchive={setArchiving} />}
      {canWork && p.status === 'published' && <Republish p={p} onEdit={() => setEdit('repost')} />}
      {/* David 11-23: a rented flat is free again — back on the market from the card, not through the archive */}
      {p.status === 'rented' && canWork && <div className="row row-wrap gap-sm">
        {!!p.published_at && <Resume p={p} republish />}
        <Resume p={p} />
      </div>}
    </div>
  );
  // the media / review steps show the files with their own controls
  const mediaInAction = walkIn || ['shoot_scheduled', 'media_uploaded', 'returned'].includes(p.status) || (p.status === 'review' && isStaff);
  const ownerVisible = !!(p.owner?.phone || p.owner_phone);
  const objLink = (oid: number) => (compact ? `/m/properties/${oid}` : `/objects/${oid}`);

  return (
    <div className="col gap-lg">
      <div className="card card-pad">
        <div className="row row-between row-wrap" style={{ alignItems: 'flex-start' }}>
          <div>
            <div className="label">{t('Объект')} #{p.id} · {SOURCE_LABEL[p.source_type || ''] || p.source_type || '—'}</div>
            <h1 style={{ fontSize: compact ? 20 : 24 }}>{p.address || t('Без адреса')}</h1>
            <div className="row row-wrap gap-sm mt">
              <PropertyStatusBadge status={p.status} />
              {p.is_exclusive && <Badge tone="solid">{t('эксклюзив')}</Badge>}
              {!!p.duplicate_scan?.n && <Badge tone={p.duplicate_scan.strong?.length ? 'red' : 'warn'}>{t('Найдены возможные дубли: {n}', { n: p.duplicate_scan.n })}</Badge>}
              <ExclusiveConflictBadge p={p} />
              <AiFlagBadges flags={p.ai_flags} />
            </div>
          </div>
          <div className="price-col">
            <div className="price" style={{ fontSize: 24 }}>{money(p.post_fields?.deal === 'Sale' ? p.post_fields.sale_price ?? p.price : p.price, p.currency || 'USD')}{p.post_fields?.deal !== 'Sale' && <span className="muted" style={{ fontSize: 12 }}>{t('/мес')}</span>}</div>
            <div className="muted" style={{ fontSize: 13 }}>{[districtLabel(p.district), bedroomsLabel(p.bedrooms), p.area ? `${p.area} ${t('м²')}` : null].filter(Boolean).join(' · ')}</div>
          </div>
        </div>
        <div className="divider" />
        <Stepper current={step} />
        {/* Irakli 11-15: the same wizard as «Новый договор», the object chosen and prefilled; the object turns «Сдано» on its deal's confirm */}
        {canWork && p.status !== 'archived' && (  // recruits too (Irakli 11-22)
          <div className="row mt"><Link className="btn btn-secondary" to={`${compact ? '/m' : ''}/contracts?property_id=${p.id}`}><FileText size={16} />{t('Оформить договор аренды')}</Link></div>
        )}
      </div>

      <div className={compact ? 'col gap-lg' : 'split'}>
        <div className="col gap-lg">
          {p.needs_check && <NeedsCheck p={p} />}
          {/* Stanislav 11-17: the operator checks the similar ones before he publishes (a twin of a posted object) */}
          {p.status === 'review' && isStaff && (
            <Card label={t('Проверка на дубль')} title={t('Похожие объекты')}>
              <DupScan p={p} />
              <ExclusiveHangs p={p} />
              <SimilarList subject={`p:${p.id}`} intro={t('Перед публикацией проверьте похожие: это может быть дубль объекта из базы или уже выложенного.')}
                emptyText={t('Похожих объектов не найдено: ни в базе, ни на сайтах, ни у конкурентов.')} />
            </Card>
          )}
          {action && (
            <Card label={t('Следующий шаг')} title={STEPS[Math.min(step, STEPS.length - 1)]}>
              {action}
              {canWork && ['taken', 'call_failed', 'owner_confirmed', 'shoot_scheduled'].includes(p.status) && <OwnerOutcomes p={p} />}
            </Card>
          )}
          {!['archived', 'rented'].includes(p.status) && <Demand p={p} />}
          {p.status === 'archived' && (
            <div className="alert alert-warn">
              <span>
                {t('В архиве')}{p.archived_at ? ` ${t('с {d}', { d: fmtDate(p.archived_at) })}` : ''} · <b>{ARCHIVE_REASON[p.archive_reason || ''] || p.archive_reason || t('причина не указана')}</b>
                {p.archive_reason === 'duplicate' && p.duplicate_of ? <> {t('объекта')} <Link to={objLink(p.duplicate_of)}>#{p.duplicate_of}</Link></> : null}
                {p.archive_comment ? ` · ${p.archive_comment}` : ''}
              </span>
              {(canWork && ['paused', 'unpublished', 'rented_by_owner', 'not_actual', 'idle_return'].includes(p.archive_reason || '')
                || (p.owner?.agent?.id === user?.id && ['rented_by_owner', 'not_actual', 'idle_return'].includes(p.archive_reason || ''))) && <Resume p={p} />}
              {canWork && ['rented_by_owner', 'rented_by_us'].includes(p.archive_reason || '') && !!p.published_at && <Resume p={p} republish />}
            </div>
          )}
          {p.status === 'archived' && p.archive_reason === 'rented_by_owner' && <OwnerLeaseNote p={p} canWork={canWork} />}
          {!!p.change_requests?.length && (
            <Card label={t('Запрос оператору')} title={`${t('На проверке у оператора')} · ${p.change_requests.length}`}>
              {p.change_requests.map((r) => <RequestRow key={r.id} r={r} showObject={false} />)}
            </Card>
          )}
          {!(p.status === 'review' && isStaff) && (
            <Card label={t('Дубли')} title={t('Похожие объекты')} actions={<MyhomeCopies p={p} />}>
              <DupScan p={p} />
              <ExclusiveHangs p={p} />
              <div className="muted" style={{ fontSize: 13, marginBottom: 8 }}>{t('Те же фото, адрес, собственник или параметры — в нашей базе, на ss.ge / myhome.ge и у конкурентов. Отметьте: дубль или нет.')}</div>
              <SimilarList subject={`p:${p.id}`} emptyText={t('Похожих объектов не найдено: ни в базе, ни на сайтах, ни у конкурентов.')} />
              <CompareLink p={p} />
            </Card>
          )}
          {!mediaInAction && <Gallery p={p} />}
          <Publications p={p} />
          <Ads p={p} />
          <Lease p={p} />
          <Deals p={p} compact={compact} />
          {!compact && <Card label={t('История')} title={t('События объекта')}><Timeline events={itemsOf(hist.data)} /></Card>}
        </div>
        <div className="col gap-lg">
          <Card label={t('Собственник')} title={<>{p.owner?.name || '—'} {p.owner?.is_agent ? <Badge tone="warn">{t('агент')}</Badge> : null}</>}
            actions={canWork && (ownerVisible || !p.owner) ? <Button size="sm" variant="secondary" onClick={() => setOwnerEdit(true)}><Pencil size={14} />{p.owner ? t('Изменить') : t('Добавить')}</Button> : undefined}>
            <KV items={[
              [t('Телефон'), <OwnerPhone p={p} />],
              // one agent line: the owner's agent (stage 6.5 binding, backend deal_block); no owner binding yet → the object's agent
              [t('Агент собственника'), agentLabel(p.owner?.agent || p.agent)],
            ]} />
            {!ownerVisible && p.owner && <div className="muted mt" style={{ fontSize: 12 }}>{t('Контакты видны оператору и агенту объекта')}</div>}
            {p.owner && !p.owner.is_agent && (isStaff ? <OwnerTransfer p={p} /> : (mine || p.owner.agent?.id === user?.id) && <OwnerTransfer p={p} request />)}
          </Card>
          <Card label={t('Объект')} title={t('Параметры')} actions={canWork ? <Button size="sm" variant="secondary" onClick={() => setEdit(true)}><Pencil size={14} />{t('Править')}</Button> : undefined}>
            <KV items={[
              [t('Адрес'), p.address || '—'],
              [t('Район'), districtLabel(p.district) || '—'],
              [t('Цена'), money(p.price, p.currency || 'USD')],
              [t('Спальни'), bedroomsLabel(p.bedrooms) || '—'],
              [t('Комнат'), p.rooms ?? '—'],
              [t('Площадь'), p.area ? `${p.area} ${t('м²')}` : '—'],
              [t('Этаж'), p.floor ? `${p.floor}${p.total_floors ? ` ${t('из')} ${p.total_floors}` : ''}` : '—'],
              [t('Кадастр'), p.cadastral_code || '—'],
              [t('Эксклюзив'), p.is_exclusive ? t('да') : t('нет')],
              ...postFieldRows(p.post_fields),
              [t('Дубль объекта'), p.duplicate_of ? <Link to={compact ? `/m/properties/${p.duplicate_of}` : `/objects/${p.duplicate_of}`}>#{p.duplicate_of}</Link> : '—'],
              [t('Создан'), fmtDateTime(p.created_at)],
              [t('Взят'), fmtDateTime(p.taken_at)],
              [t('Съёмка'), fmtDateTime(p.shoot_at)],
              [t('Опубликован'), fmtDateTime(p.published_at)],
              [t('Проверен оператором'), p.verified_at ? fmtDateTime(p.verified_at) : t('нет')],
              ...(p.archive_reason ? [[t('Причина архива'), `${ARCHIVE_REASON[p.archive_reason] || p.archive_reason}${p.archive_comment ? ` · ${p.archive_comment}` : ''}`] as [string, React.ReactNode]] : []),
              [t('Источник'), p.source_url ? <a href={p.source_url} target="_blank" rel="noreferrer">{t('открыть')}</a> : '—'],
            ]} />
            {p.description && <p className="muted mt" style={{ fontSize: 13 }}>{p.description}</p>}
            {canWork && !['archived', 'rented'].includes(p.status) && (
              <Button variant="ghost" size="sm" className="mt" onClick={() => setArchiving('')}><Archive size={14} />{t('В архив')}</Button>
            )}
          </Card>
          {compact && <Card label={t('История')} title={t('События')}><Timeline events={itemsOf(hist.data)} /></Card>}
        </div>
      </div>
      <EditModal p={p} open={!!edit} repost={edit === 'repost'} onClose={() => setEdit(false)} />
      {ownerEdit && <OwnerEditModal p={p} open onClose={() => setOwnerEdit(false)} />}
      {archiving !== false && <ArchiveModal key={archiving} p={p} preset={archiving} open onClose={() => setArchiving(false)} />}
    </div>
  );
}
