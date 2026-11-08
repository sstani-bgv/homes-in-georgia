import { useState } from 'react';
import { Link, useLocation, useNavigate, useSearchParams } from 'react-router-dom';
import { DealsAnalytics } from '../features/deals/Analytics';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CheckCircle2, FileText, Plus, XCircle } from 'lucide-react';
import { api } from '../api';
import { itemsOf } from '../api/client';
import type { Deal } from '../api/types';
import { useAuth } from '../auth/AuthContext';
import { errText, useToast } from '../components/toast';
import { Badge, Button, Card, Drawer, Empty, ErrorBox, Field, KV, Loading, PageHead, Seg, Select, Stat, Switch, Tabs, Textarea } from '../components/ui';
import { DealForm, H2O } from '../features/deals/DealForm';
import { fmtDate, fmtDateTime, lastMonths, money, monthKey, monthLabel } from '../lib/format';
import { useUserNames } from '../lib/useUserNames';

const STATUS: Record<string, string> = { pending: 'Ждёт подтверждения', confirmed: 'Подтверждена', cancelled: 'Отменена' };
export function DealStatusBadge({ s }: { s: string }) {
  return <Badge tone={s === 'confirmed' ? 'ok' : s === 'pending' ? 'warn' : 'grey'}>{STATUS[s] || s}</Badge>;
}

function EvalForm({ deal, kind, onDone }: { deal: Deal; kind: 't0' | 't14'; onDone: () => void }) {
  const toast = useToast();
  const [v, setV] = useState<{ c: number | null; o: number | null; p: number | null; result: 'clean' | 'dirty'; changes: string; complaints: boolean; complaints_text: string; comment: string }>({
    c: null, o: null, p: null, result: 'clean', changes: '', complaints: false, complaints_text: '', comment: '',
  });
  const m = useMutation({
    mutationFn: () => api.evaluations.create({
      deal_id: deal.id, kind, author: 'agent', h2o_client: v.c, h2o_owner: v.o, h2o_process: v.p,
      result: kind === 't14' ? v.result : null, changes: v.changes || null, complaints: v.complaints, complaints_text: v.complaints_text || null, comment: v.comment || null,
    }),
    onSuccess: () => { toast(kind === 't14' ? 'День 14 сохранён' : 'День 0 сохранён'); onDone(); },
    onError: (e) => toast(errText(e), true),
  });
  return (
    <div className="col">
      <H2O label="Клиент" value={v.c} onChange={(c) => setV({ ...v, c })} />
      <H2O label="Собственник" value={v.o} onChange={(o) => setV({ ...v, o })} />
      <H2O label="Процесс" value={v.p} onChange={(p) => setV({ ...v, p })} />
      {kind === 't14' && <>
        <Field label="Изменения после заселения"><Textarea value={v.changes} onChange={(e) => setV({ ...v, changes: e.target.value })} /></Field>
        <Switch checked={v.complaints} onChange={(complaints) => setV({ ...v, complaints })} label="Есть жалобы" />
        {v.complaints && <Field label="Жалобы"><Textarea value={v.complaints_text} onChange={(e) => setV({ ...v, complaints_text: e.target.value })} /></Field>}
        <div className="row row-between"><span style={{ fontWeight: 600 }}>Итог сделки</span>
          <Seg value={v.result} onChange={(result) => setV({ ...v, result })} options={[{ value: 'clean', label: 'Чистая' }, { value: 'dirty', label: 'Грязная' }]} /></div>
      </>}
      <Field label="Комментарий"><Textarea value={v.comment} onChange={(e) => setV({ ...v, comment: e.target.value })} /></Field>
      {[v.c, v.o, v.p].some((x) => x !== null && x <= 1) && <div className="alert alert-warn">Низкая оценка · операторы получат уведомление</div>}
      <Button loading={m.isPending} onClick={() => m.mutate()}>Сохранить {kind.toUpperCase()}</Button>
    </div>
  );
}

