// Domain widgets shared by desktop and mobile views.
import { useState } from 'react';
import type { ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link, useLocation, useSearchParams } from 'react-router-dom';
import { Bot, Clock, Copy, ExternalLink, Lock, MapPin, PhoneOff, User as UserIcon } from 'lucide-react';
import { api } from '../api';
import type { AiFlags, EventItem, Lead, LeadStatus, Listing, PhoneRow, Property, PropertyStatus, SimilarFlat } from '../api/types';
import { thumbUrl } from '../api/client';
import { Badge, Button, ErrorBox, KV, Lightbox, Loading, Modal, useNow } from './ui';
import { useToast } from './toast';
import { SimilarList } from './Similar';
import { lx, t, tmap } from '../lib/i18n';
import { ARCHIVE_REASON, CR_KIND, LEAD_STATUS, OUTCOME_LABEL, PROPERTY_STATUS, REFUSAL_LABEL, bedroomsLabel, districtLabel, fmtDateTime, formatPhone, money, pluralForm, SOURCE_LABEL } from '../lib/format';

/** «Скопировать» next to a phone: tel: links do nothing on desktop / Telegram Desktop. */
export function CopyPhone({ phone }: { phone: string }) {
  const toast = useToast();
  const copy = () => (navigator.clipboard ? navigator.clipboard.writeText(formatPhone(phone)) : Promise.reject())
    .then(() => toast(t('Скопировано: {p}', { p: formatPhone(phone) })), () => toast(t('Не удалось скопировать, выделите номер вручную'), true));
  return <Button size="sm" variant="secondary" onClick={(e) => { e.preventDefault(); e.stopPropagation(); copy(); }}><Copy size={14} />{t('Скопировать')}</Button>;
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
    <span className={`sla sla-${tone}`} title={t('Срок шага')}>
      <Clock size={11} strokeWidth={3} />
      {left < 0 ? `−${fmtDur(left)}` : fmtDur(left)}
    </span>
  );
}

export function LeadStatusBadge({ status }: { status: LeadStatus }) {
  const tone = status === 'rented' ? 'ok' : status === 'refused' ? 'grey' : status === 'new' ? 'redsoft' : status === 'deal_pending' || status === 'contract' ? 'warn' : 'blue';
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
      {flags.owner && <Badge tone="ok" title={reasons}>{t('собственник')}</Badge>}
      {flags.agent_suspect && <Badge tone="warn" title={reasons}>{t('похоже на агента')}</Badge>}
      {flags.duplicate && <Badge tone="red" title={reasons}>{t('дубль')}</Badge>}
    </>
  );
}

/** Red «Эксклюзив висит на ss.ge»: the exclusive object still has live ads on other sites (stage 4). */
export function ExclusiveConflictBadge({ p }: { p: Pick<Property, 'exclusive_conflict'> }) {
  const ads = p.exclusive_conflict || [];
  if (!ads.length) return null;
  const where = [...new Set(ads.map((a) => SOURCE_LABEL[a.source || ''] || a.source || t('другом сайте')))].join(', ');
  return <Badge tone="red" title={t('Попросите собственника снять объявление')}>{t('Эксклюзив висит на {where}', { where })}</Badge>;
}

/** Full listing: photos, text, all source links. A merged duplicate keeps every copy's link in `urls`. */
/** Photos in a row; a tap opens them full-screen right here, the page / modal underneath stays as it is (11-13). */
export function PhotoStrip({ photos }: { photos: (string | null | undefined)[] }) {
  const shots = photos.filter(Boolean) as string[];
  const [open, setOpen] = useState<number | null>(null);
  if (!shots.length) return <div className="faint" style={{ fontSize: 12 }}>{t('Без фото')}</div>;
  return (
    <>
      {shots.length > 2 && <div className="faint" style={{ fontSize: 12 }}>{t('{n} фото · листайте вбок', { n: shots.length })}</div>}
      <div className="cmp-photos">{shots.map((p, i) => <img key={p} src={thumbUrl(p)} alt="" loading="lazy" style={{ cursor: 'zoom-in' }} onClick={() => setOpen(i)} />)}</div>
      {open !== null && <Lightbox items={shots.map((src) => ({ src }))} index={open} onIndex={setOpen} onClose={() => setOpen(null)} />}
    </>
  );
}

/** Our object from a comparison: a new tab on the desktop, so the ad and the comparison stay open; in the Mini App
 * (no tabs) in-app navigation, back returns to the same spot. */
