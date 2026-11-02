// Lead page: B2–B6 (operator) and C6–C9 (agent). Used by /leads/:id and /m/leads/:id.
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CalendarClock, CheckCircle2, Hand, MessageSquare, Plus, RotateCcw, Send, Trash2, UserPlus, XCircle } from 'lucide-react';
import { api } from '../../api';
import { itemsOf, mediaUrl } from '../../api/client';
import type { ClientProfile, Lead } from '../../api/types';
import { useAuth } from '../../auth/AuthContext';
import { LeadStatusBadge, Place, SlaChip, Timeline } from '../../components/domain';
import { errText, useToast } from '../../components/toast';
import { Avatar, Badge, Button, Card, ErrorBox, Field, Input, KV, Loading, Modal, Seg, Textarea } from '../../components/ui';
import { OUTCOME_LABEL, REFUSAL_LABEL, bedroomsLabel, fmtDate, fmtDateTime, fmtSlot, localToIso, money } from '../../lib/format';

const PETS: Record<string, string> = { none: 'нет', cat: 'кошка', dog: 'собака', other: 'другое' };

export function ProfileView({ profile }: { profile: ClientProfile }) {
  const budget = profile.budget_max ? `${profile.budget_min ? `${money(profile.budget_min)}–` : 'до '}${money(profile.budget_max)}` : '—';
  return (
    <KV items={[
      ['Жильцов', profile.residents || '—'],
      ['Животные', (profile.pets || []).map((p) => PETS[p] || p).join(', ') || '—'],
      ['Срок, мес', (profile.terms || []).join(', ') || '—'],
      ['Бюджет', budget],
      ['Спальни', (profile.bedrooms || []).map((b) => (b === 0 ? 'студия' : b)).join(', ') || '—'],
      ['Районы', (profile.districts || []).map((d) => (d === 'any' ? 'любой' : d)).join(', ') || '—'],
      ['Точка', profile.pin ? <a href={`https://www.openstreetmap.org/?mlat=${profile.pin.lat}&mlon=${profile.pin.lng}#map=15/${profile.pin.lat}/${profile.pin.lng}`} target="_blank" rel="noreferrer">на карте{profile.radius_km ? ` · ${profile.radius_km} км` : ''}</a> : '—'],
      ['Заезд', profile.move_in ? fmtDate(profile.move_in) : '—'],
    ]} />
  );
}

function useLeadMut() {
  const qc = useQueryClient();
  const toast = useToast();
  const done = (l: Lead, msg?: string) => {
    qc.setQueryData(['lead', l.id], (old: Lead | undefined) => ({ ...(old || {}), ...l }));
    qc.invalidateQueries({ queryKey: ['lead', l.id] });
    qc.invalidateQueries({ queryKey: ['leads'] });
    if (msg) toast(msg);
  };
  return { done, toast, qc };
}

export function AssignModal({ lead, open, onClose }: { lead: Lead; open: boolean; onClose: () => void }) {
  const { done, toast } = useLeadMut();
  const { data, isLoading, error } = useQuery({ queryKey: ['recommend', lead.id], queryFn: () => api.leads.recommend(lead.id), enabled: open });
  const assign = useMutation({
    mutationFn: (agentId: number) => api.leads.assign(lead.id, agentId),
    onSuccess: (l) => { done(l, 'Заявка отправлена агенту'); onClose(); },
    onError: (e) => toast(errText(e), true),
  });
  const items = itemsOf(data);
  return (
    <Modal open={open} onClose={onClose} title="Назначить агента">
      {isLoading ? <Loading /> : error ? <ErrorBox error={error} /> : (
        <div>
          {items.map((r, i) => (
            <div key={r.agent.id} className="list-row" style={{ alignItems: 'flex-start' }}>
              <Avatar name={r.agent.name} src={r.agent.photo_url} />
              <div className="grow">
                <div className="row gap-sm row-wrap"><b>{r.agent.name}</b>{(r.recommended ?? i === 0) && <Badge tone="red">рекомендуем</Badge>}{r.agent.level ? <Badge>{r.agent.level}%</Badge> : null}</div>
                <div className="muted" style={{ fontSize: 12 }}>{(r.agent.districts || []).join(', ') || 'районы не указаны'}</div>
                <div className="row row-wrap gap-sm mt" style={{ marginTop: 6 }}>{r.reasons.map((x) => <Badge key={x} tone="grey">{x}</Badge>)}</div>
              </div>
              <Button size="sm" variant={i === 0 ? 'primary' : 'secondary'} loading={assign.isPending && assign.variables === r.agent.id} onClick={() => assign.mutate(r.agent.id)}>Назначить</Button>
            </div>
          ))}
        </div>
      )}
    </Modal>
  );
}

