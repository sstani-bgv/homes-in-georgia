// Domain widgets shared by desktop and mobile views.
import type { ReactNode } from 'react';
import { Bot, Clock, Lock, MapPin, User as UserIcon } from 'lucide-react';
import type { AiFlags, EventItem, Lead, LeadStatus, Listing, PropertyStatus } from '../api/types';
import { mediaUrl } from '../api/client';
import { Badge, useNow } from './ui';
import { LEAD_STATUS, PROPERTY_STATUS, bedroomsLabel, fmtDateTime, money, SOURCE_LABEL } from '../lib/format';

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

export function ListingCard({ listing, locked, footer, onOpen }: { listing: Listing; locked?: ReactNode; footer?: ReactNode; onOpen?: () => void }) {
  const ph = listing.photos?.[0];
  return (
    <article className={`card lcard ${locked ? 'locked' : ''}`}>
      <div className="lcard-photo" onClick={onOpen} style={{ cursor: onOpen ? 'pointer' : undefined }}>
        {ph ? <img src={mediaUrl(ph)} alt="" loading="lazy" /> : null}
        <div className="badges"><AiFlagBadges flags={listing.ai_flags} /></div>
        {listing.address && <span className="plaque plaque-pos" style={{ position: 'absolute', left: 10, bottom: 10 }}>{listing.address}</span>}
      </div>
      <div className="lcard-body">
        <div className="row row-between">
          <span className="label">{[listing.district, bedroomsLabel(listing.bedrooms), listing.area ? `${listing.area} м²` : null].filter(Boolean).join(' · ')}</span>
        </div>
        <div className="price">{money(listing.price, listing.currency || 'USD')}<span className="muted" style={{ fontSize: 12, fontWeight: 600 }}>/мес</span></div>
        <div className="row row-wrap gap-sm">
          {listing.source && <Badge tone="grey">{SOURCE_LABEL[listing.source] || listing.source}</Badge>}
          {listing.created_at && <span className="faint" style={{ fontSize: 12 }}>{fmtDateTime(listing.created_at)}</span>}
        </div>
        {listing.ai_flags?.reasons?.length ? <div className="muted" style={{ fontSize: 12 }}>{listing.ai_flags.reasons.join(' · ')}</div> : null}
        {locked && <div className="row gap-sm muted" style={{ fontSize: 12, fontWeight: 600 }}><Lock size={14} />{locked}</div>}
        <div style={{ marginTop: 'auto' }}>{footer}</div>
      </div>
    </article>
  );
}

export function Timeline({ events }: { events: EventItem[] }) {
  if (!events.length) return <div className="muted">Событий пока нет</div>;
  const sorted = [...events].sort((a, b) => new Date(b.at).getTime() - new Date(a.at).getTime());
  return (
    <div className="timeline">
      {sorted.map((e) => {
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
    </div>
  );
}

const ACTION_TEXT: Record<string, string> = {
  created: 'Заявка создана', operator_taken: 'Оператор взял заявку', agent_assigned: 'Назначен агент', slots_sent: 'Время отправлено клиенту',
  time_chosen: 'Клиент выбрал время', contacts_shared: 'Бот передал контакты агенту', viewing_done: 'Просмотр прошёл',
  agent_outcome: 'Итог агента', client_outcome: 'Ответ клиента', deal_confirmed: 'Сделка подтверждена', cancelled: 'Отменено',
  taken: 'Беру в работу', call: 'Звонок', call_failed: 'Не дозвонился', owner_confirmed: 'Собственник подтвердил', agent_marked: 'Ответил агент',
  shoot_scheduled: 'Съёмка назначена', media_uploaded: 'Загружены фото', submitted: 'Отправлено на проверку', review: 'На проверке',
  approved: 'Одобрено', returned: 'Возвращено агенту', published: 'Опубликовано в канале', archived: 'В архив', status: 'Смена статуса',
};
function eventText(e: EventItem): string {
  const base = ACTION_TEXT[e.action] || e.action;
  const d = e.data || {};
  const extra = (d.comment || d.note || d.to || d.outcome || '') as string;
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