function ObjectLink({ to, text }: { to: string; text: string }) {
  const tg = document.documentElement.classList.contains('tg');
  return tg ? <Link to={to} className="row gap-sm" style={{ fontSize: 13 }}><ExternalLink size={14} />{text}</Link>
    : <a href={to} target="_blank" rel="noreferrer" className="row gap-sm" style={{ fontSize: 13 }}><ExternalLink size={14} />{text}</a>;
}

/** actions: the feed card's buttons (Беру в работу / Агент / Неактуально, «Уже в базе» / «Похож на»), so the ad is
 * decided right from its window (11-13); they stay at the bottom while the window scrolls. */
export function ListingModal({ id, onClose, actions }: { id: string; onClose: () => void; actions?: ReactNode }) {
  const { data, isLoading, error } = useQuery({ queryKey: ['listing', id], queryFn: () => api.listings.get(id) });
  const loc = useLocation();
  const l = data as (Listing & { property?: { id: number; status?: string } | null; urls?: string[]; dup_sources?: string[]; phones?: string[]; telegram_contacts?: string[]; floor?: number | null; description?: string | null; contacts_visible?: boolean; owner?: { name?: string | null; phone?: string | null; phones?: PhoneRow[] | null; is_agent?: boolean } }) | undefined;
  return (
    <Modal open onClose={onClose} title={l?.address || t('Объявление')}>
      {isLoading ? <Loading /> : error ? <ErrorBox error={error} /> : l && (
        <div className="col gap-lg">
          {l.property && (
            <Link className="btn btn-secondary" to={`${loc.pathname.startsWith('/m/') ? '/m/properties' : '/objects'}/${l.property.id}`} onClick={onClose}>
              <ExternalLink size={16} />{t('Открыть объект #{id}', { id: l.property.id })}
            </Link>
          )}
          {!!l.photos?.length && (
            <div className="col gap-sm">
              <PhotoStrip photos={l.photos} />
            </div>
          )}
          <DontCallNote note={l.refused} />
          <div className="row row-wrap gap-sm"><AiFlagBadges flags={l.ai_flags} /></div>
          <KV items={[
            [t('Цена'), money(l.price, l.currency || 'USD')], [t('Район'), l.district || '—'], [t('Спальни'), bedroomsLabel(l.bedrooms) || '—'],
            [t('Площадь'), l.area ? `${l.area} ${t('м²')}` : '—'], [t('Этаж'), l.floor ?? '—'],
          ]} />
          {l.owner && (
            <div className="col gap-sm">
              <span className="label">{l.owner.is_agent ? t('Контакт агента') : t('Собственник')}{l.owner.name ? ` · ${l.owner.name}` : ''}</span>
              {/* every number of the owner, the main first (David 11-18: the ad's and the one given on the call) */}
              {(l.owner.phones?.length ? l.owner.phones : l.owner.phone ? [{ phone: l.owner.phone }] : []).map((r: PhoneRow) => (
                <div key={r.phone} className="row gap-sm"><a className="btn btn-secondary grow" style={{ minWidth: 0, whiteSpace: 'normal', flexWrap: 'wrap', rowGap: 0, height: 'auto', minHeight: 40, paddingBlock: 6 }} href={`tel:${r.phone.replace(/[^+\d]/g, '')}`}>
                  <span>{t('Позвонить')} · <span style={{ whiteSpace: 'nowrap' }}>{formatPhone(r.phone)}</span></span>{r.note && <span className="muted" style={{ fontSize: 12, fontWeight: 500 }}>{r.note}</span>}</a><CopyPhone phone={r.phone} /></div>
              ))}
            </div>
          )}
          {!!l.phones?.length && (
            <div className="col gap-sm">
              <span className="label">{t('Контакты из объявления')}</span>
              {l.phones.map((phone) => <div key={phone} className="row gap-sm"><a className="btn btn-secondary grow" href={`tel:${phone.replace(/[^+\d]/g, '')}`}>{t('Позвонить')} · {formatPhone(phone)}</a><CopyPhone phone={phone} /></div>)}
            </div>
          )}
          {!!l.telegram_contacts?.length && (
            <div className="col gap-sm">
              <span className="label">{t('Telegram из объявления')}</span>
              {l.telegram_contacts.map((username) => <a key={username} className="btn btn-secondary" href={`https://t.me/${username}`} target="_blank" rel="noreferrer">{t('Написать')} @{username}</a>)}
            </div>
          )}
          {l.contacts_visible && !l.owner?.phone && !l.phones?.length && !l.telegram_contacts?.length && (
            <div className="alert alert-info">{t('Телефон не указан.')} {l.urls?.length ? t('Откройте источник ниже, чтобы связаться с автором объявления.') : t('Уточните контакт у оператора.')}</div>
          )}
          {l.contacts_visible === false && <div className="alert alert-info">{t('Контакты доступны ответственному агенту и оператору.')}</div>}
          {l.ai_flags?.duplicate && (
            <div className="alert alert-warn">{t('Дубль: эта квартира уже опубликована в канале агентства. Ссылка на пост — в списке ниже.')}</div>
          )}
          <div className="col gap-sm">
            <span className="label">{t('Похожие объекты')}</span>
            <SimilarList subject={`l:${l.id}`} emptyText={t('Похожих объектов не найдено: ни в базе, ни на сайтах, ни у конкурентов.')} />
          </div>
          {!!l.urls?.length && (
            <div className="col gap-sm">
              <span className="label">{l.urls.length > 1 ? t('Где опубликовано · {n}', { n: l.urls.length }) : t('Источник')}</span>
              {l.urls.map((u) => <a key={u} href={u} target="_blank" rel="noreferrer" className="row gap-sm ellipsis"><ExternalLink size={14} />{u}</a>)}
            </div>
          )}
          {l.ai_flags?.reasons?.length ? <div className="muted" style={{ fontSize: 13 }}>{l.ai_flags.reasons.join(' · ')}</div> : null}
          {(l.description || l.text) && <div style={{ whiteSpace: 'pre-wrap', fontSize: 14 }}>{l.description || l.text}</div>}
          {actions && <div className="modal-actions">{actions}</div>}
        </div>
      )}
    </Modal>
  );
}

