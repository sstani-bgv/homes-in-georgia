// /calendar — showings, shoots and control points (old tab «tab-calendar» + cabinet week calendar).
// Built from existing data: leads.viewing_at (showings), properties.shoot_at / next_call_at, deals.t14_date / rent_to.
import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Camera, ChevronLeft, ChevronRight, ClipboardCheck, Eye, FileClock, PhoneCall } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { api } from '../api';
import { itemsOf } from '../api/client';
import type { Deal } from '../api/types';
import { Card, Empty, ErrorBox, Loading, PageHead, Seg } from '../components/ui';

type Kind = 'showing' | 'shoot' | 'call' | 't14' | 'expiry';
interface Ev { id: string; at: Date; allDay: boolean; kind: Kind; title: string; meta?: string; to: string }
const KIND: Record<Kind, { label: string; icon: LucideIcon }> = {
  showing: { label: 'Показ', icon: Eye }, shoot: { label: 'Съёмка', icon: Camera }, call: { label: 'Перезвон', icon: PhoneCall },
  t14: { label: 'День 14', icon: ClipboardCheck }, expiry: { label: 'Конец договора', icon: FileClock },
};
const WD = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
const MON = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];
const pad = (n: number) => String(n).padStart(2, '0');
const dayKey = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
function startOfWeek(d: Date) { const x = new Date(d.getFullYear(), d.getMonth(), d.getDate()); x.setDate(x.getDate() - ((x.getDay() + 6) % 7)); return x; }
function parseDay(s: string) { const [y, m, d] = s.slice(0, 10).split('-').map(Number); return new Date(y, m - 1, d); }

/** agentId: one agent's events (the cabinet opened by staff); agents only ever get their own from the backend */
export function useCalendarEvents(agentId?: number) {
  const a = { agent_id: agentId };
  const leads = useQuery({ queryKey: ['leads', 'calendar', agentId], queryFn: () => api.leads.list(a) });
  const props = useQuery({ queryKey: ['properties', 'calendar', agentId], queryFn: () => api.properties.list(a) });
  const deals = useQuery({ queryKey: ['deals', 'calendar', agentId], queryFn: () => api.deals.list(a) });
  const leases = useQuery({ queryKey: ['properties', 'calendar', 'rented', agentId], queryFn: () => api.properties.list({ ...a, rented: 1, limit: 500 }) });
  const events = useMemo(() => {
    const out: Ev[] = [];
    for (const l of itemsOf(leads.data)) {
      if (!l.viewing_at || ['refused', 'rented'].includes(l.status)) continue;
      out.push({ id: `l${l.id}`, at: new Date(l.viewing_at), allDay: false, kind: 'showing', title: l.listing?.address || l.listing?.title || `Заявка #${l.id}`,
        meta: [l.client?.name, l.agent?.name].filter(Boolean).join(' · '), to: `/leads/${l.id}` });
    }
    for (const p of itemsOf(props.data)) {
      if (p.shoot_at && p.status === 'shoot_scheduled') out.push({ id: `s${p.id}`, at: new Date(p.shoot_at), allDay: false, kind: 'shoot', title: p.address || `Объект #${p.id}`, meta: p.agent?.name || undefined, to: `/objects/${p.id}` });
      if (p.next_call_at && ['taken', 'call_failed'].includes(p.status)) out.push({ id: `c${p.id}`, at: new Date(p.next_call_at), allDay: false, kind: 'call', title: p.address || `Объект #${p.id}`, meta: 'Повторный звонок собственнику', to: `/objects/${p.id}` });
    }
    for (const d of itemsOf(deals.data) as Deal[]) {
      if (d.status === 'cancelled') continue;
      if (d.t14_date && d.t14_status !== 'done') out.push({ id: `t${d.id}`, at: parseDay(d.t14_date), allDay: true, kind: 't14', title: d.address || `Сделка #${d.id}`, meta: 'Связаться с клиентом и собственником', to: `/deals?open=${d.id}` });
      if (d.rent_to) out.push({ id: `e${d.id}`, at: parseDay(d.rent_to), allDay: true, kind: 'expiry', title: d.address || `Сделка #${d.id}`, meta: d.client_name || undefined, to: `/deals?open=${d.id}` });
    }
    for (const p of itemsOf(leases.data)) {
      if (p.rent_to) out.push({ id: `r${p.id}`, at: parseDay(p.rent_to), allDay: true, kind: 'expiry', title: p.address || `Объект #${p.id}`, meta: p.tenant_name || 'Окончание аренды', to: `/objects/${p.id}` });
    }
    return out.filter((e) => !Number.isNaN(e.at.getTime())).sort((a, b) => a.at.getTime() - b.at.getTime());
  }, [leads.data, props.data, deals.data, leases.data]);
  return { events, isLoading: leads.isLoading || props.isLoading || deals.isLoading, error: leads.error || props.error || deals.error };
}

const MONTHS = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь', 'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'];

