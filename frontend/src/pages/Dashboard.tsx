import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { ArrowRight, Building2, Clock, Coins, Handshake, Repeat2, Send } from 'lucide-react';
import { api } from '../api';
import { itemsOf } from '../api/client';
import type { Lead, Property } from '../api/types';
import { useAuth } from '../auth/AuthContext';
import { SlaChip, LeadStatusBadge, PropertyStatusBadge } from '../components/domain';
import { Avatar, Card, Empty, ErrorBox, Loading, PageHead, Progress, Stat } from '../components/ui';
import { parseMetrics } from '../lib/useUserNames';
import { LEAD_BOARD, LEAD_STATUS, PROPERTY_BOARD, PROPERTY_STATUS, fmtSlot, money, nextLevelAt, num } from '../lib/format';

function FunnelRow({ order, labels, counts, link }: { order: string[]; labels: Record<string, string>; counts?: Record<string, number>; link: string }) {
  const max = Math.max(1, ...order.map((k) => counts?.[k] || 0));
  return (
    <div className="col gap-sm">
      {order.map((k) => (
        <Link key={k} to={link} className="row" style={{ color: 'inherit', textDecoration: 'none' }}>
          <span style={{ width: 132, fontSize: 12, fontWeight: 600 }} className="muted ellipsis">{labels[k]}</span>
          <div className="grow" style={{ height: 18, background: 'var(--brand-tint)', borderRadius: 6, overflow: 'hidden' }}>
            <div style={{ width: `${((counts?.[k] || 0) / max) * 100}%`, height: '100%', background: k === 'refused' ? 'var(--faint)' : 'var(--brand)', borderRadius: 6, minWidth: counts?.[k] ? 4 : 0 }} />
          </div>
          <span className="num" style={{ width: 28, textAlign: 'right', fontWeight: 800 }}>{counts?.[k] || 0}</span>
        </Link>
      ))}
    </div>
  );
}

function TaskRow({ to, title, meta, right }: { to: string; title: string; meta?: string; right?: React.ReactNode }) {
  return (
    <Link to={to} className="list-row" style={{ color: 'inherit', textDecoration: 'none' }}>
      <div className="grow">
        <div style={{ fontWeight: 700 }} className="ellipsis">{title}</div>
        {meta && <div className="muted" style={{ fontSize: 12 }}>{meta}</div>}
      </div>
      {right}
      <ArrowRight size={16} color="var(--faint)" />
    </Link>
  );
}

function MyTasks() {
  const { user, isStaff } = useAuth();
  const leads = useQuery({ queryKey: ['leads', 'list', isStaff ? 'all' : 'mine'], queryFn: () => api.leads.list(isStaff ? {} : { agent_id: user?.id }) });
  const props = useQuery({ queryKey: ['properties', 'list', isStaff ? 'review' : 'mine'], queryFn: () => api.properties.list(isStaff ? { status: 'review' } : { mine: 1 }) });
  const ls = itemsOf(leads.data);
  const ps = itemsOf(props.data);

  let leadTasks: Lead[];
  let propTasks: Property[];
  if (isStaff) {
    leadTasks = ls.filter((l) => ['new', 'operator_taken', 'viewing_done', 'deal_pending'].includes(l.status) || (l.sla_due_at && new Date(l.sla_due_at).getTime() < Date.now()));
    propTasks = ps.filter((p) => p.status === 'review');
  } else {
    leadTasks = ls.filter((l) => ['agent_assigned', 'time_chosen', 'viewing_done'].includes(l.status));
    propTasks = ps.filter((p) => ['taken', 'call_failed', 'owner_confirmed', 'shoot_scheduled', 'returned', 'media_uploaded'].includes(p.status));
  }
  const total = leadTasks.length + propTasks.length;

  return (
    <Card label="Мои задачи" title={total ? `${total} в работе` : 'Всё сделано'}>
      {leads.isLoading || props.isLoading ? <Loading /> : total === 0 ? <Empty text="Срочных задач нет" /> : (
        <div>
          {leadTasks.slice(0, 6).map((l) => (
            <TaskRow key={`l${l.id}`} to={`/leads/${l.id}`} title={`Заявка #${l.id} · ${l.listing?.title || ''}`}
              meta={l.status === 'agent_assigned' && !isStaff ? 'Введите свободное время' : l.status === 'new' ? 'Возьмите заявку' : l.viewing_at ? `Просмотр ${fmtSlot(l.viewing_at)}` : LEAD_STATUS[l.status]}
              right={l.sla_due_at ? <SlaChip lead={l} /> : <LeadStatusBadge status={l.status} />} />
          ))}
          {propTasks.slice(0, 6).map((p) => (
            <TaskRow key={`p${p.id}`} to={`/objects/${p.id}`} title={`${p.district || ''} · ${p.address || 'Объект #' + p.id}`}
              meta={isStaff ? 'Проверить и опубликовать' : p.status === 'call_failed' ? 'Перезвонить собственнику' : p.status === 'returned' ? p.review_comment || 'Возвращён на доработку' : PROPERTY_STATUS[p.status]}
              right={<PropertyStatusBadge status={p.status} />} />
          ))}
        </div>
      )}
    </Card>
  );
}