/** «18.11 14:05 Давид звонил — собственник отказался: не хочет работать» on every ad of a flat whose owner said no
 * (refusals_for: «Не хочет работать», «Сдал сам»), so the next agent doesn't call him again (David 11-18). */
export function DontCallNote({ note }: { note?: Listing['refused'] }) {
  if (!note) return null;
  const p = { at: fmtDateTime(note.at), who: note.by || t('агент'), c: note.comment ? `: ${note.comment}` : '' };
  return (
    <div className="alert alert-warn" style={{ fontSize: 13 }}>
      <PhoneOff size={16} style={{ flex: 'none', marginTop: 1 }} />
      <span>{note.reason === 'rented_by_owner' ? t('{at} {who} звонил — собственник сдал сам{c}', p) : t('{at} {who} звонил — собственник отказался{c}', p)}</span>
    </div>
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
  return (
    <div className="cmp-side">
      <span className="label">{label}</span>
      <PhotoStrip photos={photos} />
      <strong style={{ fontSize: 15 }}>{address || t('Адрес не указан')}</strong>
      <KV items={rows} />
      {children}
      {/* our own object: in-app navigation (back returns here, and the Mini App stays the Mini App); a source site: new tab */}
      {link?.href && (link.href.startsWith('/') && !link.href.startsWith('/api/')
        ? <ObjectLink to={link.href} text={link.text} />
        : <a href={link.href} target="_blank" rel="noreferrer" className="row gap-sm" style={{ fontSize: 13 }}><ExternalLink size={14} />{link.text}</a>)}
    </div>
  );
}

export const listingRows = (l: Listing & { floor?: number | null }): [string, ReactNode][] => [
  [t('Цена'), money(l.price, l.currency || 'USD')], [t('Район'), l.district || '—'], [t('Спальни'), bedroomsLabel(l.bedrooms) || '—'],
  [t('Площадь'), l.area ? `${l.area} ${t('м²')}` : '—'], [t('Этаж'), l.floor ?? '—'],
];

/** The ad's column; the source link comes from the full listing (the feed hides it until you may see contacts). */
export function ListingSide({ l }: { l: Listing & { floor?: number | null } }) {
  const { data } = useQuery({ queryKey: ['listing', l.id], queryFn: () => api.listings.get(l.id) });
  const href = l.url || (data as { urls?: string[] } | undefined)?.urls?.[0];
  return (
    <CompareSide label={`${t('Это объявление')}${l.source ? ` · ${SOURCE_LABEL[l.source] || l.source}` : ''}`} photos={l.photos || []} address={l.address}
      rows={listingRows(l)} link={{ href, text: t('Открыть источник') }} />
  );
}

/** «В архиве по этому адресу: 3 объекта» / «В базе по этому адресу: 2 объекта (в архиве 1)» */
export function baseAtAddress(similar: SimilarFlat[]): string {
  const n = similar.length, arch = similar.filter((s) => s.status === 'archived').length;
  const objs = (k: number) => lx(`${k} ${['объект', 'объекта', 'объектов'][pluralForm(k)]}`, `${k} ობიექტი`, `${k} ${k === 1 ? 'property' : 'properties'}`);
  return arch === n ? t('В архиве по этому адресу: {x}', { x: objs(n) }) : `${t('В базе по этому адресу: {x}', { x: objs(n) })}${arch ? t(' (в архиве {n})', { n: arch }) : ''}`;
}

/** «Есть в базе»: the ad next to the objects of our base at its address. */
function ChannelCompare({ listing, onClose }: { listing: Listing & { floor?: number | null }; onClose: () => void }) {
  const similar = listing.similar || [];
  return (
    <Modal open wide onClose={onClose} title={baseAtAddress(similar)}>
      <div className="col gap-lg">
        <div className="alert alert-warn" style={{ fontSize: 13 }}>{t('В базе рядом есть квартиры с теми же спальнями и близкими этажом и площадью (±1 этаж, ±10%). Сравните фото (нажмите, чтобы открыть крупно): это одна из них? Бот мог ошибиться — решите сами.')}</div>
        <div className="cmp">
          <ListingSide l={listing} />
          <div className="col gap-sm" style={{ minWidth: 0 }}>
            {similar.map((s) => (
              <CompareSide key={s.id} label={`${t('Объект #{id}', { id: s.id })}${s.status ? ` · ${PROPERTY_STATUS[s.status as PropertyStatus] || s.status}` : ''}${s.archive_reason ? ` (${ARCHIVE_REASON[s.archive_reason] || s.archive_reason})` : ''} ${s.reasons?.length ? ` · ${s.reasons.join(' · ')}` : ''}`} photos={s.photos?.length ? s.photos : [s.photo]} address={s.address}
                rows={[[t('Цена'), money(s.price_usd ?? null, 'USD')], [t('Спальни'), bedroomsLabel(s.bedrooms) || '—'], [t('Площадь'), s.area ? `${s.area} ${t('м²')}` : '—'], [t('Этаж'), s.floor ?? '—']]}
                link={{ href: s.url, text: t('Открыть объект') }} />
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
  // the open ad / comparison lives in the URL: «Открыть объект» / a source site and back reopens it on the same spot (David 11-12)
  const [params, setParams] = useSearchParams();
  const modal = (k: 'ad' | 'cmp', on: boolean) => setParams((p) => {
    const n = new URLSearchParams(p);
    if (on) n.set(k, listing.id); else n.delete(k);
    return n;
  }, { replace: true });
  const open = params.get('ad') === listing.id, cmp = params.get('cmp') === listing.id;
  const setOpen = (on: boolean) => modal('ad', on), setCmp = (on: boolean) => modal('cmp', on);
  const show = onOpen || (() => setOpen(true));
  const similar = !listing.ai_flags?.duplicate ? listing.similar || [] : [];
  return (
    <article className={`card lcard ${locked ? 'locked' : ''}`}>
      {open && <ListingModal id={listing.id} onClose={() => setOpen(false)} actions={(chips || footer) && <>{chips && <div className="row row-wrap gap-sm">{chips}</div>}{footer}</>} />}
      {cmp && <ChannelCompare listing={listing} onClose={() => setCmp(false)} />}
      <div className="lcard-photo" onClick={show} style={{ cursor: 'pointer' }}>
        {ph ? <img src={thumbUrl(ph)} alt="" loading="lazy" /> : null}
        <div className="badges"><AiFlagBadges flags={listing.ai_flags} /></div>
        {listing.address && <span className="plaque plaque-pos" style={{ position: 'absolute', left: 10, bottom: 10 }}>{listing.address}</span>}
      </div>
      <div className="lcard-body">
        <div className="row row-between">
          <span className="label">{[listing.district, bedroomsLabel(listing.bedrooms), listing.area ? `${listing.area} ${t('м²')}` : null].filter(Boolean).join(' · ')}</span>
        </div>
        <div className="price" onClick={show} style={{ cursor: 'pointer' }}>{money(listing.price, listing.currency || 'USD')}<span className="muted" style={{ fontSize: 12, fontWeight: 600 }}>{t('/мес')}</span></div>
        <div className="row row-wrap gap-sm">
          {listing.source && <Badge tone="grey">{SOURCE_LABEL[listing.source] || listing.source}</Badge>}
          {listing.created_at && <span className="faint" style={{ fontSize: 12 }}>{fmtDateTime(listing.created_at)}</span>}
        </div>
        {(chips || !!similar.length) && (
          <div className="row row-wrap gap-sm" style={{ minWidth: 0 }}>
            {!!similar.length && (
              <ChipButton tone="redsoft" onClick={() => setCmp(true)}>{baseAtAddress(similar)}</ChipButton>
            )}
            {chips}
          </div>
        )}
        <DontCallNote note={listing.refused} />
        {locked && <div className="row gap-sm muted" style={{ fontSize: 12, fontWeight: 600 }}><Lock size={14} />{locked}</div>}
        <div style={{ marginTop: 'auto' }}>{footer}</div>
      </div>
    </article>
  );
}

/** Latest first; long histories show the latest `limit` and «Показать ещё». */
export function Timeline({ events, limit = 5 }: { events: EventItem[]; limit?: number }) {
  const [all, setAll] = useState(false);
  if (!events.length) return <div className="muted">{t('Событий пока нет')}</div>;
  const sorted = [...events].sort((a, b) => new Date(b.at).getTime() - new Date(a.at).getTime());
  const shown = all ? sorted : sorted.slice(0, limit);
  return (
    <div className="timeline">
      {shown.map((e) => {
        const isBot = e.actor_label === 'bot';
        const who = e.actor_name || (e.actor_label === 'bot' ? t('Бот') : e.actor_label === 'client' ? t('Клиент') : e.actor_label) || '';
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
      {sorted.length > shown.length && <Button size="sm" variant="secondary" style={{ alignSelf: 'flex-start', marginTop: 8 }} onClick={() => setAll(true)}>{t('Показать ещё')} ({sorted.length - shown.length})</Button>}
    </div>
  );
}

const ACTION_TEXT: Record<string, string> = tmap({
  created: 'Заявка создана', operator_taken: 'Оператор взял заявку', agent_assigned: 'Назначен агент', slots_sent: 'Время отправлено клиенту',
  time_chosen: 'Клиент выбрал время', contacts_shared: 'Бот передал контакты агенту', viewing_done: 'Просмотр прошёл',
  agent_outcome: 'Итог агента', client_outcome: 'Ответ клиента', deal_confirmed: 'Сделка подтверждена', cancelled: 'Отменено',
  // backend lead event names (services/leads.py _event): outcome_<by>, deal_*, rented, reschedule ...
  outcome_agent: 'Итог агента', outcome_client: 'Ответ клиента', deal_created: 'Заполнена форма сделки', deal_cancelled: 'Сделка отменена',
  contract_started: 'Этап «Договор»: составляют договор', rented: 'Сделка подтверждена · сдано', reopened: 'Заявка снова открыта', reschedule: 'Перенос просмотра', client_late: 'Клиент опаздывает',
  client_own_time: 'Клиент предложил своё время', message_to_client: 'Оператор написал клиенту в личку', message_from_client: 'Клиент написал оператору в личку', slots_not_delivered: 'Время не отправлено клиенту: userbot оператора', client_card_not_delivered: 'Не отправлено клиенту: userbot оператора', client_confirmed: 'Клиент подтвердил просмотр', client_comment: 'Комментарий клиента', sla_overdue: 'Срок шага истёк', object_gone: 'Агент: квартира уже сдана',
  taken: 'Беру в работу', call: 'Звонок', call_reached: 'Дозвонился', call_failed: 'Не дозвонился', updated: 'Поля объекта изменены', dismissed: 'Неактуально', owner_refused: 'Собственник не хочет работать · объект вернулся в ленту', owner_thinking: 'Собственник думает', owner_rented_self: 'Собственник сдал сам', owner_confirmed: 'Собственник подтвердил', owner_contact: 'Контакт собственника изменён', agent_marked: 'Ответил агент',
  shoot_scheduled: 'Съёмка назначена', media_uploaded: 'Загружены фото', submitted: 'Отправлено на проверку', review: 'На проверке',
  approved: 'Одобрено', returned: 'Возвращено агенту', published: 'Опубликовано в канале', archived: 'В архив', status: 'Смена статуса',
  owner_transferred: 'Собственник передан агенту', owner_agent_override: 'Оператор привязал собственника другого агента',
  change_requested: 'Запрос оператору', change_request_approved: 'Запрос одобрен', change_request_rejected: 'Запрос отклонён',
  rent_cancelled: 'Сделка отменена · снова на рынке', resumed: 'Снят с паузы',
  idle_returned: 'Возвращён в «Новые объявления»: не было действий',
  tg_reposted: 'Пост в Telegram обновлён · старый удалён', tg_removed: 'Посты удалены из Telegram',
  duplicate_merged: 'Сюда перенесён удалённый дубль', deleted_as_duplicate: 'Удалён как дубль',
  media_added: 'Добавлены фото / видео', media_deleted: 'Файл удалён', media_reordered: 'Изменён порядок файлов', checklist: 'Чек-лист комнат', verified: 'Объект проверен оператором',
  from_deal: 'Создан из сделки', fast_track: 'Собственник согласен · объект показан клиентам',
  agent_bound: 'Собственник закреплён за агентом', agent_released: 'Собственник освобождён',
  listing_linked: 'Найдено объявление этого объекта', exclusive_conflict: 'Эксклюзив висит на другом сайте', ad_removed_checked: 'Проверено: объявление снято', ad_takedown_asked: 'Попросили снять объявление',
  duplicate_scan: 'Поиск дублей', exclusive_contract_waived: 'Договор без электронной подписи: подтвердил оператор',
  exclusive_dups_clear: 'Эксклюзив подтверждён: дублей нет', exclusive_dups_waiting: 'Ожидается устранение дублей (24 ч)',
  exclusive_dups_review: 'Сомнительные совпадения — ручная проверка', exclusive_dups_operator: 'Дубли остались — решение оператора',
  exclusive_contract_uploaded: 'Загружен эксклюзивный договор', exclusive_contract_signed: 'Собственник подписал эксклюзивный договор',
  // history from David's old work chats (backend/app/importers/legacy_objects.py)
  legacy_posted: 'Пост в канале (старая база)', legacy_updated: 'Пост обновлён (старая база)', legacy_discount: 'Скидка (старая база)',
  legacy_exclusive: 'Эксклюзив (старая база)', legacy_archived: 'В архив (старая база)',
  legacy_rented_by_owner: 'Собственник сдал сам (старая база)', legacy_rented_by_us: 'Сдали мы (старая база)',
});
const AD_EVENTS = ['listing_linked', 'exclusive_conflict', 'ad_removed_checked', 'ad_takedown_asked'];
function eventText(e: EventItem): string {
  const base = ACTION_TEXT[e.action] || e.action;
  const d = e.data || {};
  const outcome = d.outcome ? OUTCOME_LABEL[d.outcome as string] || (d.outcome as string) : '';
  const reason = d.reason ? ARCHIVE_REASON[d.reason as string] || REFUSAL_LABEL[d.reason as string] || (d.reason as string) : '';
  const kind = e.action.startsWith('change_request') && d.kind ? CR_KIND[d.kind as string] || (d.kind as string) : '';
  const source = AD_EVENTS.includes(e.action) && d.source ? SOURCE_LABEL[d.source as string] || (d.source as string) : '';
  const dups = e.action === 'duplicate_scan' ? t('найдено: {n}', { n: Number(d.n || 0) }) : '';
  const unposted = Array.isArray(d.failed) && d.failed.length ? t('не удалось удалить: {n}', { n: d.failed.length }) : '';
  const extra = (d.comment || d.note || d.to || [outcome, reason, source, kind, unposted, dups].filter(Boolean).join(', ') || '') as string;
  return extra ? `${base}: ${extra}` : base;
}

export function DupCheck({ value }: { value?: string | null }) {
  if (!value) return null;
  return value === 'exists'
    ? <div className="alert alert-warn">⚠️ {t('Уже есть в базе · собственник уже был в работе')}</div>
    : <div className="alert alert-ok">✅ {t('Добавлено в базу')}</div>;
}

export function Place({ district, address }: { district?: string | null; address?: string | null }) {
  return <span className="row gap-sm muted" style={{ fontSize: 12 }}><MapPin size={13} />{[districtLabel(district), address].filter(Boolean).join(', ') || '—'}</span>;
}