export function CalendarView({ base = '', agentId }: { base?: string; agentId?: number }) {
  const { events, isLoading, error } = useCalendarEvents(agentId);
  const [month, setMonth] = useState(() => new Date(new Date().getFullYear(), new Date().getMonth(), 1));
  const [sel, setSel] = useState(() => dayKey(new Date()));
  const [filter, setFilter] = useState<'all' | Kind>('all');
  // 6 weeks from the Monday on/before the 1st: a fixed-height month grid
  const first = startOfWeek(month);
  const days = Array.from({ length: 42 }, (_, i) => { const d = new Date(first); d.setDate(d.getDate() + i); return d; });
  const byDay = useMemo(() => {
    const m = new Map<string, Ev[]>();
    for (const e of events) { if (filter !== 'all' && e.kind !== filter) continue; const k = dayKey(e.at); m.set(k, [...(m.get(k) || []), e]); }
    return m;
  }, [events, filter]);
  const today = dayKey(new Date());
  const shift = (n: number) => setMonth(new Date(month.getFullYear(), month.getMonth() + n, 1));
  const selDate = parseDay(sel);
  const list = byDay.get(sel) || [];
  const upcoming = events.filter((e) => (filter === 'all' || e.kind === filter) && e.at >= new Date(new Date().setHours(0, 0, 0, 0))).slice(0, 8);
  const link = (to: string) => (base && to.startsWith('/leads') ? `${base}${to}` : base && to.startsWith('/objects') ? to.replace('/objects', `${base}/properties`) : to);
  const time = (e: Ev) => (e.allDay ? '' : `${pad(e.at.getHours())}:${pad(e.at.getMinutes())} `);

  return (
    <div className="col gap-lg">
      <div className="row row-between row-wrap">
        <div className="row gap-sm">
          <button className="btn btn-secondary icon-btn" onClick={() => shift(-1)} aria-label="Предыдущий месяц"><ChevronLeft size={18} /></button>
          <h2 style={{ minWidth: 170, textAlign: 'center' }}>{MONTHS[month.getMonth()]} {month.getFullYear()}</h2>
          <button className="btn btn-secondary icon-btn" onClick={() => shift(1)} aria-label="Следующий месяц"><ChevronRight size={18} /></button>
          <button className="btn btn-secondary btn-sm" onClick={() => { setMonth(new Date(new Date().getFullYear(), new Date().getMonth(), 1)); setSel(today); }}>Сегодня</button>
        </div>
        <Seg value={filter} onChange={setFilter} options={[{ value: 'all', label: 'Все' }, ...(Object.keys(KIND) as Kind[]).map((k) => ({ value: k, label: KIND[k].label }))]} />
      </div>
      <div className="card cal-month">
        {WD.map((w) => <div key={w} className="cal-head">{w}</div>)}
        {days.map((d) => {
          const k = dayKey(d);
          const evs = byDay.get(k) || [];
          return (
            <button key={k} className={`cal-cell ${k === sel ? 'on' : ''} ${k === today ? 'today' : ''} ${d.getMonth() !== month.getMonth() ? 'out' : ''}`} onClick={() => setSel(k)}>
              <span className="cal-num">{d.getDate()}</span>
              {evs.slice(0, 3).map((e) => <span key={e.id} className={`cal-ev ev-${e.kind}`}>{time(e)}{e.title}</span>)}
              {evs.length > 3 && <span className="cal-more">ещё {evs.length - 3}</span>}
              {evs.length > 0 && <span className="cal-dots-m">{evs.slice(0, 3).map((e) => <i key={e.id} className={`ev-${e.kind}`} />)}</span>}
            </button>
          );
        })}
      </div>
      {isLoading ? <Loading /> : error ? <ErrorBox error={error} /> : (
        <div className="split">
          <Card label={sel === today ? 'Сегодня' : WD[(selDate.getDay() + 6) % 7]} title={`${selDate.getDate()} ${MON[selDate.getMonth()]}`}>
            {list.length === 0 ? <Empty text="На этот день ничего не запланировано" /> : list.map((e) => <EvRow key={e.id} e={e} to={link(e.to)} />)}
          </Card>
          <Card label="Ближайшие" title="Что дальше">
            {upcoming.length === 0 ? <Empty text="Ближайших событий нет" /> : upcoming.map((e) => <EvRow key={e.id} e={e} to={link(e.to)} withDate />)}
          </Card>
        </div>
      )}
      <div className="faint" style={{ fontSize: 12 }}>Показы появляются, когда клиент выбирает время в заявке. Съёмка — из карточки объекта, День 14 и конец договора — из сделок.</div>
    </div>
  );
}

function EvRow({ e, to, withDate }: { e: Ev; to: string; withDate?: boolean }) {
  const K = KIND[e.kind];
  const time = e.allDay ? 'весь день' : `${pad(e.at.getHours())}:${pad(e.at.getMinutes())}`;
  return (
    <Link to={to} className="list-row" style={{ color: 'inherit', textDecoration: 'none' }}>
      <span className="cal-time">{withDate ? <>{pad(e.at.getDate())}.{pad(e.at.getMonth() + 1)}<br /></> : null}<b>{time}</b></span>
      <span className={`avatar ev-${e.kind}`} aria-hidden><K.icon size={16} /></span>
      <span className="grow" style={{ minWidth: 0 }}><b className="ellipsis" style={{ display: 'block' }}>{e.title}</b><span className="muted" style={{ fontSize: 12 }}>{K.label}{e.meta ? ` · ${e.meta}` : ''}</span></span>
    </Link>
  );
}

export function CalendarPage() {
  return (<><PageHead label="Показы, съёмки и контрольные точки" title="Календарь" /><CalendarView /></>);
}
export function MCalendar() { return <CalendarView base="/m" />; }
