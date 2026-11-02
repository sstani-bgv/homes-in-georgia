// «Сделки → Аналитика» — port of the old «Рабочий дашборд» (tab-deals): period switch, KPI tiles,
// channel block, districts, apartment types, objects per week, platform commission by month, agent earnings, last deals.
// Data: GET /api/analytics/dashboard (?month=), GET /api/analytics/listings (channel posts). Visibility: settings.dashboard_metrics / dashboard_sections.
import { useState } from 'react';
import { useQueries, useQuery } from '@tanstack/react-query';
import { api } from '../../api';
import { http } from '../../api/client';
import { Badge, Card, Empty, ErrorBox, Loading, Seg, Stat } from '../../components/ui';
import { monthKey, money, num } from '../../lib/format';
import { parseMetrics } from '../../lib/useUserNames';

type Summary = { total: number; confirmed?: number; exclusive?: number; commission: number; platform: number; agents?: number; price: number; avg_commission: number };
type Monthly = { month: string; deals: number; commission: number; platform: number; price: number };
type LB = { id: number; name: string; deals: number; agent_amount: number; platform_amount: number };
type LastDeal = { id: number; agent?: string | null; price: number; commission: number; platform: number; exclusive: boolean; status: string; date: string };
type Raw = { deals_month?: Summary; deals_all?: Summary; monthly?: Monthly[]; leaderboard?: LB[]; leaderboard_all?: LB[]; last_deals?: LastDeal[]; channel_active_listings?: number; active_objects?: number };

export const ANALYTICS_METRICS: [string, string][] = [['an_deals', 'Сделок платформы'], ['an_platform', 'Комиссия платформы'], ['an_commission', 'Общая комиссия'],
  ['an_avg', 'Ср. комиссия'], ['an_turnover', 'Общий оборот'], ['an_objects', 'Объектов'], ['an_avg_price', 'Ср. цена аренды'], ['an_exclusive', 'Эксклюзивов']];
export const ANALYTICS_SECTIONS: [string, string][] = [['districts', 'Районы: объекты и цены'], ['types', 'Тип квартир'], ['weekly', 'Объекты за неделю'],
  ['monthly', 'График комиссии по месяцам'], ['agents', 'Агенты: заработок за период'], ['last', 'Последние сделки']];

const MON = ['янв', 'фев', 'мар', 'апр', 'май', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];
const shortMonth = (k: string) => { const [m, y] = k.split('/').map(Number); return `${MON[m - 1] || m} ${String(y).slice(2)}`; };
const prevMonths = (n: number) => Array.from({ length: n }, (_, i) => { const d = new Date(); return monthKey(new Date(d.getFullYear(), d.getMonth() - i, 1)); });

function sumSummaries(list: (Summary | undefined)[]): Summary {
  const s = list.filter(Boolean) as Summary[];
  const total = s.reduce((a, x) => a + (x.total || 0), 0);
  const commission = s.reduce((a, x) => a + (x.commission || 0), 0);
  return { total, commission, platform: s.reduce((a, x) => a + (x.platform || 0), 0), price: s.reduce((a, x) => a + (x.price || 0), 0),
    exclusive: s.reduce((a, x) => a + (x.exclusive || 0), 0), avg_commission: total ? Math.round(commission / total) : 0 };
}
function mergeLB(list: (LB[] | undefined)[]): LB[] {
  const m = new Map<number, LB>();
  for (const lb of list) for (const r of lb || []) {
    const c = m.get(r.id) || { id: r.id, name: r.name, deals: 0, agent_amount: 0, platform_amount: 0 };
    m.set(r.id, { ...c, deals: c.deals + r.deals, agent_amount: c.agent_amount + r.agent_amount, platform_amount: c.platform_amount + r.platform_amount });
  }
  return [...m.values()].sort((a, b) => b.agent_amount - a.agent_amount);
}