export function DealView({ id, onClose, initialEval }: { id: number; onClose: () => void; initialEval?: 't0' | 't14' | null }) {
  const { isStaff } = useAuth();
  const loc = useLocation();
  const qc = useQueryClient();
  const toast = useToast();
  const { data: d, isLoading, error } = useQuery({ queryKey: ['deal', id], queryFn: () => api.deals.get(id) });
  const evals = useQuery({ queryKey: ['evals', id], queryFn: () => api.evaluations.list(id) });
  const [evalKind, setEvalKind] = useState<'t0' | 't14' | null>(initialEval ?? null);
  const nameOf = useUserNames();
  const refresh = () => { qc.invalidateQueries({ queryKey: ['deal', id] }); qc.invalidateQueries({ queryKey: ['deals'] }); qc.invalidateQueries({ queryKey: ['evals', id] }); };
  const confirm = useMutation({ mutationFn: () => api.deals.confirm(id), onSuccess: () => { refresh(); qc.invalidateQueries({ queryKey: ['lead'] }); qc.invalidateQueries({ queryKey: ['leads'] }); toast('Сделка подтверждена'); }, onError: (e) => toast(errText(e), true) });
  const cancel = useMutation({ mutationFn: () => api.deals.cancel(id), onSuccess: () => { refresh(); toast('Сделка отменена'); onClose(); }, onError: (e) => toast(errText(e), true) });
  if (error) return <ErrorBox error={error} />;
  if (isLoading || !d) return <Loading />;
  return (
    <div className="col gap-lg">
      <div className="row row-between"><DealStatusBadge s={d.status} />{d.is_exclusive && <Badge tone="solid">эксклюзив</Badge>}</div>
      <div className="calc-box">
        <div className="calc-row"><span>Цена</span><strong>{money(d.price)}</strong></div>
        <div className="calc-row"><span>Комиссия ({d.commission_pct ?? '—'}%)</span><strong>{money(d.commission_total)}</strong></div>
        <div className="calc-row"><span>Платформа</span><strong>{money(d.platform_amount)}</strong></div>
        {Number(d.recruit_amount) > 0 && <div className="calc-row"><span>Рекрут {d.recruit_id ? nameOf(d.recruit_id, d.recruit?.name) : ''}</span><strong>{money(d.recruit_amount)}</strong></div>}
        {Number(d.client_src_amount) > 0 && <div className="calc-row"><span>Источник: {d.client_src}</span><strong>{money(d.client_src_amount)}</strong></div>}
        <div className="calc-row"><span>Агент ({d.agent_level}%)</span><strong>{money(d.agent_amount)}</strong></div>
      </div>
      {d.property?.needs_check && <div className="alert alert-warn">Новый объект из сделки — проверить. {isStaff ? 'Сделку можно подтвердить после проверки объекта.' : 'Сделку подтвердят после проверки объекта оператором.'}</div>}
      <KV items={[
        ['Объект', d.property ? <Link to={`${loc.pathname.startsWith('/m/') ? '/m/properties' : '/objects'}/${d.property.id}`}>#{d.property.id} · {d.property.address || 'без адреса'}</Link> : '—'],
        ['Агент', nameOf(d.agent_id, d.agent?.name)], ['Тип', `${d.deal_type === 'sale' ? 'Продажа' : 'Аренда'}${d.sub_type ? ` · ${d.sub_type}` : ''}`], ['Адрес', d.address],
        ['Клиент', [d.client_name, d.client_phone, d.client_tg].filter(Boolean).join(' · ') || '—'], ['Собственник', [d.owner_name, d.owner_phone].filter(Boolean).join(' · ') || '—'],
        ['Аренда', d.rent_from ? `${fmtDate(d.rent_from)} — ${fmtDate(d.rent_to)}` : '—'], ['День 14', d.t14_date ? `${fmtDate(d.t14_date)} · ${d.t14_status || ''}` : '—'],
        ['Создана', fmtDateTime(d.created_at)], ['Заявка', d.lead_id ? `#${d.lead_id}` : '—'],
      ]} />
      <Link to={`${loc.pathname.startsWith('/m/') ? '/m' : ''}/contracts?deal_id=${d.id}`} className="btn btn-secondary"><FileText size={16} />Подготовить договор</Link>
      {isStaff && d.status === 'pending' && (
        <div className="row row-wrap">
          <Button variant="ok" loading={confirm.isPending} disabled={!!d.property?.needs_check} onClick={() => confirm.mutate()}><CheckCircle2 size={16} />Подтвердить сделку</Button>
          <Button variant="danger" loading={cancel.isPending} onClick={() => cancel.mutate()}><XCircle size={16} />Отменить</Button>
        </div>
      )}
      <Card label="Оценки" title="День 0 / День 14" actions={<>
        <Button size="sm" variant="secondary" onClick={() => setEvalKind('t0')}>День 0</Button>
        <Button size="sm" variant="secondary" onClick={() => setEvalKind('t14')}>День 14</Button>
      </>}>
        {itemsOf(evals.data).length === 0 ? <div className="muted">Оценок пока нет</div> : itemsOf(evals.data).map((e) => (
          <div key={e.id} className="list-row">
            <Badge tone={e.kind === 't14' ? 'solid' : 'blue'}>{e.kind}</Badge>
            <span className="grow">{e.author === 'client' ? 'Клиент' : 'Агент'} · H2O <b className="num">{e.h2o_client ?? '–'}/{e.h2o_owner ?? '–'}/{e.h2o_process ?? '–'}</b>{e.comment ? ` · ${e.comment}` : ''}</span>
            {e.result && <Badge tone={e.result === 'clean' ? 'ok' : 'red'}>{e.result === 'clean' ? 'чистая' : 'грязная'}</Badge>}
          </div>
        ))}
        {evalKind && <div className="mt-lg"><EvalForm deal={d} kind={evalKind} onDone={() => { setEvalKind(null); refresh(); }} /></div>}
      </Card>
    </div>
  );
}

function DealsList({ onOpen }: { onOpen: (id: number) => void }) {
  const { isStaff } = useAuth();
  const [month, setMonth] = useState(monthKey());
  const [agent, setAgent] = useState('');
  const [status, setStatus] = useState('');
  const agents = useQuery({ queryKey: ['users', 'agents'], queryFn: () => api.users.list({ role: 'agent,recruit', active: true }), enabled: isStaff });
  const { data, isLoading, error } = useQuery({ queryKey: ['deals', month, agent, status], queryFn: () => api.deals.list({ month, agent_id: agent ? Number(agent) : undefined, status }) });
  const items = itemsOf(data);
  const nameOf = useUserNames();
  const confirmed = items.filter((d) => d.status === 'confirmed');
  return (
    <>
      <div className="row row-wrap" style={{ marginBottom: 14 }}>
        <select className="select" style={{ width: 'auto' }} value={month} onChange={(e) => setMonth(e.target.value)}>
          <option value="">Все месяцы</option>
          {lastMonths(18).map((m) => <option key={m} value={m}>{monthLabel(m)}</option>)}
        </select>
        {isStaff && <div style={{ width: 200 }}><Select value={agent} onChange={(e) => setAgent(e.target.value)} placeholder="Все агенты" options={itemsOf(agents.data).map((a) => ({ value: a.id, label: a.name }))} /></div>}
        <Seg value={status} onChange={setStatus} options={[{ value: '', label: 'Все' }, { value: 'pending', label: 'Ждут' }, { value: 'confirmed', label: 'Подтв.' }, { value: 'cancelled', label: 'Отмена' }]} />
      </div>
      <div className="grid g3" style={{ marginBottom: 14 }}>
        <Stat label="Подтверждено" value={confirmed.length} sub={`ждут: ${items.filter((d) => d.status === 'pending').length}`} />
        <Stat label="Комиссия" value={money(confirmed.reduce((s, d) => s + Number(d.commission_total || 0), 0))} />
        <Stat blue label="Платформа" value={money(confirmed.reduce((s, d) => s + Number(d.platform_amount || 0), 0))} />
      </div>
      {isLoading ? <Loading /> : error ? <ErrorBox error={error} /> : items.length === 0 ? <Empty text="Сделок нет" /> : (
        <div className="card table-wrap">
          <table className="table">
            <thead><tr><th>#</th><th>Дата</th><th>Агент</th><th>Адрес</th><th>Цена</th><th className="hide-mobile">Комиссия</th><th className="hide-mobile">Агенту</th><th>Статус</th></tr></thead>
            <tbody>
              {items.map((d) => (
                <tr key={d.id} className="clickable" onClick={() => onOpen(d.id)}>
                  <td className="muted num">{d.id}</td>
                  <td className="nowrap">{fmtDate(d.created_at)}</td>
                  <td>{nameOf(d.agent_id, d.agent?.name)}</td>
                  <td>{d.address || '—'} {d.is_exclusive && <Badge tone="solid">экс</Badge>}</td>
                  <td className="num" style={{ fontWeight: 700 }}>{money(d.price)}</td>
                  <td className="num hide-mobile">{money(d.commission_total)}</td>
                  <td className="num hide-mobile">{money(d.agent_amount)}</td>
                  <td><DealStatusBadge s={d.status} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

export function Expiring({ onOpen }: { onOpen: (id: number) => void }) {
  const { data, isLoading, error } = useQuery({ queryKey: ['deals', 'expiring'], queryFn: () => api.deals.expiring(30) });
  const items = itemsOf(data);
  const nameOf = useUserNames();
  if (isLoading) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  if (!items.length) return <Empty text="В ближайшие 30 дней договоры не заканчиваются" />;
  return (
    <div className="grid" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))' }}>
      {items.map((d) => {
        const days = d.days_left ?? Math.ceil((new Date(d.rent_to || '').getTime() - Date.now()) / 86400_000);
        return (
          <button key={d.id} className="card card-pad" style={{ textAlign: 'left', cursor: 'pointer' }} onClick={() => onOpen(d.id)}>
            <div className="row row-between"><span className="label">Договор до {fmtDate(d.rent_to)}</span>
              <Badge tone={days <= 1 ? 'red' : days <= 7 ? 'warn' : 'blue'}>{days} дн</Badge></div>
            <h3 className="mt">{d.address || `Сделка #${d.id}`}</h3>
            <div className="muted" style={{ fontSize: 13 }}>{nameOf(d.agent_id, d.agent?.name)} · {d.client_name || 'клиент'} · {money(d.price)}</div>
          </button>
        );
      })}
    </div>
  );
}

export function DealsPage({ newDeal }: { newDeal?: boolean }) {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const tab = (params.get('tab') as 'list' | 'expiring' | 'analytics') || 'list';
  const setTab = (t: string) => { if (t === 'list') params.delete('tab'); else params.set('tab', t); setParams(params, { replace: true }); };
  const open = Number(params.get('open')) || null;
  const setOpen = (id: number | null) => { if (id) params.set('open', String(id)); else params.delete('open'); setParams(params, { replace: true }); };
  const leadId = Number(params.get('lead_id') || params.get('lead')) || undefined;

  if (newDeal) {
    return (
      <>
        <PageHead label="Сделки · C9b" title="Форма сделки" />
        <DealForm leadId={leadId} onDone={(d) => navigate(`/deals?open=${d.id}`)} />
      </>
    );
  }
  return (
    <>
      <PageHead title="Сделки" actions={<Button onClick={() => navigate('/deals/new')}><Plus size={16} />Новая сделка</Button>} />
      <Tabs value={tab} onChange={setTab} tabs={[{ value: 'list', label: 'Сделки' }, { value: 'expiring', label: 'Истекают договоры' }, { value: 'analytics', label: 'Аналитика' }]} />
      {tab === 'list' ? <DealsList onOpen={setOpen} /> : tab === 'expiring' ? <Expiring onOpen={setOpen} /> : <DealsAnalytics />}
      <Drawer open={!!open} onClose={() => setOpen(null)} title={`Сделка #${open}`}>{open && <DealView id={open} onClose={() => setOpen(null)} />}</Drawer>
    </>
  );
}
