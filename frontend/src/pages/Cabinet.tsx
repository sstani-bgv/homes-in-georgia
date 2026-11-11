import { useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { BookOpen, CalendarClock, CalendarDays, FileText } from 'lucide-react';
import { api } from '../api';
import { itemsOf } from '../api/client';
import type { PropertyStatus } from '../api/types';
import { FIELD_FILTER } from '../api/types';
import { useAuth } from '../auth/AuthContext';
import { PropertyStatusBadge } from '../components/domain';
import { Avatar, Badge, Card, Drawer, Empty, ErrorBox, Loading, PageHead, Progress, Seg, Stat, Tabs } from '../components/ui';
import { MyLeadsList } from '../features/leads/LeadBoard';
import { PropertyList } from '../features/properties/lists';
import { PROPERTY_STATUS, ROLE_LABEL, fmtDate, fmtSlot, money, monthLabel } from '../lib/format';
import { CalendarView } from './Calendar';
import { DealStatusBadge, DealView, DealsList } from './Deals';
import { LevelProgress } from './Team';

const CNT: [string, string][] = [['photo', 'Фото'], ['showing', 'Показы'], ['stickers', 'Расклейка'], ['rental', 'Сдачи']];

/** userId: staff looking at an agent's cabinet (undefined = one's own); tabbed: the desktop «Обзор» tab, where
 * objects and deals have their own tabs (the Mini App keeps the short lists here). */
export function CabinetView({ base = '', userId, tabbed }: { base?: string; userId?: number; tabbed?: boolean }) {
  const { user } = useAuth();
  const { data, isLoading, error } = useQuery({ queryKey: ['cabinet', userId], queryFn: () => api.cabinet(userId) });
  if (isLoading) return <Loading />;
  if (error || !data) return <ErrorBox error={error} />;
  const u = data.user || user;
  const lvl = data.level;
  const leadLink = (id: number) => (base ? `${base}/leads/${id}` : `/leads/${id}`);
  const propLink = (id: number) => (base ? `${base}/properties/${id}` : `/objects/${id}`);
  const cur = data.monthly?.[0];
  const leases = data.expiring_leases || [];
  return (
    <div className="col gap-lg">
      <div className="card card-blue card-pad">
        <div className="row">
          <Avatar size="lg" name={u?.name} src={u?.photo_url} />
          <div className="grow">
            <div className="label">{u ? ROLE_LABEL[u.role] : ''}</div>
            <h2 style={{ color: '#fff', fontSize: 22, fontWeight: 900 }}>{u?.name}</h2>
          </div>
          {lvl && <div style={{ textAlign: 'right' }}><div className="stat-value" style={{ marginTop: 0 }}>{lvl.level}%</div><div className="label">уровень</div></div>}
        </div>
        {lvl && <div className="mt-lg"><LevelProgress deals={lvl.exclusive_deals} level={lvl.level} /><div className="label mt">{lvl.exclusive_deals} эксклюзивных сделок</div></div>}
      </div>

      {cur && (
        <div className="grid g4">
          <Stat label={`Сделок · ${monthLabel(cur.month)}`} value={cur.deals} sub={`аренда: ${cur.rentals}`} />
          <Stat label="Заработано за месяц" value={money(cur.earned)} />
          <Stat label="Платформе за месяц" value={money(cur.platform)} />
          <Stat label="Объектов добавлено" value={cur.added} />
        </div>
      )}

      {data.counters && (
        <Card label={data.month ? monthLabel(data.month) : 'Этот месяц'} title="Мои счётчики">
          <div className="grid g4">
            {CNT.map(([k, label]) => {
              const c = data.counters?.[k as 'photo'];
              if (!c) return null;
              return (
                <div key={k} className="col gap-sm">
                  <div className="row row-between"><span className="label">{label}</span><b className="num">{c.done}/{c.plan}</b></div>
                  <Progress value={c.done} max={c.plan} />
                </div>
              );
            })}
          </div>
        </Card>
      )}

      {(() => {
        const ch = (data as unknown as { channel_listings?: { active?: number; last_7_days?: number } }).channel_listings;
        const reps = ((data as unknown as { reports?: { id: number; kind: string; date?: string | null; created_at?: string }[] }).reports || []);
        return (
          <div className="grid g2">
            <Card label="Инструменты" title="Быстрый доступ">
              <div className="add-grid">
                <Link to={`${base}/contracts`} className="add-item"><FileText size={20} /><span><b>Договор</b><small>Аренда или продажа</small></span></Link>
                <Link to={`${base}/calendar`} className="add-item"><CalendarDays size={20} /><span><b>Календарь</b><small>Показы и съёмки</small></span></Link>
                {!base && <Link to="/team?tab=standards" className="add-item"><BookOpen size={20} /><span><b>Стандарты</b><small>Лестница и викторина</small></span></Link>}
              </div>
            </Card>
            <Card label="Канал" title="Мои публикации">
              {ch ? (
                <div className="grid g2">
                  <div><div className="label">Активных</div><div className="stat-value">{ch.active ?? 0}</div></div>
                  <div><div className="label">За 7 дней</div><div className="stat-value">{ch.last_7_days ?? 0}</div></div>
                </div>
              ) : <div className="muted">Нет данных</div>}
              {reps.length > 0 && <div className="muted mt" style={{ fontSize: 12 }}>Отчётов с моим участием: {reps.length}, последний {fmtDate(reps[0].date || reps[0].created_at)}</div>}
            </Card>
          </div>
        );
      })()}

      <div className="grid g2">
        <Card label="Ближайшие" title="Просмотры">
          {(data.viewings || []).length === 0 ? <Empty text="Просмотров не запланировано" icon={<CalendarClock size={28} />} /> : (data.viewings || []).map((l) => (
            <Link key={l.id} to={leadLink(l.id)} className="list-row" style={{ color: 'inherit', textDecoration: 'none' }}>
              <Badge tone="solid">{l.viewing_at ? fmtSlot(l.viewing_at) : '—'}</Badge>
              <span className="grow ellipsis" style={{ fontWeight: 600 }}>{l.listing?.title}</span>
            </Link>
          ))}
        </Card>
        <Card label="Аренда" title="Скоро заканчиваются">
          {(data.expiring || []).length === 0 && leases.length === 0 ? <Empty text="Нет договоров на продление" /> : (data.expiring || []).map((d) => (
            <div key={d.id} className="list-row"><span className="grow"><b>{d.address}</b><div className="muted" style={{ fontSize: 12 }}>до {fmtDate(d.rent_to)}</div></span><Badge tone="warn">{d.client_name}</Badge></div>
          ))}
          {/* objects with a lease end: ended in the last 2 weeks or ends within 2 months, time to re-rent */}
          {leases.map((p) => {
            const ended = new Date(p.rent_to) < new Date(new Date().toDateString());
            return (
              <Link key={`p${p.id}`} to={propLink(p.id)} className="list-row" style={{ color: 'inherit', textDecoration: 'none' }}>
                <span className="grow"><b>{p.address || `Объект #${p.id}`}</b><div className="muted" style={{ fontSize: 12 }}>{ended ? 'закончилась' : 'до'} {fmtDate(p.rent_to)}{p.tenant_name ? ` · ${p.tenant_name}` : ''}</div></span>
                <Badge tone={ended ? 'red' : 'warn'}>{ended ? 'пересдать' : 'аренда'}</Badge>
              </Link>
            );
          })}
        </Card>
      </div>

      {!tabbed && <div className="grid g2">
        <Card title="Мои объекты">
          {(data.properties || []).length === 0 ? <Empty text="Объектов нет" /> : (data.properties || []).slice(0, 8).map((p) => (
            <Link key={p.id} to={propLink(p.id)} className="list-row" style={{ color: 'inherit', textDecoration: 'none' }}>
              <span className="grow ellipsis"><b>{p.address}</b> <span className="muted">{p.district}</span></span><PropertyStatusBadge status={p.status} />
            </Link>
          ))}
        </Card>
        <Card label="Сделки" title="Мои сделки">
          {(data.deals || []).length === 0 ? <Empty text="Сделок нет" /> : (data.deals || []).slice(0, 8).map((d) => (
            <div key={d.id} className="list-row">
              <span className="grow ellipsis"><b>{d.address || `#${d.id}`}</b><div className="muted" style={{ fontSize: 12 }}>{fmtDate(d.created_at)} · моя доля {money(d.agent_amount)}</div></span>
              <DealStatusBadge s={d.status} />
            </div>
          ))}
        </Card>
      </div>}
    </div>
  );
}

type Tab = 'overview' | 'objects' | 'deals' | 'leads' | 'calendar' | 'stats';
const TABS: { value: Tab; label: string }[] = [
  { value: 'overview', label: 'Обзор' }, { value: 'objects', label: 'Объекты' }, { value: 'deals', label: 'Сделки' },
  { value: 'leads', label: 'Заявки' }, { value: 'calendar', label: 'Календарь' }, { value: 'stats', label: 'Статистика' },
];
const DONE: PropertyStatus[] = ['published', 'rented', 'archived'];
const GROUPS: Record<string, { label: string; status: string }> = {
  work: { label: 'В работе', status: (Object.keys(PROPERTY_STATUS) as PropertyStatus[]).filter((s) => !DONE.includes(s)).join(',') },
  published: { label: 'Опубликованные', status: 'published' },
  rented: { label: 'Сданные', status: 'rented' },
  archived: { label: 'Архив', status: 'archived' },
};

function CabinetObjects({ agentId }: { agentId: number }) {
  const [g, setG] = useState('work');
  return (
    <div className="col">
      <Seg value={g} onChange={setG} options={Object.entries(GROUPS).map(([value, x]) => ({ value, label: x.label }))} />
      <PropertyList key={g} status={GROUPS[g].status} agentId={agentId} />
    </div>
  );
}

function CabinetDeals({ agentId }: { agentId: number }) {
  const [open, setOpen] = useState<number | null>(null);
  return (
    <>
      <DealsList agentId={agentId} onOpen={setOpen} />
      <Drawer open={!!open} onClose={() => setOpen(null)} title={`Сделка #${open}`}>{open && <DealView id={open} onClose={() => setOpen(null)} />}</Drawer>
    </>
  );
}

/** per month: deals (rentals), objects added, the agent's money and the platform's share; bars scaled to the best month */
function CabinetStats({ userId }: { userId?: number }) {
  const { data, isLoading, error } = useQuery({ queryKey: ['cabinet', userId], queryFn: () => api.cabinet(userId) });
  if (isLoading) return <Loading />;
  if (error || !data) return <ErrorBox error={error} />;
  const rows = data.monthly || [];
  const max = Math.max(1, ...rows.map((r) => r.earned));
  const sum = (k: 'deals' | 'rentals' | 'added' | 'earned' | 'platform' | 'commission') => rows.reduce((a, r) => a + r[k], 0);
  return (
    <div className="col gap-lg">
      <div className="grid g4">
        <Stat label="Сделок за 12 месяцев" value={sum('deals')} sub={`аренда: ${sum('rentals')}`} />
        <Stat blue label="Заработано" value={money(sum('earned'))} />
        <Stat label="Платформе" value={money(sum('platform'))} sub={`комиссия всего: ${money(sum('commission'))}`} />
        <Stat label="Объектов добавлено" value={sum('added')} />
      </div>
      <div className="card table-wrap">
        <table className="table">
          <thead><tr><th>Месяц</th><th>Сделок</th><th>Аренда</th><th>Объектов</th><th>Заработано</th><th className="hide-mobile">Платформе</th><th className="hide-mobile">Комиссия</th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.month}>
                <td className="nowrap">{monthLabel(r.month)}</td>
                <td className="num">{r.deals}</td>
                <td className="num">{r.rentals}</td>
                <td className="num">{r.added}</td>
                <td style={{ minWidth: 160 }}><div className="num" style={{ fontWeight: 700 }}>{money(r.earned)}</div><Progress value={r.earned} max={max} /></td>
                <td className="num hide-mobile">{money(r.platform)}</td>
                <td className="num hide-mobile">{money(r.commission)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="faint" style={{ fontSize: 12 }}>Сделки без отменённых, по месяцу сделки. Заработано: доля агента плюс доля рекрутера, если он привёл. Платформе: доля платформы с его сделок.</div>
    </div>
  );
}

/** ?tab= one of TABS; staff open any agent's cabinet with ?user=<id>, agents always get their own */
export function CabinetPage() {
  const { user, isStaff } = useAuth();
  const [params, setParams] = useSearchParams();
  const set = (k: string, v: string) => { if (v) params.set(k, v); else params.delete(k); setParams(params, { replace: true }); };
  const tab = (params.get('tab') as Tab) || 'overview';
  const picked = isStaff ? Number(params.get('user')) || undefined : undefined;
  const uid = picked ?? user?.id ?? 0;
  const agents = useQuery({ queryKey: ['users', 'agents-all'], queryFn: () => api.users.list({ role: FIELD_FILTER }), enabled: isStaff });
  const pick = isStaff && (
    <select className="select" style={{ width: 'auto' }} aria-label="Чей кабинет" value={picked ?? ''} onChange={(e) => set('user', e.target.value)}>
      <option value="">Мой кабинет</option>
      {itemsOf(agents.data).slice().sort((a, b) => Number(b.is_active !== false) - Number(a.is_active !== false) || (a.name || '').localeCompare(b.name || '')).map((u) => (
        <option key={u.id} value={u.id}>{u.name}{u.is_active === false ? ' (не работает)' : ''}</option>
      ))}
    </select>
  );
  return (
    <>
      <PageHead label="Личный кабинет" title="Кабинет" actions={pick} />
      <Tabs value={tab} onChange={(t) => set('tab', t === 'overview' ? '' : t)} tabs={TABS} />
      {tab === 'overview' ? <CabinetView userId={picked} tabbed />
        : tab === 'objects' ? <CabinetObjects key={uid} agentId={uid} />
        : tab === 'deals' ? <CabinetDeals key={uid} agentId={uid} />
        : tab === 'leads' ? <MyLeadsList agentId={uid} />
        : tab === 'calendar' ? <CalendarView agentId={uid} />
        : <CabinetStats userId={picked} />}
    </>
  );
}