export function DashboardPage() {
  const { user } = useAuth();
  const { data, isLoading, error } = useQuery({ queryKey: ['dashboard'], queryFn: api.analytics.dashboard, refetchInterval: 60_000 });
  const settings = useQuery({ queryKey: ['settings'], queryFn: api.settings.get, retry: false });
  const metrics = parseMetrics(settings.data?.dashboard_metrics) || parseMetrics(data?.metrics);
  const ALIAS: Record<string, string[]> = { deals: ['deals'], commission: ['platform', 'commission'], objects: ['active_objects', 'objects'],
    reposts: ['reposts'], top_agents: ['leaderboard', 'top_agents'], telegram_top: ['telegram_top'], funnel: ['funnel'], response_times: ['response_times'] };
  const show = (k: string) => !metrics || (ALIAS[k] || [k]).some((a) => metrics[a]);
  const rt = data?.response_times as { operator_take_avg_min?: number | null; agent_slots_avg_min?: number | null } | undefined;
  const target = Number(settings.data?.deals_plan || (data as Record<string, unknown> | undefined)?.deals_plan || 0);

  return (
    <>
      <PageHead label={`Добрый день, ${user?.name || ''}`} title="Главная" />
      {isLoading ? <Loading /> : error ? <ErrorBox error={error} /> : data && (
        <>
          <div className="grid g4">
            {show('deals') && <Stat blue label="Сделок за месяц" value={num(data.deals_count)} icon={<Handshake size={18} />}
              sub={target ? <><span>План {target}</span><div className="mt"><Progress value={data.deals_count || 0} max={target} /></div></> : undefined} />}
            {show('commission') && <Stat label="Комиссия платформы" value={money(data.platform_commission)} icon={<Coins size={18} color="var(--brand)" />} sub="за текущий месяц" />}
            {show('objects') && <Stat label="Активных объектов" value={num(data.active_objects)} icon={<Building2 size={18} color="var(--brand)" />} sub="в канале и базе" />}
            {show('reposts') && <Stat label="Репостов" value={num(data.reposts)} icon={<Repeat2 size={18} color="var(--brand)" />} sub={data.sla_overdue ? <span style={{ color: 'var(--accent)', fontWeight: 700 }}>Просрочено заявок: {data.sla_overdue}</span> : 'за месяц'} />}
          </div>

          <div className="split section">
            <div className="col gap-lg">
              <MyTasks />
              {show('funnel') && (
                <div className="grid g2">
                  <Card label="Воронка 1" title="Объекты">
                    <FunnelRow order={PROPERTY_BOARD} labels={PROPERTY_STATUS} counts={data.funnel?.properties} link="/objects?tab=board" />
                  </Card>
                  <Card label="Воронка 2" title="Заявки">
                    <FunnelRow order={LEAD_BOARD} labels={LEAD_STATUS} counts={data.funnel?.leads} link="/leads" />
                  </Card>
                </div>
              )}
            </div>
            <div className="col gap-lg">
              {show('top_agents') && (
                <Card label="Лидеры" title="Топ-5 агентов" actions={<Link to="/team" className="label">Все</Link>}>
                  {(data.top_agents || []).length === 0 ? <Empty /> : (data.top_agents || []).slice(0, 5).map((a, i) => {
                    const next = a.next_level_at ?? nextLevelAt(a.exclusive_deals);
                    return (
                      <div key={a.id} className="list-row">
                        <span className={`rank ${i === 0 ? 'top' : ''}`}>{i + 1}</span>
                        <Avatar name={a.name} src={a.photo_url} />
                        <div className="grow">
                          <div className="row row-between"><b>{a.name}</b><span className="badge">{a.level || 50}%</span></div>
                          <div className="muted" style={{ fontSize: 12 }}>{a.exclusive_deals} эксклюзивов{next ? ` · до ${a.level === 50 ? 70 : 90}%: ${next - a.exclusive_deals}` : ''}</div>
                        </div>
                      </div>
                    );
                  })}
                </Card>
              )}
              {show('telegram_top') && (data.telegram_top || []).length > 0 && (
                <Card label="Telegram" title="Топ-10 постов" actions={<Send size={16} color="var(--brand)" />}>
                  {(data.telegram_top || []).slice(0, 10).map((t, i) => (
                    <div key={i} className="list-row" style={{ padding: '7px 0' }}>
                      <span className="rank" style={{ width: 22, height: 22, fontSize: 11 }}>{i + 1}</span>
                      <span className="grow ellipsis" style={{ fontSize: 13, fontWeight: 600 }}>{t.name}</span>
                      <span className="num muted" style={{ fontSize: 12, fontWeight: 700 }}>{num(t.value)}</span>
                    </div>
                  ))}
                </Card>
              )}
              {show('response_times') && rt && (
                <Card label="Скорость" title="Время реакции, 30 дней">
                  <div className="grid g2">
                    <div><div className="label">Оператор берёт</div><div className="stat-value" style={{ fontSize: 22 }}>{rt.operator_take_avg_min ?? '—'}<span className="muted" style={{ fontSize: 12 }}> мин</span></div></div>
                    <div><div className="label">Агент даёт время</div><div className="stat-value" style={{ fontSize: 22 }}>{rt.agent_slots_avg_min ?? '—'}<span className="muted" style={{ fontSize: 12 }}> мин</span></div></div>
                  </div>
                </Card>
              )}
              <Card label="SLA" title="Сроки шагов">
                <div className="col gap-sm muted" style={{ fontSize: 13 }}>
                  <div className="row"><Clock size={14} />Оператор берёт заявку: 5 мин</div>
                  <div className="row"><Clock size={14} />Агент вводит время: 30 мин</div>
                  <div className="row"><Clock size={14} />Время клиенту: 1 ч</div>
                  <div className="row"><Clock size={14} />Проверка объекта: до 1 ч</div>
                </div>
              </Card>
            </div>
          </div>
        </>
      )}
    </>
  );
}