function pad(n: number) { return String(n).padStart(2, '0'); }
function dayStr(offset: number) { const d = new Date(Date.now() + offset * 86400_000); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`; }
const QUICK_TIMES = ['10:00', '12:00', '14:00', '16:00', '18:00', '19:30'];

/** C7: agent enters several free windows quickly. */
export function SlotsEntry({ lead }: { lead: Lead }) {
  const { done, toast } = useLeadMut();
  const [day, setDay] = useState(dayStr(1));
  const [slots, setSlots] = useState<string[]>([]);
  const [custom, setCustom] = useState('');
  const add = (v: string) => { if (v && !slots.includes(v)) setSlots((s) => [...s, v].sort()); };
  const send = useMutation({
    mutationFn: () => api.leads.slots(lead.id, slots.map(localToIso)),
    onSuccess: (l) => { done(l, 'Бот отправил варианты клиенту'); setSlots([]); },
    onError: (e) => toast(errText(e), true),
  });
  return (
    <div className="col">
      <div className="muted" style={{ fontSize: 13 }}>Узнайте у собственника свободное время и добавьте несколько окон. Бот сам отправит их клиенту.</div>
      <Seg value={day} onChange={setDay} options={[0, 1, 2, 3].map((o) => ({ value: dayStr(o), label: o === 0 ? 'Сегодня' : o === 1 ? 'Завтра' : fmtDate(dayStr(o)).slice(0, 5) }))} />
      <div className="row row-wrap gap-sm">
        {QUICK_TIMES.map((t) => {
          const v = `${day}T${t}`;
          const on = slots.includes(v);
          return <button key={t} type="button" className={`chip ${on ? 'active' : ''}`} onClick={() => (on ? setSlots(slots.filter((s) => s !== v)) : add(v))}>{t}</button>;
        })}
      </div>
      <div className="row" style={{ alignItems: 'flex-end' }}>
        <Field label="Своё время" className="grow"><Input type="datetime-local" value={custom} onChange={(e) => setCustom(e.target.value)} /></Field>
        <Button variant="secondary" disabled={!custom} onClick={() => { add(custom); setCustom(''); }}><Plus size={16} />Добавить</Button>
      </div>
      {slots.length > 0 && (
        <div className="row row-wrap gap-sm">
          {slots.map((s) => (
            <span key={s} className="slot-pill">{fmtSlot(s)}<button className="btn-ghost" style={{ border: 0, background: 'none', cursor: 'pointer', padding: 0, display: 'flex' }} onClick={() => setSlots(slots.filter((x) => x !== s))} aria-label="Убрать"><Trash2 size={13} /></button></span>
          ))}
        </div>
      )}
      <Button size="lg" disabled={!slots.length} loading={send.isPending} onClick={() => send.mutate()}><Send size={16} />Отправить клиенту · {slots.length}</Button>
    </div>
  );
}

function AgentOutcome({ lead }: { lead: Lead }) {
  const { done, toast } = useLeadMut();
  const [reason, setReason] = useState('price');
  const [pick, setPick] = useState<string | null>(null);
  const m = useMutation({
    mutationFn: (o: string) => api.leads.outcome(lead.id, o, o === 'refused' ? reason : undefined),
    onSuccess: (l) => done(l, 'Итог сохранён'),
    onError: (e) => toast(errText(e), true),
  });
  return (
    <div className="col">
      <div className="row row-wrap">
        <Button variant="ok" onClick={() => m.mutate('rented')} loading={m.isPending && m.variables === 'rented'}>Сдано</Button>
        <Button variant="secondary" onClick={() => setPick('refused')}>Не подошло</Button>
        <Button variant="secondary" onClick={() => m.mutate('thinking')} loading={m.isPending && m.variables === 'thinking'}>Думает</Button>
        <Button variant="ghost" onClick={() => m.mutate('reschedule')} loading={m.isPending && m.variables === 'reschedule'}><RotateCcw size={15} />Перенос</Button>
      </div>
      {pick === 'refused' && (
        <div className="row row-wrap">
          <Seg value={reason} onChange={setReason} options={Object.entries(REFUSAL_LABEL).map(([value, label]) => ({ value, label }))} />
          <Button variant="danger" onClick={() => m.mutate('refused')} loading={m.isPending}>Сохранить отказ</Button>
        </div>
      )}
    </div>
  );
}

function OutcomeCompare({ lead, compact }: { lead: Lead; compact?: boolean }) {
  const { isStaff } = useAuth();
  const { done, toast, qc } = useLeadMut();
  const match = lead.client_outcome && lead.agent_outcome && lead.client_outcome === lead.agent_outcome;
  const confirm = useMutation({
    mutationFn: () => api.deals.confirm(lead.deal_id!),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['deals'] }); api.leads.get(lead.id).then((l) => done(l, 'Сделка подтверждена · #Rented')); },
    onError: (e) => toast(errText(e), true),
  });
  const box = (who: string, v?: string | null) => (
    <div className="outcome-card">
      <div className="label">{who}</div>
      <div style={{ fontSize: 18, fontWeight: 800, marginTop: 4 }}>{v ? OUTCOME_LABEL[v] || v : <span className="faint">нет ответа</span>}</div>
    </div>
  );
  return (
    <div className="col">
      <div className="side-by-side">{box('Агент', lead.agent_outcome)}{box('Клиент', lead.client_outcome)}</div>
      {lead.refusal_reason && <div className="muted">Причина отказа: <b>{REFUSAL_LABEL[lead.refusal_reason] || lead.refusal_reason}</b></div>}
      {lead.client_outcome && lead.agent_outcome && !match && <div className="alert alert-warn">Ответы расходятся · уточните у агента и клиента</div>}
      {isStaff && lead.status === 'deal_pending' && lead.deal_id && (
        <div className="row row-wrap">
          <Button variant="ok" size="lg" loading={confirm.isPending} onClick={() => confirm.mutate()}><CheckCircle2 size={18} />Подтвердить сделку</Button>
          <Link className="btn btn-secondary btn-lg" to={`/deals?open=${lead.deal_id}`}>Открыть сделку</Link>
        </div>
      )}
      {lead.status === 'deal_pending' && !lead.deal_id && (
        <Link className="btn btn-lg" to={compact ? `/m/deal?lead_id=${lead.id}` : `/deals/new?lead_id=${lead.id}`}>Заполнить форму сделки</Link>
      )}
    </div>
  );
}

export function LeadDetail({ id, compact }: { id: number; compact?: boolean }) {
  const { user, isStaff } = useAuth();
  const { data: lead, isLoading, error } = useQuery({ queryKey: ['lead', id], queryFn: () => api.leads.get(id), refetchInterval: 20_000 });
  const { done, toast } = useLeadMut();
  const [assignOpen, setAssignOpen] = useState(false);
  const [msgOpen, setMsgOpen] = useState(false);
  const [msg, setMsg] = useState('');
  const [cancelOpen, setCancelOpen] = useState(false);
  const [reason, setReason] = useState('');
  const take = useMutation({ mutationFn: () => api.leads.take(id), onSuccess: (l) => done(l, 'Заявка ваша'), onError: (e) => toast(errText(e), true) });
  const resched = useMutation({ mutationFn: () => api.leads.reschedule(id), onSuccess: (l) => done(l, 'Агент снова введёт время') , onError: (e) => toast(errText(e), true) });
  const send = useMutation({ mutationFn: () => api.leads.message(id, msg), onSuccess: () => { toast('Бот отправил сообщение клиенту'); setMsgOpen(false); setMsg(''); }, onError: (e) => toast(errText(e), true) });
  const cancel = useMutation({ mutationFn: () => api.leads.cancel(id, reason), onSuccess: (l) => { done(l, 'Заявка отменена'); setCancelOpen(false); }, onError: (e) => toast(errText(e), true) });

  if (isLoading) return <Loading />;
  if (error || !lead) return <ErrorBox error={error} />;
  const c = lead.client;
  const isAgent = lead.agent_id === user?.id || lead.agent?.id === user?.id;
  const closed = ['rented', 'refused'].includes(lead.status);
  const l = lead.listing;
  const showOutcome = ['viewing_done', 'deal_pending', 'rented', 'refused', 'thinking'].includes(lead.status) || lead.agent_outcome || lead.client_outcome;

  return (
    <div className="col gap-lg">
      <div className="card card-pad">
        <div className="row row-between row-wrap">
          <div>
            <div className="label">Заявка #{lead.id} · {fmtDateTime(lead.created_at)}</div>
            <h1 style={{ fontSize: compact ? 20 : 24 }}>{l?.title || `Объект ${lead.listing_id || ''}`}</h1>
            <div className="row row-wrap gap-sm mt"><LeadStatusBadge status={lead.status} /><SlaChip lead={lead} />
              {lead.viewing_at && <Badge tone="solid"><CalendarClock size={12} />просмотр {fmtSlot(lead.viewing_at)}</Badge>}
            </div>
          </div>
          <div className="row row-wrap">
            {isStaff && lead.status === 'new' && <Button size="lg" loading={take.isPending} onClick={() => take.mutate()}><Hand size={18} />Взять</Button>}
            {isStaff && !closed && <Button size={lead.status === 'operator_taken' ? 'lg' : undefined} variant={lead.status === 'operator_taken' ? 'primary' : 'secondary'} onClick={() => setAssignOpen(true)}><UserPlus size={16} />{lead.agent ? 'Сменить агента' : 'Назначить агента'}</Button>}
          </div>
        </div>
        <div className="divider" />
        <div className="row row-wrap gap-lg" style={{ fontSize: 13 }}>
          <span><span className="label">Оператор </span><b>{lead.operator?.name || '—'}</b></span>
          <span><span className="label">Агент </span><b>{lead.agent?.name || '—'}</b></span>
          {lead.client_note && <span className="muted">{lead.client_note}</span>}
        </div>
      </div>

      <div className={compact ? 'col gap-lg' : 'split'}>
        <div className="col gap-lg">
          {(isAgent || isStaff) && (lead.status === 'agent_assigned' || (lead.status === 'slots_sent' && isAgent)) && (
            <Card label={lead.status === 'slots_sent' ? 'Добавить ещё окна' : 'Шаг C7'} title="Свободное время для просмотра"><SlotsEntry lead={lead} /></Card>
          )}
          {lead.slots && lead.slots.length > 0 && (
            <Card label="Окна" title="Время, отправленное клиенту">
              <div className="row row-wrap gap-sm">
                {lead.slots.map((s) => <span key={s.id} className={`slot-pill ${s.chosen ? 'chosen' : ''}`}>{s.chosen && <CheckCircle2 size={13} />}{fmtSlot(s.starts_at)}</span>)}
              </div>
            </Card>
          )}
          {isAgent && ['time_chosen', 'viewing_done', 'thinking'].includes(lead.status) && !lead.agent_outcome && (
            <Card label="Шаг C9a" title="Итог просмотра"><AgentOutcome lead={lead} /></Card>
          )}
          {showOutcome && <Card label="Шаг B6" title="Итог: агент и клиент"><OutcomeCompare lead={lead} compact={compact} /></Card>}
          {isStaff && !closed && (
            <div className="row row-wrap">
              <Button variant="secondary" onClick={() => setMsgOpen(true)}><MessageSquare size={16} />Написать клиенту</Button>
              {['slots_sent', 'time_chosen'].includes(lead.status) && <Button variant="secondary" loading={resched.isPending} onClick={() => resched.mutate()}><RotateCcw size={16} />Перенести</Button>}
              <Button variant="danger" onClick={() => setCancelOpen(true)}><XCircle size={16} />Отменить</Button>
            </div>
          )}
          {!compact && <Card label="История" title="Лог заявки"><Timeline events={lead.history || []} /></Card>}
        </div>
        <div className="col gap-lg">
          {l && (
            <div className="card lcard">
              {l.photos?.[0] && <div className="lcard-photo"><img src={mediaUrl(l.photos[0])} alt="" /></div>}
              <div className="lcard-body">
                <div className="label">Объект{lead.property_id ? ` #${lead.property_id}` : ''}</div>
                <div className="price">{money(l.price, l.currency || 'USD')}<span className="muted" style={{ fontSize: 12 }}>/мес</span></div>
                <div className="muted">{[l.district, bedroomsLabel(l.bedrooms), l.area ? `${l.area} м²` : null].filter(Boolean).join(' · ')}</div>
                <Place address={l.address} />
                {lead.property_id && <Link to={compact ? `/m/properties/${lead.property_id}` : `/objects/${lead.property_id}`} className="label">Открыть объект</Link>}
              </div>
            </div>
          )}
          <Card label="Клиент" title={c?.name || 'Клиент'} actions={c?.lang ? <Badge tone="grey">{c.lang.toUpperCase()}</Badge> : undefined}>
            {lead.contacts_visible ?? (!!lead.contacts_shared_at || isStaff) ? (
              <div className="col gap-sm" style={{ marginBottom: 12 }}>
                {c?.phone && <a href={`tel:${c.phone.replace(/\s/g, '')}`} style={{ fontWeight: 700 }}>{c.phone}</a>}
                {c?.username && <a href={`https://t.me/${c.username}`} target="_blank" rel="noreferrer" style={{ fontWeight: 700 }}>@{c.username}</a>}
                {lead.contacts_shared_at && <span className="faint" style={{ fontSize: 12 }}>Контакты переданы агенту {fmtDateTime(lead.contacts_shared_at)}</span>}
              </div>
            ) : (
              <div className="alert alert-info" style={{ marginBottom: 12 }}>Контакты бот пришлёт сам, когда клиент выберет время</div>
            )}
            {c?.profile ? <ProfileView profile={c.profile} /> : <span className="muted">Анкета не заполнена</span>}
          </Card>
          {compact && <Card label="История" title="Лог заявки"><Timeline events={lead.history || []} /></Card>}
        </div>
      </div>

      <AssignModal lead={lead} open={assignOpen} onClose={() => setAssignOpen(false)} />
      <Modal open={msgOpen} onClose={() => setMsgOpen(false)} title="Сообщение клиенту">
        <Field label="Текст · бот отправит от имени агентства"><Textarea value={msg} onChange={(e) => setMsg(e.target.value)} /></Field>
        <Button className="mt" disabled={!msg.trim()} loading={send.isPending} onClick={() => send.mutate()}><Send size={16} />Отправить</Button>
      </Modal>
      <Modal open={cancelOpen} onClose={() => setCancelOpen(false)} title="Отменить заявку">
        <Field label="Причина"><Textarea value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
        <Button className="mt" variant="danger" disabled={!reason.trim()} loading={cancel.isPending} onClick={() => cancel.mutate()}>Отменить заявку</Button>
      </Modal>
    </div>
  );
}
