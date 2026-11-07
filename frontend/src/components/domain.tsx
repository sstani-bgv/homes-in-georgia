// Domain widgets shared by desktop and mobile views.
import { useState } from 'react';
import type { ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Bot, Clock, Copy, ExternalLink, Lock, MapPin, User as UserIcon } from 'lucide-react';
import { api } from '../api';
import type { AiFlags, EventItem, Lead, LeadStatus, Listing, Property, PropertyStatus, SimilarFlat } from '../api/types';
import { thumbUrl } from '../api/client';
import { Badge, Button, ErrorBox, KV, Loading, Modal, useNow } from './ui';
import { useToast } from './toast';
import { ARCHIVE_REASON, CR_KIND, LEAD_STATUS, OUTCOME_LABEL, PROPERTY_STATUS, REFUSAL_LABEL, bedroomsLabel, fmtDateTime, money, SOURCE_LABEL } from '../lib/format';

/** «Скопировать» next to a phone: tel: links do nothing on desktop / Telegram Desktop. */
export function CopyPhone({ phone }: { phone: string }) {
  const toast = useToast();
  const copy = () => (navigator.clipboard ? navigator.clipboard.writeText(phone) : Promise.reject())
    .then(() => toast(`Скопировано: ${phone}`), () => toast('Не удалось скопировать, выделите номер вручную', true));
  return <Button size="sm" variant="secondary" onClick={(e) => { e.preventDefault(); e.stopPropagation(); copy(); }}><Copy size={14} />Скопировать</Button>;
}

const SLA_DEFAULT: Record<string, number> = { operator_take: 5, agent_slots: 30, slots_to_client: 60 };

function fmtDur(ms: number): string {
  const s = Math.floor(Math.abs(ms) / 1000);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const p = (n: number) => String(n).padStart(2, '0');
  return h ? `${h}:${p(m)}:${p(sec)}` : `${p(m)}:${p(sec)}`;
}

/** SLA timer chip: green > 40% window left, yellow ≤ 40%, red overdue (§4.3). Ticks every second. */
export function SlaChip({ lead }: { lead: Pick<Lead, 'sla_due_at' | 'sla_stage' | 'sla_window_min'> }) {
  const now = useNow(1000);
  if (!lead.sla_due_at) return null;
  const due = new Date(lead.sla_due_at).getTime();
  const windowMs = (lead.sla_window_min || SLA_DEFAULT[lead.sla_stage || ''] || 30) * 60_000;
  const left = due - now;
  const tone = left < 0 ? 'red' : left / windowMs <= 0.4 ? 'yellow' : 'green';
  return (
    <span className={`sla sla-${tone}`} title="Срок шага">
      <Clock size={11} strokeWidth={3} />
      {left < 0 ? `−${fmtDur(left)}` : fmtDur(left)}
    </span>
  );
}

export function LeadStatusBadge({ status }: { status: LeadStatus }) {
  const tone = status === 'rented' ? 'ok' : status === 'refused' ? 'grey' : status === 'new' ? 'redsoft' : status === 'deal_pending' ? 'warn' : 'blue';
  return <Badge tone={tone}>{LEAD_STATUS[status] || status}</Badge>;
}

export function PropertyStatusBadge({ status }: { status: PropertyStatus }) {
  const tone = ['published', 'rented'].includes(status) ? 'ok' : status === 'returned' || status === 'call_failed' ? 'redsoft'
    : status === 'review' ? 'warn' : status === 'archived' || status === 'agent_marked' ? 'grey' : 'blue';
  return <Badge tone={tone}>{PROPERTY_STATUS[status] || status}</Badge>;
}

export function AiFlagBadges({ flags }: { flags?: AiFlags | null }) {
  if (!flags) return null;
  const reasons = (flags.reasons || []).join(' · ');
  return (
    <>
      {flags.owner && <Badge tone="ok" title={reasons}>собственник</Badge>}
      {flags.agent_suspect && <Badge tone="warn" title={reasons}>похоже на агента</Badge>}
      {flags.duplicate && <Badge tone="red" title={reasons}>дубль</Badge>}
    </>
  );
}

