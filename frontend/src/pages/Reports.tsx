import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../api';
import { useSearchParams } from 'react-router-dom';
import type { ReportKind } from '../api/types';
import { Card, Drawer, Empty, ErrorBox, Loading, PageHead, Progress, Seg, Tabs } from '../components/ui';
import { fmtDateTime } from '../lib/format';
import { DealsAnalytics } from '../features/deals/Analytics';
import { DealView, Expiring } from './Deals';
import { KIND_LABEL, REPORT_KINDS, ReportForm, ReportsList } from '../features/reports/ReportForms';
import { PropertyCheck } from './Tools';

export function ReportKindPicker({ value, onChange }: { value: ReportKind; onChange: (k: ReportKind) => void }) {
  return (
    <div className="grid" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(120px, 1fr))', marginBottom: 16 }}>
      {REPORT_KINDS.map((k) => (
        <button key={k.kind} type="button" className={`card card-pad col gap-sm ${value === k.kind ? 'card-blue' : ''}`}
          style={{ cursor: 'pointer', alignItems: 'flex-start', color: value === k.kind ? '#fff' : 'var(--brand)', padding: 14 }} onClick={() => onChange(k.kind)}>
          <k.icon size={20} />
          <span style={{ fontWeight: 800, fontSize: 13, color: value === k.kind ? '#fff' : 'var(--ink)' }}>{k.label}</span>
        </button>
      ))}
    </div>
  );
}

export function ReportsPage() {
  const [params] = useSearchParams();
  return <ReportsInner key={params.toString()} />;
}

function ReportsInner() {
  const [params] = useSearchParams();
  const qKind = params.get('kind') as ReportKind | null;
  type Tab = 'new' | 'list' | 'check' | 'expiring' | 'deals' | 'posts';
  const [tab, setTab] = useState<Tab>((params.get('tab') as Tab) || 'new');
  const [deal, setDeal] = useState<number | null>(null);
  const [kind, setKind] = useState<ReportKind>(qKind && REPORT_KINDS.some((k) => k.kind === qKind) ? qKind : 'photo');
  const [filter, setFilter] = useState<string>('');
  return (
    <>
      <PageHead label="Фото, показы, расклейка, сдачи — то, что агенты раньше слали в рабочую группу" title="Отчёты" />
      <Tabs value={tab} onChange={setTab} tabs={[{ value: 'new', label: 'Новый отчёт' }, { value: 'list', label: 'Журнал' }, { value: 'check', label: 'Проверка объекта' },
        { value: 'expiring', label: 'Истекают договоры' }, { value: 'deals', label: 'Аналитика сделок' }, { value: 'posts', label: 'Посты в канал' }]} />
      <Drawer open={!!deal} onClose={() => setDeal(null)} title={`Сделка #${deal}`}>{deal && <DealView id={deal} onClose={() => setDeal(null)} />}</Drawer>
      {tab === 'posts' ? <ChannelPosts /> : tab === 'expiring' ? <Expiring onOpen={setDeal} /> : tab === 'deals' ? <DealsAnalytics /> : tab === 'check' ? <PropertyCheck /> : tab === 'new' ? (
        <div style={{ maxWidth: 760 }}>
          <ReportKindPicker value={kind} onChange={setKind} />
          <Card label="Отчёт" title={KIND_LABEL[kind]}><ReportForm key={kind} kind={kind} /></Card>
        </div>
      ) : (
        <>
          <select className="select" style={{ width: 'auto', marginBottom: 14 }} value={filter} onChange={(e) => setFilter(e.target.value)}>
            <option value="">Все типы</option>
            {REPORT_KINDS.map((k) => <option key={k.kind} value={k.kind}>{k.label}</option>)}
          </select>
          <ReportsList kind={filter || undefined} />
        </>
      )}
    </>
  );
}

const iso = (d: Date) => d.toLocaleDateString('sv');
/** The period's [from, to] in local (Tbilisi) days. */
function period(p: 'today' | 'week' | 'month' | 'last'): [string, string] {
  const t = new Date();
  if (p === 'today') return [iso(t), iso(t)];
  if (p === 'week') { const m = new Date(t); m.setDate(t.getDate() - ((t.getDay() + 6) % 7)); return [iso(m), iso(t)]; }
  if (p === 'month') return [iso(new Date(t.getFullYear(), t.getMonth(), 1)), iso(t)];
  return [iso(new Date(t.getFullYear(), t.getMonth() - 1, 1)), iso(new Date(t.getFullYear(), t.getMonth(), 0))];
}

/** «Посты в канал» (Stanislav 11-13): who posted how many objects to the channel over a period, by the post's hashtag. */
function ChannelPosts() {
  const [p, setP] = useState<'today' | 'week' | 'month' | 'last' | 'custom'>('month');
  const [range, setRange] = useState<[string, string]>(period('month'));
  const pick = (v: typeof p) => { setP(v); if (v !== 'custom') setRange(period(v)); };
  const { data, isLoading, error } = useQuery({ queryKey: ['analytics', 'channel-posts', range], queryFn: () => api.analytics.channelPosts(range[0], range[1]) });
  const max = Math.max(1, ...(data?.items || []).map((x) => x.posts));
  return (
    <div className="col gap-lg" style={{ maxWidth: 760 }}>
      <Seg value={p} onChange={pick} options={[{ value: 'today', label: 'Сегодня' }, { value: 'week', label: 'Неделя' }, { value: 'month', label: 'Месяц' }, { value: 'last', label: 'Прошлый месяц' }, { value: 'custom', label: 'Даты' }]} />
      {p === 'custom' && (
        <div className="row gap-sm">
          <input className="input" type="date" value={range[0]} max={range[1]} onChange={(e) => setRange([e.target.value, range[1]])} />
          <span className="muted">—</span>
          <input className="input" type="date" value={range[1]} min={range[0]} onChange={(e) => setRange([range[0], e.target.value])} />
        </div>
      )}
      {isLoading ? <Loading /> : error ? <ErrorBox error={error} /> : !data?.items.length ? <Empty text="За этот период постов нет" /> : (
        <Card label={`${range[0].split('-').reverse().join('.')} – ${range[1].split('-').reverse().join('.')}`} title={`Всего постов: ${data.total}`}>
          <div className="col gap-sm">
            {data.items.map((x, i) => (
              <div key={`${x.agent_id}-${x.name}`} className="col" style={{ gap: 4 }}>
                <div className="row row-between" style={{ fontSize: 14 }}>
                  <span><span className="faint">{i + 1}.</span> <b>{x.name}</b>{!x.agent_id && <span className="faint" style={{ fontSize: 12 }}> · хэштег без агента в CRM</span>}</span>
                  <span><b className="num">{x.posts}</b>{x.exclusive ? <span className="muted" style={{ fontSize: 12 }}> · эксклюзивов {x.exclusive}</span> : null}</span>
                </div>
                <Progress value={x.posts} max={max} />
                {x.last && <span className="faint" style={{ fontSize: 11 }}>последний {fmtDateTime(x.last)}</span>}
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}
