// C9b deal form with live commission calc (/api/deals/calc) and T0 evaluation (as in the old #Rented form).
import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CheckCircle2, ExternalLink, Search } from 'lucide-react';
import { Link, useLocation } from 'react-router-dom';
import { api } from '../../api';
import { ApiError, itemsOf } from '../../api/client';
import type { Deal, DealCalcIn } from '../../api/types';
import { FIELD_FILTER } from '../../api/types';
import { useAuth } from '../../auth/AuthContext';
import { errText, useToast } from '../../components/toast';
import { PropertyStatusBadge } from '../../components/domain';
import { Badge, Button, Card, Field, Input, Seg, Select, Switch, Textarea } from '../../components/ui';
import { OwnerSuggest } from '../../components/OwnerSuggest';
import { levelFor, money, phoneInput, todayStr } from '../../lib/format';
import { AXES, AXIS_NOTE, PARTICIPATE, PEOPLE_HINT, defaultScenario, peopleRule, type Axis, type DistRow, type Scenario } from '../../lib/scenarios';
import { cadastralError, cadastralHint, cadastralInput, cadastralNorm } from '../../lib/cadastral';

function useDebounced<T>(v: T, ms = 300): T {
  const [d, setD] = useState(v);
  useEffect(() => { const t = setTimeout(() => setD(v), ms); return () => clearTimeout(t); }, [v, ms]);
  return d;
}

export function H2O({ label, value, onChange }: { label: string; value: number | null; onChange: (v: number) => void }) {
  return (
    <div className="row row-between row-wrap">
      <span style={{ fontWeight: 600 }}>{label}</span>
      <Seg value={value ?? -1} onChange={onChange} options={[0, 1, 2, 3].map((n) => ({ value: n, label: String(n) }))} />
    </div>
  );
}

/** A $ field that commits on blur / Enter (the calc behind it is debounced; typing must not be overwritten). */
function AmountInput({ value, onCommit, label }: { value: number | null | undefined; onCommit: (v: number) => void; label: string }) {
  const [s, setS] = useState('');
  const [focus, setFocus] = useState(false);
  const shown = focus ? s : value == null ? '' : money(value);
  const commit = () => { setFocus(false); const n = Number(s.replace(',', '.')); if (s.trim() !== '' && Number.isFinite(n) && n >= 0 && n !== value) onCommit(n); };
  return (
    <input className="deal-money-edit num" aria-label={label} title="Нажмите, чтобы изменить" inputMode="decimal" value={shown}
      onFocus={() => { setFocus(true); setS(value == null ? '' : String(value)); }} onChange={(e) => setS(e.target.value)}
      onBlur={commit} onKeyDown={(e) => e.key === 'Enter' && (e.target as HTMLInputElement).blur()} />
  );
}
const notSignedFlag = (status?: string, pre?: { contract: unknown }) => status === 'contract' && !!pre && !pre.contract;
const r2 = (x: number) => Math.round(x * 100) / 100;

// staff's manual rows → the API split: the platform is the remainder
const splitOf = (rows: DistRow[] | null) => rows && [...rows.filter((r) => r.who !== 'platform'), { who: 'platform' as const, role: 'Платформа', pct: 100 - rows.filter((r) => r.who !== 'platform').reduce((a, r) => a + r.pct, 0) }];

const SRC = ['', 'Instagram', 'Facebook', 'Telegram-канал', 'Рекомендация', 'Сайт', 'Другое'];