/** Red «Эксклюзив висит на ss.ge»: the exclusive object still has live ads on other sites (stage 4). */
export function ExclusiveConflictBadge({ p }: { p: Pick<Property, 'exclusive_conflict'> }) {
  const ads = p.exclusive_conflict || [];
  if (!ads.length) return null;
  const where = [...new Set(ads.map((a) => SOURCE_LABEL[a.source || ''] || a.source || 'другом сайте'))].join(', ');
  return <Badge tone="red" title="Попросите собственника снять объявление">Эксклюзив висит на {where}</Badge>;
}

/** Full listing: photos, text, all source links. A merged duplicate keeps every copy's link in `urls`. */
/** The listing next to a similar flat from the agency channel, so the agent decides by eye whether it is the same one. */
export function SimilarInChannel({ listing, similar }: { listing: Listing & { floor?: number | null }; similar: SimilarFlat[] }) {
  return (
    <div className="col gap-sm">
      <div className="alert alert-warn">Похожая квартира уже есть в канале агентства. Сравните фото и параметры: это одна квартира или разные в одном доме?</div>
      {similar.map((s) => (
        <div key={s.id} className="card col gap-sm" style={{ padding: 12 }}>
          <div className="row gap-sm" style={{ alignItems: 'flex-start' }}>
            {s.photo && <img src={thumbUrl(s.photo)} alt="" loading="lazy" style={{ width: 120, height: 90, objectFit: 'cover', borderRadius: 8, flex: 'none' }} />}
            <div className="col" style={{ minWidth: 0 }}>
              <strong className="ellipsis">{s.address || 'Пост в канале'}</strong>
              <span className="faint" style={{ fontSize: 12 }}>в {s.distance_m} м от этого адреса</span>
              {s.url && <a href={s.url} target="_blank" rel="noreferrer" className="row gap-sm" style={{ fontSize: 13 }}><ExternalLink size={14} />Открыть пост в канале</a>}
            </div>
          </div>
          <table style={{ fontSize: 13, width: '100%' }}>
            <thead><tr><th /><th style={{ textAlign: 'left' }}>Это объявление</th><th style={{ textAlign: 'left' }}>В канале</th></tr></thead>
            <tbody>
              {([
                ['Цена', money(listing.price, 'USD'), money(s.price_usd ?? null, 'USD')],
                ['Спальни', bedroomsLabel(listing.bedrooms) || '—', bedroomsLabel(s.bedrooms) || '—'],
                ['Площадь', listing.area ? `${listing.area} м²` : '—', s.area ? `${s.area} м²` : '—'],
                ['Этаж', listing.floor ?? '—', s.floor ?? '—'],
              ] as [string, ReactNode, ReactNode][]).map(([k, a, b]) => (
                <tr key={k}><td className="muted">{k}</td><td>{a}</td><td style={{ fontWeight: String(a) === String(b) ? 400 : 700 }}>{b}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </div>
  );
}

export function ListingModal({ id, onClose }: { id: string; onClose: () => void }) {
  const { data, isLoading, error } = useQuery({ queryKey: ['listing', id], queryFn: () => api.listings.get(id) });
  const l = data as (Listing & { urls?: string[]; dup_sources?: string[]; phones?: string[]; telegram_contacts?: string[]; floor?: number | null; description?: string | null; contacts_visible?: boolean; owner?: { name?: string | null; phone?: string | null; is_agent?: boolean } }) | undefined;
  return (
    <Modal open onClose={onClose} title={l?.address || 'Объявление'}>
      {isLoading ? <Loading /> : error ? <ErrorBox error={error} /> : l && (
        <div className="col gap-lg">
          {!!l.photos?.length && (
            <div className="row" style={{ overflowX: 'auto', gap: 8 }}>
              {l.photos.map((p) => <img key={p} src={thumbUrl(p)} alt="" loading="lazy" style={{ height: 180, borderRadius: 10, flex: 'none' }} />)}
            </div>
          )}
          <div className="row row-wrap gap-sm"><AiFlagBadges flags={l.ai_flags} /></div>
          <KV items={[
            ['Цена', money(l.price, l.currency || 'USD')], ['Район', l.district || '—'], ['Спальни', bedroomsLabel(l.bedrooms) || '—'],
            ['Площадь', l.area ? `${l.area} м²` : '—'], ['Этаж', l.floor ?? '—'],
          ]} />
          {l.owner && (
            <div className="col gap-sm">
              <span className="label">{l.owner.is_agent ? 'Контакт агента' : 'Собственник'}{l.owner.name ? ` · ${l.owner.name}` : ''}</span>
              {l.owner.phone && <div className="row gap-sm"><a className="btn btn-secondary grow" href={`tel:${l.owner.phone.replace(/[^+\d]/g, '')}`}>Позвонить · {l.owner.phone}</a><CopyPhone phone={l.owner.phone} /></div>}
            </div>
          )}
          {!!l.phones?.length && (
            <div className="col gap-sm">
              <span className="label">Контакты из объявления</span>
              {l.phones.map((phone) => <div key={phone} className="row gap-sm"><a className="btn btn-secondary grow" href={`tel:${phone.replace(/[^+\d]/g, '')}`}>Позвонить · {phone}</a><CopyPhone phone={phone} /></div>)}
            </div>
          )}
          {!!l.telegram_contacts?.length && (
            <div className="col gap-sm">
              <span className="label">Telegram из объявления</span>
              {l.telegram_contacts.map((username) => <a key={username} className="btn btn-secondary" href={`https://t.me/${username}`} target="_blank" rel="noreferrer">Написать @{username}</a>)}
            </div>
          )}
          {l.contacts_visible && !l.owner?.phone && !l.phones?.length && !l.telegram_contacts?.length && (
            <div className="alert alert-info">Телефон не указан. {l.urls?.length ? 'Откройте источник ниже, чтобы связаться с автором объявления.' : 'Уточните контакт у оператора.'}</div>
          )}
          {l.contacts_visible === false && <div className="alert alert-info">Контакты доступны ответственному агенту и оператору.</div>}
          {l.ai_flags?.duplicate && (
            <div className="alert alert-warn">Дубль: эта квартира уже опубликована в канале агентства. Ссылка на пост — в списке ниже.</div>
          )}
          {!l.ai_flags?.duplicate && !!l.similar?.length && <SimilarInChannel listing={l} similar={l.similar} />}
          {!!l.urls?.length && (
            <div className="col gap-sm">
              <span className="label">{l.urls.length > 1 ? `Где опубликовано · ${l.urls.length}` : 'Источник'}</span>
              {l.urls.map((u) => <a key={u} href={u} target="_blank" rel="noreferrer" className="row gap-sm ellipsis"><ExternalLink size={14} />{u}</a>)}
            </div>
          )}
          {l.ai_flags?.reasons?.length ? <div className="muted" style={{ fontSize: 13 }}>{l.ai_flags.reasons.join(' · ')}</div> : null}
          {(l.description || l.text) && <div style={{ whiteSpace: 'pre-wrap', fontSize: 14 }}>{l.description || l.text}</div>}
        </div>
      )}
    </Modal>
  );
}

/** A clickable badge that says what it is about: «Похож на #12 · Абашидзе 17» (ellipsized); tap opens the comparison. */
export function ChipButton({ tone, onClick, children }: { tone: 'ok' | 'redsoft' | 'warn' | 'grey'; onClick: () => void; children: ReactNode }) {
  return <button type="button" className={`badge badge-${tone} badge-btn`} onClick={onClick}><span className="ellipsis">{children}</span></button>;
}

/** One column of a comparison: photos (previews), address, params, a link. */
export function CompareSide({ label, photos, address, rows, link, children }: {
  label: string; photos: (string | null | undefined)[]; address?: string | null; rows: [string, ReactNode][];
  link?: { href?: string | null; text: string }; children?: ReactNode;
}) {
  const shots = photos.filter(Boolean) as string[];
  return (
    <div className="cmp-side">
      <span className="label">{label}</span>
      {shots.length ? <div className="cmp-photos">{shots.map((p) => <img key={p} src={thumbUrl(p)} alt="" loading="lazy" />)}</div>
        : <div className="faint" style={{ fontSize: 12 }}>Без фото</div>}
      <strong style={{ fontSize: 15 }}>{address || 'Адрес не указан'}</strong>
      <KV items={rows} />
      {children}
      {link?.href && <a href={link.href} target="_blank" rel="noreferrer" className="row gap-sm" style={{ fontSize: 13 }}><ExternalLink size={14} />{link.text}</a>}
    </div>
  );
}

export const listingRows = (l: Listing & { floor?: number | null }): [string, ReactNode][] => [
  ['Цена', money(l.price, l.currency || 'USD')], ['Район', l.district || '—'], ['Спальни', bedroomsLabel(l.bedrooms) || '—'],
  ['Площадь', l.area ? `${l.area} м²` : '—'], ['Этаж', l.floor ?? '—'],
];

/** The ad's column; the source link comes from the full listing (the feed hides it until you may see contacts). */
export function ListingSide({ l }: { l: Listing & { floor?: number | null } }) {
  const { data } = useQuery({ queryKey: ['listing', l.id], queryFn: () => api.listings.get(l.id) });
  const href = l.url || (data as { urls?: string[] } | undefined)?.urls?.[0];
  return (
    <CompareSide label={`Это объявление${l.source ? ` · ${SOURCE_LABEL[l.source] || l.source}` : ''}`} photos={l.photos || []} address={l.address}
      rows={listingRows(l)} link={{ href, text: 'Открыть источник' }} />
  );
}

/** «Похож в канале»: the ad next to the agency channel post(s) it resembles. */
function ChannelCompare({ listing, onClose }: { listing: Listing & { floor?: number | null }; onClose: () => void }) {
  const similar = listing.similar || [];
  return (
    <Modal open wide onClose={onClose} title="Похожая квартира в канале">
      <div className="col gap-lg">
        <div className="alert alert-warn" style={{ fontSize: 13 }}>Похожая квартира уже есть в канале агентства. Сравните фото и параметры: это одна квартира или разные в одном доме?</div>
        <div className="cmp">
          <ListingSide l={listing} />
          <div className="col gap-sm" style={{ minWidth: 0 }}>
            {similar.map((s) => (
              <CompareSide key={s.id} label={`Пост в канале · ${s.distance_m ? `в ${s.distance_m} м` : 'тот же адрес'}`} photos={[s.photo]} address={s.address}
                rows={[['Цена', money(s.price_usd ?? null, 'USD')], ['Спальни', bedroomsLabel(s.bedrooms) || '—'], ['Площадь', s.area ? `${s.area} м²` : '—'], ['Этаж', s.floor ?? '—']]}
                link={{ href: s.url, text: 'Открыть пост в канале' }} />
            ))}
          </div>
        </div>
      </div>
    </Modal>
  );
}

/** chips: one wrapping row of badges under the price (matches, known object); flag reasons live in the badges' tooltips. */
export function ListingCard({ listing, locked, footer, chips, onOpen }: { listing: Listing; locked?: ReactNode; footer?: ReactNode; chips?: ReactNode; onOpen?: () => void }) {
  const ph = listing.photos?.[0];
  const [open, setOpen] = useState(false);
  const [cmp, setCmp] = useState(false);
  const show = onOpen || (() => setOpen(true));
  const similar = !listing.ai_flags?.duplicate ? listing.similar || [] : [];
  return (
    <article className={`card lcard ${locked ? 'locked' : ''}`}>
      {open && <ListingModal id={listing.id} onClose={() => setOpen(false)} />}
      {cmp && <ChannelCompare listing={listing} onClose={() => setCmp(false)} />}
      <div className="lcard-photo" onClick={show} style={{ cursor: 'pointer' }}>
        {ph ? <img src={thumbUrl(ph)} alt="" loading="lazy" /> : null}
        <div className="badges"><AiFlagBadges flags={listing.ai_flags} /></div>
        {listing.address && <span className="plaque plaque-pos" style={{ position: 'absolute', left: 10, bottom: 10 }}>{listing.address}</span>}
      </div>
      <div className="lcard-body">
        <div className="row row-between">
          <span className="label">{[listing.district, bedroomsLabel(listing.bedrooms), listing.area ? `${listing.area} м²` : null].filter(Boolean).join(' · ')}</span>
        </div>
        <div className="price" onClick={show} style={{ cursor: 'pointer' }}>{money(listing.price, listing.currency || 'USD')}<span className="muted" style={{ fontSize: 12, fontWeight: 600 }}>/мес</span></div>
        <div className="row row-wrap gap-sm">
          {listing.source && <Badge tone="grey">{SOURCE_LABEL[listing.source] || listing.source}</Badge>}
          {listing.created_at && <span className="faint" style={{ fontSize: 12 }}>{fmtDateTime(listing.created_at)}</span>}
        </div>
        {(chips || !!similar.length) && (
          <div className="row row-wrap gap-sm" style={{ minWidth: 0 }}>
            {!!similar.length && (
              <ChipButton tone="redsoft" onClick={() => setCmp(true)}>Похож в канале{similar[0].address ? ` · ${similar[0].address}` : ''}</ChipButton>
            )}
            {chips}
          </div>
        )}
        {listing.refused && (
          <div className="alert alert-info" style={{ fontSize: 12 }}>
            Звонил {listing.refused.by || 'агент'} {fmtDateTime(listing.refused.at)}: собственник не хочет работать{listing.refused.comment ? ` («${listing.refused.comment}»)` : ''}. Попробуйте вы.
          </div>
        )}
        {locked && <div className="row gap-sm muted" style={{ fontSize: 12, fontWeight: 600 }}><Lock size={14} />{locked}</div>}
        <div style={{ marginTop: 'auto' }}>{footer}</div>
      </div>
    </article>
  );
}

/** Latest first; long histories show the latest `limit` and «Показать ещё». */
export function Timeline({ events, limit = 5 }: { events: EventItem[]; limit?: number }) {
  const [all, setAll] = useState(false);
  if (!events.length) return <div className="muted">Событий пока нет</div>;
  const sorted = [...events].sort((a, b) => new Date(b.at).getTime() - new Date(a.at).getTime());
  const shown = all ? sorted : sorted.slice(0, limit);
  return (
    <div className="timeline">
      {shown.map((e) => {
        const isBot = e.actor_label === 'bot';
        const who = e.actor_name || (e.actor_label === 'bot' ? 'Бот' : e.actor_label === 'client' ? 'Клиент' : e.actor_label) || '';
        return (
          <div className="tl-item" key={e.id}>
            <span className={`tl-dot ${isBot ? 'bot' : ''}`} />
            <div>
              <div className="tl-text">{e.text || eventText(e)}</div>
              <div className="tl-meta row gap-sm">
                {isBot ? <Bot size={12} /> : who ? <UserIcon size={12} /> : null}
                {who && <span>{who}</span>}
                <span>{fmtDateTime(e.at)}</span>
              </div>
            </div>
          </div>
        );
      })}
      {sorted.length > shown.length && <Button size="sm" variant="secondary" style={{ alignSelf: 'flex-start', marginTop: 8 }} onClick={() => setAll(true)}>Показать ещё ({sorted.length - shown.length})</Button>}
    </div>
  );
}

const ACTION_TEXT: Record<string, string> = {
  created: 'Заявка создана', operator_taken: 'Оператор взял заявку', agent_assigned: 'Назначен агент', slots_sent: 'Время отправлено клиенту',
  time_chosen: 'Клиент выбрал время', contacts_shared: 'Бот передал контакты агенту', viewing_done: 'Просмотр прошёл',
  agent_outcome: 'Итог агента', client_outcome: 'Ответ клиента', deal_confirmed: 'Сделка подтверждена', cancelled: 'Отменено',
  // backend lead event names (services/leads.py _event): outcome_<by>, deal_*, rented, reschedule ...
  outcome_agent: 'Итог агента', outcome_client: 'Ответ клиента', deal_created: 'Заполнена форма сделки', deal_cancelled: 'Сделка отменена',
  rented: 'Сделка подтверждена · сдано', reopened: 'Заявка снова открыта', reschedule: 'Перенос просмотра', client_late: 'Клиент опаздывает',
  client_own_time: 'Клиент предложил своё время', message_to_client: 'Оператор написал клиенту в личку', message_from_client: 'Клиент написал оператору в личку', slots_not_delivered: 'Время не отправлено клиенту: userbot оператора', client_card_not_delivered: 'Не отправлено клиенту: userbot оператора', client_confirmed: 'Клиент подтвердил просмотр', client_comment: 'Комментарий клиента', sla_overdue: 'Срок шага истёк',
  taken: 'Беру в работу', call: 'Звонок', call_reached: 'Дозвонился', call_failed: 'Не дозвонился', updated: 'Поля объекта изменены', dismissed: 'Неактуально', owner_refused: 'Собственник не хочет работать · объект вернулся в ленту', owner_thinking: 'Собственник думает', owner_rented_self: 'Собственник сдал сам', owner_confirmed: 'Собственник подтвердил', agent_marked: 'Ответил агент',
  shoot_scheduled: 'Съёмка назначена', media_uploaded: 'Загружены фото', submitted: 'Отправлено на проверку', review: 'На проверке',
  approved: 'Одобрено', returned: 'Возвращено агенту', published: 'Опубликовано в канале', archived: 'В архив', status: 'Смена статуса',
  owner_transferred: 'Собственник передан агенту', owner_agent_override: 'Оператор привязал собственника другого агента',
  change_requested: 'Запрос оператору', change_request_approved: 'Запрос одобрен', change_request_rejected: 'Запрос отклонён',
  rent_cancelled: 'Сделка отменена · снова на рынке',
  media_added: 'Добавлены фото / видео', media_deleted: 'Файл удалён', checklist: 'Чек-лист комнат', verified: 'Объект проверен оператором',
  from_deal: 'Создан из сделки', fast_track: 'Собственник согласен · объект показан клиентам',
  agent_bound: 'Собственник закреплён за агентом', agent_released: 'Собственник освобождён',
  listing_linked: 'Найдено объявление этого объекта', exclusive_conflict: 'Эксклюзив висит на другом сайте', ad_removed_checked: 'Проверено: объявление снято',
};
const AD_EVENTS = ['listing_linked', 'exclusive_conflict', 'ad_removed_checked'];
function eventText(e: EventItem): string {
  const base = ACTION_TEXT[e.action] || e.action;
  const d = e.data || {};
  const outcome = d.outcome ? OUTCOME_LABEL[d.outcome as string] || (d.outcome as string) : '';
  const reason = d.reason ? ARCHIVE_REASON[d.reason as string] || REFUSAL_LABEL[d.reason as string] || (d.reason as string) : '';
  const kind = e.action.startsWith('change_request') && d.kind ? CR_KIND[d.kind as string] || (d.kind as string) : '';
  const source = AD_EVENTS.includes(e.action) && d.source ? SOURCE_LABEL[d.source as string] || (d.source as string) : '';
  const extra = (d.comment || d.note || d.to || [outcome, reason, source, kind].filter(Boolean).join(', ') || '') as string;
  return extra ? `${base}: ${extra}` : base;
}

export function DupCheck({ value }: { value?: string | null }) {
  if (!value) return null;
  return value === 'exists'
    ? <div className="alert alert-warn">⚠️ Уже есть в базе · собственник уже был в работе</div>
    : <div className="alert alert-ok">✅ Добавлено в базу</div>;
}

export function Place({ district, address }: { district?: string | null; address?: string | null }) {
  return <span className="row gap-sm muted" style={{ fontSize: 12 }}><MapPin size={13} />{[district, address].filter(Boolean).join(', ') || '—'}</span>;
}