function Bars({ rows, fmt = num }: { rows: { k: string; v: number; sub?: string }[]; fmt?: (v: number) => string }) {
  const max = Math.max(1, ...rows.map((r) => r.v));
  if (!rows.length) return <Empty text="Нет данных" />;
  return (
    <div className="bars">
      {rows.map((r) => (
        <div key={r.k} className="bar-row" title={`${r.k}: ${fmt(r.v)}${r.sub ? ` · ${r.sub}` : ''}`}>
          <span className="ellipsis" style={{ fontWeight: 600 }}>{r.k}</span>
          <div className="bar-track"><div style={{ width: `${(r.v / max) * 100}%` }} /></div>
          <span className="num muted nowrap" style={{ fontSize: 12 }}><b style={{ color: 'var(--ink)' }}>{fmt(r.v)}</b>{r.sub ? ` · ${r.sub}` : ''}</span>
        </div>
      ))}
    </div>
  );
}
function VBars({ rows, fmt = num }: { rows: { k: string; v: number }[]; fmt?: (v: number) => string }) {
  const max = Math.max(1, ...rows.map((r) => r.v));
  if (!rows.length) return <Empty text="Нет данных" />;
  return (
    <div className="vbars" role="img" aria-label={rows.map((r) => `${r.k} ${fmt(r.v)}`).join(', ')}>
      {rows.map((r) => (
        <div key={r.k} className="vbar" title={`${r.k}: ${fmt(r.v)}`}>
          {r.v > 0 && <em>{fmt(r.v)}</em>}
          <div style={{ height: `${(r.v / max) * 100}%` }} />
          <span>{r.k}</span>
        </div>
      ))}
    </div>
  );
}