export function DealForm({ leadId, onDone }: { leadId?: number; onDone?: (d: Deal) => void }) {
  const { user, isStaff } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const agents = useQuery({ queryKey: ['users', 'agents'], queryFn: () => api.users.list({ role: FIELD_FILTER, active: true }), enabled: isStaff });
  const recruits = useQuery({ queryKey: ['users', 'recruits'], queryFn: () => api.users.list({ role: 'recruit', active: true }) });
  const lead = useQuery({ queryKey: ['lead', leadId], queryFn: () => api.leads.get(leadId!), enabled: !!leadId });
  // the signed contract's data: everything typed there comes into the deal (Stanislav 11-11)
  const pre = useQuery({ queryKey: ['deal-prefill', leadId], queryFn: () => api.leads.dealPrefill(leadId!), enabled: !!leadId });
  const prefilled = useRef(false);

  const [f, setF] = useState({
    agent_id: user?.id ?? 0, recruit_id: '' as string | number, deal_type: 'rent' as 'rent' | 'sale', sub_type: 'long',
    is_exclusive: false, is_rerented: false, price: '' as string | number, commission_pct: 50, commission_total: '' as string | number,
    agent_level: user?.level || levelFor(user?.exclusive_deals || 0), recruit_pct: 0, client_src: '',
    address: '', cadastral: '', ad_link: '', metro: '', client_name: '', client_phone: '', client_tg: '', owner_name: '', owner_phone: '', owner_id: 0, owner_typed: false,
    rent_from: todayStr(), rent_to: '',
  });
  // Molecula scenario (null = the classic level scheme with recruit % and client source) and staff's manual split of the rows
  const [sc, setSc] = useState<Scenario | null>(null);
  const [edit, setEdit] = useState<DistRow[] | null>(null);
  const setAxis = (a: Axis) => { setSc(sc?.axis === a ? null : defaultScenario(a)); setEdit(null); };
  const patchSc = (p: Partial<Scenario>) => { setSc((x) => (x ? { ...x, ...p } : x)); setEdit(null); };
  const [t0, setT0] = useState<{ c: number | null; o: number | null; p: number | null; comment: string }>({ c: null, o: null, p: null, comment: '' });
  const upd = (k: keyof typeof f, v: unknown) => setF((x) => ({ ...x, [k]: v }));
  // stage 7: the object is picked from the base (its fields become read-only) or typed by hand as a new object
  const loc = useLocation();
  const [pid, setPid] = useState<number | null>(null);
  const [manual, setManual] = useState(false);
  const [q, setQ] = useState('');
  const [exists, setExists] = useState<number | null>(null);
  const dq = useDebounced(q.trim());
  const found = useQuery({ queryKey: ['deal-search', dq, leadId], queryFn: () => api.properties.dealSearch({ q: dq, lead_id: leadId }), enabled: !pid && !manual && !!dq });
  const picked = useQuery({ queryKey: ['deal-pick', pid, f.deal_type, leadId], queryFn: () => api.properties.dealSearch({ pid: pid!, deal_type: f.deal_type, lead_id: leadId }), enabled: !!pid });
  const obj = pid ? picked.data?.items[0] : undefined;
  const ro = (k: string) => !!obj?.fields && k in obj.fields;
  useEffect(() => { if (obj?.fields) setF((x) => ({ ...x, ...obj.fields })); }, [obj]);
  const pick = (id: number | null) => {
    if (obj?.fields) setF((x) => ({ ...x, ...Object.fromEntries(Object.keys(obj.fields!).map((k) => [k, k === 'is_exclusive' ? false : ''])) }));
    setPid(id); setManual(false); setExists(null);
  };
  const objLink = (id: number) => (loc.pathname.startsWith('/m/') ? `/m/properties/${id}` : `/objects/${id}`);

  useEffect(() => {
    const l = lead.data;
    if (!l || pre.isLoading || prefilled.current) return;
    prefilled.current = true;  // once: a refetch must not wipe what was typed
    const { terms: _t, client_birth: _cb, owner_birth: _ob, ...cx } = pre.data?.fields || {};
    const sub = cx.sub_type || 'long';
    setF((x) => ({
      ...x,
      agent_id: l.agent?.id ?? l.agent_id ?? x.agent_id,
      price: l.listing?.price ?? x.price,
      address: l.listing?.address || x.address,
      client_name: l.client?.name || x.client_name,
      client_phone: l.client?.phone || x.client_phone,
      client_tg: l.client?.username ? `@${l.client.username}` : x.client_tg,
      ad_link: l.listing?.url || x.ad_link,
      ...Object.fromEntries(Object.entries(cx).filter(([, v]) => v != null && v !== '')),
      sub_type: sub,
      commission_pct: cx.deal_type === 'sale' ? 3 : sub === 'commercial' ? 100 : x.commission_pct,
    }));
    // a lead on a channel post usually has no object in the base yet: start with the object typed by hand (from the ad)
    if (l.property_id) setPid(l.property_id); else setManual(true);
  }, [lead.data, pre.data, pre.isLoading]);

  const agentList = itemsOf(agents.data);
  useEffect(() => {
    const a = agentList.find((u) => u.id === Number(f.agent_id));
    if (a) upd('agent_level', a.level || levelFor(a.exclusive_deals));
  }, [f.agent_id, agents.data]); // eslint-disable-line react-hooks/exhaustive-deps

  const calcIn: DealCalcIn = useMemo(() => ({
    price: Number(f.price) || 0, commission_pct: Number(f.commission_pct), commission_total: f.commission_total === '' ? null : Number(f.commission_total),
    agent_level: Number(f.agent_level), is_exclusive: f.is_exclusive, recruit_pct: f.recruit_id && !sc ? Number(f.recruit_pct) : 0,
    client_src: sc ? null : f.client_src || null, deal_type: f.deal_type, is_rerented: f.is_rerented,
    scenario: sc, split: splitOf(edit),
  }), [f, sc, edit]);
  const dIn = useDebounced(calcIn);
  const debouncing = JSON.stringify(calcIn) !== JSON.stringify(dIn);
  const calc = useQuery({ queryKey: ['calc', dIn], queryFn: () => api.deals.calc(dIn), enabled: dIn.price > 0, placeholderData: (p) => p });

  const setType = (t: 'rent' | 'sale') => { upd('deal_type', t); upd('commission_pct', t === 'sale' ? 3 : f.sub_type === 'commercial' ? 100 : 50); };
  const setSub = (s: string) => { upd('sub_type', s); upd('commission_pct', s === 'commercial' ? 100 : 50); };

  const save = useMutation({
    mutationFn: async () => {
      // Only user inputs: amounts (platform/agent/recruit/client_src) are computed by the backend.
      const str = (v: string) => (v.trim() === '' ? null : v.trim());
      const body: Partial<Deal> & Record<string, unknown> = {
        lead_id: leadId ?? null, property_id: manual ? null : pid, new_object: manual,
        agent_id: Number(f.agent_id), recruit_id: f.recruit_id ? Number(f.recruit_id) : null,
        deal_type: f.deal_type, sub_type: f.deal_type === 'rent' ? f.sub_type : null,
        is_exclusive: f.is_exclusive, is_rerented: f.is_rerented,
        price: Number(f.price), commission_pct: Number(f.commission_pct),
        commission_total: f.commission_total === '' ? null : Number(f.commission_total),
        agent_level: Number(f.agent_level), recruit_pct: f.recruit_id && !sc ? Number(f.recruit_pct) : null,
        client_src: sc ? null : f.client_src || null, scenario: sc, split: splitOf(edit),
        address: str(f.address), cadastral: str(f.cadastral), ad_link: str(f.ad_link), metro: str(f.metro),
        client_name: str(f.client_name), client_phone: str(f.client_phone), client_tg: str(f.client_tg),
        owner_name: str(f.owner_name), owner_phone: str(f.owner_phone), owner_id: f.owner_id || null,
        rent_from: f.deal_type === 'rent' ? f.rent_from || null : null, rent_to: f.deal_type === 'rent' ? f.rent_to || null : null,
        client_birth: pre.data?.fields.client_birth || null, owner_birth: pre.data?.fields.owner_birth || null,
      };
      const d = await api.deals.create(body);
      if (t0.c !== null || t0.o !== null || t0.p !== null) {
        await api.evaluations.create({ deal_id: d.id, kind: 't0', author: 'agent', h2o_client: t0.c, h2o_owner: t0.o, h2o_process: t0.p, comment: t0.comment || null });
      }
      return d;
    },
    onSuccess: (d) => {
      qc.invalidateQueries({ queryKey: ['deals'] });
      qc.invalidateQueries({ queryKey: ['leads'] });
      if (leadId) qc.invalidateQueries({ queryKey: ['lead', leadId] });
      toast('Сделка отправлена оператору на подтверждение');
      onDone?.(d);
    },
    onError: (e) => {
      if (e instanceof ApiError && e.code === 'object_exists') setExists(Number(/#(\d+)/.exec(e.message)?.[1]) || null);
      toast(errText(e), true);
    },
  });

  const r = calc.data;
  const objectOk = manual ? !!f.address.trim() : !!obj && !obj.blocked;
  const rule = peopleRule(sc);
  const hasA = Number(f.agent_id) > 0, hasR = !!f.recruit_id;
  const peopleOk = !sc || (rule === 'both' ? hasA && hasR : rule === 'either' ? hasA || hasR : hasA);
  const splitSum = edit ? edit.filter((r) => r.who !== 'platform').reduce((a, r) => a + r.pct, 0) : 0;
  const valid = !edit?.some((x) => x.pct < 0) && !notSignedFlag(lead.data?.status, pre.data) && objectOk && Number(f.price) > 0 && Number(f.agent_id) > 0 && (f.deal_type === 'sale' || !!f.rent_from) && peopleOk && splitSum <= 100 && !cadastralError(f.cadastral, obj?.fields?.cadastral);
  const calcBusy = debouncing || calc.isFetching;
  const missing = [
    !manual && !pid && 'объект (найдите в базе или «Объекта нет в базе»)', manual && !f.address.trim() && 'адрес',
    !(Number(f.price) > 0) && 'цена', !peopleOk && (rule === 'both' ? 'агента и рекрута (второго участника)' : 'агента или второго участника'), splitSum > 100 && 'доли не больше 100%', edit?.some((x) => x.pct < 0) && 'доли не меньше 0', !(Number(f.agent_id) > 0) && 'агент', f.deal_type === 'rent' && !f.rent_from && 'дата начала аренды',
  ].filter(Boolean) as string[];

  const comm = r?.commission_total || 0;
  const badRows = !!edit?.some((x) => x.pct < 0);
  const distSum = r?.distribution ? r2(r.distribution.reduce((a, x) => a + x.pct, 0)) : 0;
  // staff types a row's $: its % of the commission; the platform takes the rest (or the agent, when the platform is typed)
  const setRowAmount = (i: number, amount: number) => {
    if (!r?.distribution || !comm) return;
    const rows = (edit ?? r.distribution.map(({ who, role, pct }) => ({ who, role, pct }))).map((x, j) => (j === i ? { ...x, pct: r2((amount / comm) * 100) } : x));
    const absorb = rows.findIndex((x) => x.who === (rows[i].who === 'platform' ? 'agent' : 'platform'));
    if (absorb >= 0) rows[absorb] = { ...rows[absorb], pct: r2(100 - rows.reduce((a, x, j) => (j === absorb ? a : a + x.pct), 0)) };
    setEdit(rows);
  };
  const terms = pre.data?.fields.terms || {};
  const cx = pre.data?.contract;
  const notSigned = notSignedFlag(lead.data?.status, pre.data);

  return (
    <div className="col gap-lg">
      <div className="deal-money" role="region" aria-label="Деньги по сделке">
        <div className="deal-money-tile">
          <span className="label">Клиент платит</span>
          {isStaff && r ? <AmountInput label="Клиент платит, $" value={r.commission_total} onCommit={(v) => upd('commission_total', v)} /> : <b className="num">{r ? money(r.commission_total) : '—'}</b>}
          <small>{Number(f.price) > 0 ? `${f.commission_pct}% от ${money(Number(f.price))}` : 'введите цену'}</small>
        </div>
        {r?.distribution?.map((x, i) => (
          <div key={i} className="deal-money-tile">
            <span className="label">{x.role}</span>
            {isStaff ? <AmountInput label={`${x.role}, $`} value={x.amount} onCommit={(v) => setRowAmount(i, v)} /> : <b className="num">{money(x.amount)}</b>}
            <small>{r2(x.pct)}%</small>
          </div>
        ))}
        <div className="deal-money-tile">
          <span className="label">Итого</span>
          <b className={`num ${r && (Math.abs(distSum - 100) > 0.01 || badRows) ? 'text-red' : ''}`}>{r ? `${distSum}%` : '—'}</b>
          <small>{calcBusy ? 'пересчёт…' : badRows ? 'доля < 0' : edit ? 'вручную' : 'по сценарию'}</small>
          {isStaff && (edit || f.commission_total !== '') && <button type="button" className="btn btn-secondary btn-sm" onClick={() => { setEdit(null); upd('commission_total', ''); }}>Сбросить</button>}
        </div>
      </div>
      {notSigned && <div className="alert alert-warn">Договор по заявке #{leadId} ещё не подписан: сделку оформляют после «Договор подписан».</div>}
      {cx && (
        <div className="alert alert-ok" style={{ fontSize: 13 }}>
          <CheckCircle2 size={16} />
          <span>Данные из договора #{cx.id}{cx.signed_at ? ` (подписан ${cx.signed_at.slice(0, 10).split('-').reverse().join('.')})` : ''} подставлены.
            {[terms.deposit_usd && `Депозит $${terms.deposit_usd}`, terms.pay_day && `оплата ${terms.pay_day}-го числа`, terms.rent_duration_months && `срок ${terms.rent_duration_months} мес`, terms.transfer_date && `передача ${terms.transfer_date}`].filter(Boolean).map((t) => ` ${t}.`).join('')}</span>
        </div>
      )}
    <div className="split">
      <div className="col gap-lg">
        <Card label="1" title={leadId ? `База расчёта · заявка #${leadId}` : 'База расчёта'}>
          <div className="col">
            <div className="row row-wrap">
              <Seg value={f.deal_type} onChange={setType} options={[{ value: 'rent', label: 'Аренда · 50%' }, { value: 'sale', label: 'Продажа · 3%' }]} />
              {f.deal_type === 'rent' && <Seg value={f.sub_type} onChange={setSub} options={[{ value: 'long', label: 'Долгосрок' }, { value: 'short', label: 'Краткосрок' }, { value: 'daily', label: 'Посуточно' }, { value: 'commercial', label: 'Коммерция' }]} />}
            </div>
            <div className="form-grid">
              <Field label={f.deal_type === 'sale' ? 'Цена объекта, $' : 'Сумма сделки (цена аренды), $/мес'}><Input type="number" inputMode="decimal" value={f.price} onChange={(e) => upd('price', e.target.value)} disabled={ro('price')} required /></Field>
              {/* the commission is fixed for agents (backend deals.default_commission_pct ignores theirs): they see it in the top tiles only.
                  Staff change the % here or the $ right in the tiles above. */}
              {isStaff && <Field label="Комиссия, % от цены"><Input type="number" inputMode="decimal" value={f.commission_pct} onChange={(e) => { upd('commission_pct', e.target.value); upd('commission_total', ''); }} /></Field>}
              {isStaff && <Field label="Агент"><Select value={f.agent_id} onChange={(e) => upd('agent_id', e.target.value)} placeholder="Выбрать" options={agentList.map((a) => ({ value: a.id, label: `${a.name} (${a.level || 50}%)` }))} /></Field>}
              {/* the level is set by admin/operator; the backend ignores agent_level sent by an agent */}
              {isStaff && <Field label="Уровень агента"><Seg value={Number(f.agent_level)} onChange={(v) => upd('agent_level', v)} options={[50, 70, 90].map((v) => ({ value: v, label: `${v}%` }))} /></Field>}
              <Field label="Рекрут / второй участник" hint={sc ? PEOPLE_HINT[rule] : undefined}>
                <Select value={f.recruit_id} onChange={(e) => upd('recruit_id', e.target.value)} placeholder="Без рекрута" options={(isStaff ? agentList.filter((u) => u.id !== Number(f.agent_id)) : itemsOf(recruits.data)).map((u) => ({ value: u.id, label: u.name }))} />
              </Field>
              {!sc && f.recruit_id ? <Field label="Доля рекрута, %"><Input type="number" value={f.recruit_pct} onChange={(e) => upd('recruit_pct', e.target.value)} /></Field> : <div />}
              {!sc && <Field label="Источник клиента" hint="10% от комиссии"><Select value={f.client_src} onChange={(e) => upd('client_src', e.target.value)} options={SRC.slice(1).map((x) => ({ value: x, label: x }))} placeholder="Свой клиент" /></Field>}
              {/* exclusive confirmation (verbal / signed + screenshot) lives on the object card (session exclusive-sign) */}
              <div className="span2 row row-wrap gap-lg">
                <Switch checked={f.is_exclusive} onChange={(v) => !ro('is_exclusive') && upd('is_exclusive', v)} label={f.is_exclusive ? 'Эксклюзив объекта' : 'Неэксклюзив: агенту 50%'} />
                <Switch checked={f.is_rerented} onChange={(v) => upd('is_rerented', v)} label="Повторная сдача" />
              </div>
            </div>
          </div>
        </Card>
        <Card label="2" title="Что происходит в сделке?">
          <div className="grid g2">
            {AXES.map((a, i) => (
              <button key={a.v} type="button" className={`choice ${sc?.axis === a.v ? 'on' : ''}`} style={i === AXES.length - 1 ? { gridColumn: '1 / -1' } : undefined} onClick={() => setAxis(a.v)} aria-pressed={sc?.axis === a.v}>
                <span className="grow"><b>{a.t}</b><small>{a.s}</small></span>
              </button>
            ))}
          </div>
          {!sc && <div className="muted mt" style={{ fontSize: 12 }}>Сценарий не выбран: стандартная схема по уровню агента, рекруту и источнику клиента. Выберите сценарий, если сделка идёт по-другому.</div>}
        </Card>
        {sc && (
          <Card label="3" title="Детали сценария">
            <div className="col">
              {sc.axis === 'make' && <Field label="Лестница по эксклюзивам агента" hint="1–10: 50% · 11–100: 70% · после 100: 90%. Неэксклюзивная сделка всегда 50%."><Input value={`Сейчас ${f.is_exclusive ? f.agent_level : 50}%`} disabled /></Field>}
              {sc.axis === 'participate' && <Field label="Вариант участия"><Select value={sc.opt} onChange={(e) => patchSc({ opt: e.target.value })} options={Object.entries(PARTICIPATE).map(([k, v]) => ({ value: k, label: `${v[0]} — ${v[1]}%` }))} /></Field>}
              {sc.axis === 'transfer' && <Field label="Роль в передаче"><Select value={sc.role} onChange={(e) => patchSc({ role: e.target.value })} options={[
                { value: 'mentor', label: 'Наставник · полный цикл · 50%' }, { value: 'stream', label: 'Передаю поток · 25% с агентом 50% / 15% с агентом 70%' }, { value: 'keeper', label: 'Хранитель потока · 1–5%' }]} /></Field>}
              {sc.axis === 'transfer' && sc.role === 'keeper' && <Field label="Сделок в потоке"><Input type="number" value={sc.n ?? 0} onChange={(e) => patchSc({ n: Number(e.target.value) })} /></Field>}
              {sc.axis === 'create' && <Field label="Состояние направления"><Select value={sc.role} onChange={(e) => patchSc({ role: e.target.value })} options={[{ value: 'test', label: 'Тест направления · 50%' }, { value: 'after', label: 'Создатель направления · 25% / 15%' }]} /></Field>}
              {((sc.axis === 'transfer' && sc.role === 'stream') || (sc.axis === 'create' && sc.role === 'after')) && <Field label="Уровень агента полного цикла"><Seg value={sc.lev ?? 50} onChange={(v) => patchSc({ lev: v })} options={[{ value: 50, label: '50%' }, { value: 70, label: '70%' }]} /></Field>}
              <div className="alert alert-info">{AXIS_NOTE[sc.axis]}</div>
            </div>
          </Card>
        )}
        <Card label="Объект и стороны" title="Детали">
          <div className="col" style={{ marginBottom: 14 }}>
            {pid ? (
              <div className="list-row">
                <div className="grow">
                  <div style={{ fontWeight: 700 }}>Объект #{pid} · {obj?.address || '…'}</div>
                  {obj && <div className="row row-wrap gap-sm mt"><PropertyStatusBadge status={obj.status} />{obj.agent_tag && <span className="muted" style={{ fontSize: 12 }}>{obj.agent_tag}</span>}{obj.needs_check && <Badge tone="warn">на проверке</Badge>}</div>}
                </div>
                <Link to={objLink(pid)} className="btn btn-secondary btn-sm"><ExternalLink size={14} />Открыть</Link>
                <Button size="sm" variant="secondary" onClick={() => pick(null)}>Сменить</Button>
              </div>
            ) : manual ? (
              <div className="alert alert-info" style={{ fontSize: 13 }}>
                <span className="grow">Объекта нет в базе: он будет создан из этих данных и уйдёт оператору на проверку. Сделку подтвердят после проверки объекта.</span>
                <Button size="sm" variant="secondary" onClick={() => setManual(false)}>Выбрать из базы</Button>
              </div>
            ) : (
              <>
                <div className="input-icon">
                  <Search size={16} />
                  <input className="input" placeholder="Адрес, номер объекта, телефон собственника, кадастр" value={q} onChange={(e) => setQ(e.target.value)} />
                </div>
                {!!dq && found.data && (found.data.items.length === 0 ? <div className="muted" style={{ fontSize: 13 }}>В базе не найдено</div> : found.data.items.map((o) => (
                  <button key={o.id} type="button" className="list-row" style={{ textAlign: 'left', width: '100%', background: 'none', cursor: 'pointer' }} onClick={() => pick(o.id)}>
                    <div className="grow">
                      <div style={{ fontWeight: 700 }}>#{o.id} · {o.address || 'Без адреса'}</div>
                      <div className="muted" style={{ fontSize: 12 }}>{[o.district, o.agent_tag].filter(Boolean).join(' · ')}</div>
                    </div>
                    {o.blocked ? <Badge tone="warn">{o.blocked}</Badge> : <PropertyStatusBadge status={o.status} />}
                  </button>
                )))}
                <Button size="sm" variant="secondary" onClick={() => setManual(true)}>Объекта нет в базе</Button>
              </>
            )}
            {obj?.blocked && <div className="alert alert-warn">{obj.blocked}: сделку по нему оформляет его агент.</div>}
            {obj?.fields && <div className="muted" style={{ fontSize: 12 }}>Данные объекта подставлены из базы. Изменить их можно в карточке объекта (после проверки — через «Запрос оператору»).</div>}
            {exists && <div className="alert alert-warn"><span className="grow">Такой объект уже есть в базе: #{exists}</span><Button size="sm" onClick={() => pick(exists)}>Выбрать #{exists}</Button></div>}
          </div>
          <div className="form-grid">
            <Field label="Адрес" className="span2"><Input value={f.address} onChange={(e) => upd('address', e.target.value)} disabled={!manual && (!pid || ro('address'))} /></Field>
            <Field label="Кадастровый код" hint={cadastralHint(f.cadastral, obj?.fields?.cadastral)}><Input value={f.cadastral} placeholder="01.14.12.003.045" onChange={(e) => upd('cadastral', cadastralInput(e))} onBlur={() => f.cadastral && f.cadastral !== obj?.fields?.cadastral && upd('cadastral', cadastralNorm(f.cadastral))} disabled={!manual && (!pid || ro('cadastral'))} /></Field>
            <Field label="Ссылка на объявление"><Input value={f.ad_link} onChange={(e) => upd('ad_link', e.target.value)} disabled={!manual && (!pid || ro('ad_link'))} /></Field>
            {ro('metro') && <Field label="Метро" className="span2"><Input value={f.metro} disabled /></Field>}
            {f.deal_type === 'rent' && <>
              <Field label="Аренда с"><Input type="date" value={f.rent_from} onChange={(e) => upd('rent_from', e.target.value)} /></Field>
              <Field label="Аренда до"><Input type="date" value={f.rent_to} onChange={(e) => upd('rent_to', e.target.value)} /></Field>
            </>}
            <Field label="Клиент"><Input value={f.client_name} onChange={(e) => upd('client_name', e.target.value)} /></Field>
            <Field label="Телефон клиента"><Input value={f.client_phone} onChange={(e) => upd('client_phone', phoneInput(e))} inputMode="tel" /></Field>
            <Field label="Telegram клиента"><Input value={f.client_tg} onChange={(e) => upd('client_tg', e.target.value)} /></Field>
            <Field label="Собственник"><Input value={f.owner_name} onChange={(e) => setF((x) => ({ ...x, owner_name: e.target.value, owner_id: 0, owner_typed: true }))} disabled={!manual && (!pid || ro('owner_name'))} /></Field>
            <Field label="Телефон собственника" className="span2"><Input value={f.owner_phone} onChange={(e) => { const v = phoneInput(e); setF((x) => ({ ...x, owner_phone: v, owner_id: 0, owner_typed: true })); }} inputMode="tel" disabled={!manual && (!pid || ro('owner_phone'))} /></Field>
            {f.owner_typed && !f.owner_id && (
              <div className="span2"><OwnerSuggest query={f.owner_phone || f.owner_name}
                onPick={(o) => setF((x) => ({ ...x, owner_id: o.id, owner_name: o.name || '', owner_phone: o.phone || '' }))} /></div>
            )}
          </div>
        </Card>
        <Card label="День 0" title="Оценка сделки при заключении · H2O 0–3">
          <div className="col">
            <H2O label="Клиент" value={t0.c} onChange={(v) => setT0({ ...t0, c: v })} />
            <H2O label="Собственник" value={t0.o} onChange={(v) => setT0({ ...t0, o: v })} />
            <H2O label="Процесс" value={t0.p} onChange={(v) => setT0({ ...t0, p: v })} />
            <Field label="Комментарий"><Textarea value={t0.comment} onChange={(e) => setT0({ ...t0, comment: e.target.value })} /></Field>
          </div>
        </Card>
      </div>
      <div className="col gap-lg deal-side">
        <Button size="lg" variant="ok" block disabled={!valid || calcBusy} loading={save.isPending} onClick={() => save.mutate()}><CheckCircle2 size={18} />Отправить на подтверждение</Button>
        {missing.length > 0 && <div className="alert alert-warn" style={{ fontSize: 13 }}>Чтобы отправить, заполните: {missing.join(', ')}</div>}
        <div className="muted" style={{ fontSize: 12 }}>После подтверждения оператором заявка станет «Сдано», бот поставит День 14 и напоминания о конце договора за 30, 7 и 1 день.</div>
      </div>
    </div>
    </div>
  );
}