export function DealsAnalytics() {
  const [period, setPeriod] = useState<'month' | 'q' | 'all'>('month');
  const dash = useQuery({ queryKey: ['dashboard'], queryFn: api.analytics.dashboard });
  const months = prevMonths(3).slice(1);
  const prev = useQueries({ queries: months.map((m) => ({ queryKey: ['dashboard', m], queryFn: () => http.get<Raw>('/analytics/dashboard', { month: m }), enabled: period === 'q' })) });
  const listings = useQuery({ queryKey: ['analytics', 'listings'], queryFn: api.analytics.listings, retry: false });
  const settings = useQuery({ queryKey: ['settings'], queryFn: api.settings.get, retry: false });
  const metrics = parseMetrics(settings.data?.dashboard_metrics);
  const sections = parseMetrics(settings.data?.dashboard_sections);
  const showM = (k: string) => !metrics || Object.keys(metrics).every((x) => !x.startsWith('an_')) || !!metrics[k];
  const showS = (k: string) => !sections || !!sections[k];
  const [week, setWeek] = useState<0 | 1>(0);

  const raw = dash.data as unknown as Raw | undefined;
  const summary: Summary | undefined = period === 'month' ? raw?.deals_month : period === 'all' ? raw?.deals_all : sumSummaries([raw?.deals_month, ...prev.map((p) => p.data?.deals_month)]);
  const lb = period === 'month' ? raw?.leaderboard || [] : period === 'all' ? raw?.leaderboard_all || [] : mergeLB([raw?.leaderboard, ...prev.map((p) => p.data?.leaderboard)]);

  const L = listings.data;
  const districts = (L?.listings.by_district || []).slice(0, 10).map((x) => ({ k: x.district, v: x.count, sub: x.avg_price ? `ср. ${money(x.avg_price)} · ${money(x.min_price)}–${money(x.max_price)}` : undefined }));
  const types = Object.entries(L?.active.by_rooms || {}).sort((x, y) => y[1] - x[1]).map(([k, v]) => ({ k, v }));
  const wk = week === 0 ? L?.weekly.current : L?.weekly.last_week;
  const weekly = (wk?.top_agents || []).map((x) => ({ k: x.name, v: x.count }));

  if (dash.isLoading) return <Loading />;
  if (dash.error) return <ErrorBox error={dash.error} />;
  const s = summary;
  return (
    <div className="col gap-lg">
      <div className="row row-between row-wrap">
        <Seg value={period} onChange={setPeriod} options={[{ value: 'month', label: 'Месяц' }, { value: 'q', label: '3 мес.' }, { value: 'all', label: 'Всё' }]} />
        {period === 'q' && prev.some((p) => p.isLoading) && <span className="muted" style={{ fontSize: 12 }}>Загружаем прошлые месяцы</span>}
      </div>
      <div className="grid g4">
        {showM('an_deals') && <Stat blue label="Сделок" value={num(s?.total)} sub={period === 'month' ? 'с начала месяца' : period === 'q' ? 'за 3 месяца' : 'за всё время'} />}
        {showM('an_platform') && <Stat label="Платформа" value={money(s?.platform)} sub="комиссия" />}
        {showM('an_commission') && <Stat label="Общая комиссия" value={money(s?.commission)} sub="агентства" />}
        {showM('an_avg') && <Stat label="Ср. комиссия" value={money(s?.avg_commission)} sub="за сделку" />}
        {showM('an_turnover') && <Stat label="Оборот" value={money(s?.price)} sub="сумма сделок" />}
        {showM('an_objects') && <Stat label="Объектов" value={num(L?.active.total ?? raw?.channel_active_listings)} sub="активных в канале" />}
        {showM('an_avg_price') && <Stat label="Ср. цена" value={L?.active.avg_price ? money(L.active.avg_price) : '—'} sub="аренда, активные" />}
        {showM('an_exclusive') && <Stat label="Эксклюзив" value={L ? `${L.active.excl_pct}%` : '—'} sub="от активных" />}
      </div>
      <div className="grid g2">
        {showS('districts') && <Card label="Канал и объекты" title="Районы: объекты и цены">{listings.isLoading ? <Loading /> : listings.error ? <ErrorBox error={listings.error} /> : <Bars rows={districts} />}</Card>}
        {showS('types') && <Card label="Канал и объекты" title="Тип квартир">{listings.isLoading ? <Loading /> : <Bars rows={types} />}</Card>}
        {showS('weekly') && (
          <Card label="Канал и объекты" title={`Объекты за неделю${wk ? ` · с ${wk.week_start}, всего ${wk.total}` : ''}`} actions={<Seg value={week} onChange={setWeek} options={[{ value: 0, label: 'Эта' }, { value: 1, label: 'Прошлая' }]} />}>
            {listings.isLoading ? <Loading /> : <Bars rows={weekly} />}
          </Card>
        )}
        {showS('monthly') && (
          <Card label="Платформа и сделки" title="Комиссия платформы по месяцам">
            <VBars rows={(raw?.monthly || []).map((m) => ({ k: shortMonth(m.month), v: m.platform }))} fmt={(v) => (v >= 1000 ? `$${(v / 1000).toFixed(1)}k` : `$${v}`)} />
          </Card>
        )}
      </div>
      {showS('agents') && (
        <Card label="Платформа и сделки" title="Агенты: заработок за период" pad={false}>
          {lb.length === 0 ? <Empty /> : (
            <div className="table-wrap">
              <table className="table">
                <thead><tr><th>Агент</th><th style={{ textAlign: 'right' }}>Агент $</th><th style={{ textAlign: 'right' }}>Платф. $</th><th style={{ textAlign: 'right' }}>Сд.</th></tr></thead>
                <tbody>{lb.map((r) => (
                  <tr key={r.id}><td><b>{r.name}</b></td><td className="num" style={{ textAlign: 'right' }}>{money(r.agent_amount)}</td><td className="num" style={{ textAlign: 'right' }}>{money(r.platform_amount)}</td><td className="num" style={{ textAlign: 'right' }}>{r.deals}</td></tr>
                ))}</tbody>
              </table>
            </div>
          )}
        </Card>
      )}
      {showS('last') && (
        <Card label="Платформа и сделки" title="Последние сделки" pad={false}>
          {(raw?.last_deals || []).length === 0 ? <Empty /> : (
            <div className="table-wrap">
              <table className="table">
                <thead><tr><th>Дата</th><th>Агент</th><th style={{ textAlign: 'right' }}>Цена</th><th style={{ textAlign: 'right' }}>Комиссия</th><th style={{ textAlign: 'right' }}>Платформа</th><th /></tr></thead>
                <tbody>{(raw?.last_deals || []).map((d) => (
                  <tr key={d.id}><td className="num">{d.date}</td><td>{d.agent || '—'}</td><td className="num" style={{ textAlign: 'right' }}>{money(d.price)}</td><td className="num" style={{ textAlign: 'right' }}>{money(d.commission)}</td><td className="num" style={{ textAlign: 'right' }}>{money(d.platform)}</td>
                    <td>{d.exclusive && <Badge>эксклюзив</Badge>} {d.status === 'pending' && <Badge tone="warn">ждёт</Badge>}</td></tr>
                ))}</tbody>
              </table>
            </div>
          )}
        </Card>
      )}
    </div>
  );
}
