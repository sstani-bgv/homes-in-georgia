// C9b deal form with live commission calc (/api/deals/calc) and T0 evaluation (as in the old #Rented form).
import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CheckCircle2 } from 'lucide-react';
import { api } from '../../api';
import { itemsOf } from '../../api/client';
import type { Deal, DealCalcIn } from '../../api/types';
import { useAuth } from '../../auth/AuthContext';
import { errText, useToast } from '../../components/toast';
import { Button, Card, Field, Input, Seg, Select, Switch, Textarea } from '../../components/ui';
import { levelFor, money, todayStr } from '../../lib/format';

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

const SRC = ['', 'Instagram', 'Facebook', 'Telegram-канал', 'Рекомендация', 'Сайт', 'Другое'];

export function DealForm({ leadId, onDone }: { leadId?: number; onDone?: (d: Deal) => void }) {
  const { user, isStaff } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const agents = useQuery({ queryKey: ['users', 'agents'], queryFn: () => api.users.list({ role: 'agent,recruit', active: true }), enabled: isStaff });
  const recruits = useQuery({ queryKey: ['users', 'recruits'], queryFn: () => api.users.list({ role: 'recruit', active: true }) });
  const lead = useQuery({ queryKey: ['lead', leadId], queryFn: () => api.leads.get(leadId!), enabled: !!leadId });

  const [f, setF] = useState({
    agent_id: user?.id ?? 0, recruit_id: '' as string | number, deal_type: 'rent' as 'rent' | 'sale', sub_type: 'long',
    is_exclusive: false, is_rerented: false, price: '' as string | number, commission_pct: 50, commission_total: '' as string | number,
    agent_level: user?.level || levelFor(user?.exclusive_deals || 0), recruit_pct: 0, client_src: '',
    address: '', cadastral: '', ad_link: '', metro: '', client_name: '', client_phone: '', client_tg: '', owner_name: '', owner_phone: '',
    rent_from: todayStr(), rent_to: '',
  });
  const [t0, setT0] = useState<{ c: number | null; o: number | null; p: number | null; comment: string }>({ c: null, o: null, p: null, comment: '' });
  const upd = (k: keyof typeof f, v: unknown) => setF((x) => ({ ...x, [k]: v }));

  useEffect(() => {
    const l = lead.data;
    if (!l) return;
    setF((x) => ({
      ...x,
      agent_id: l.agent?.id ?? l.agent_id ?? x.agent_id,
      price: l.listing?.price ?? x.price,
      address: l.listing?.address || x.address,
      client_name: l.client?.name || x.client_name,
      client_phone: l.client?.phone || x.client_phone,
      client_tg: l.client?.username ? `@${l.client.username}` : x.client_tg,
    }));
  }, [lead.data]);

  const agentList = itemsOf(agents.data);
  useEffect(() => {
    const a = agentList.find((u) => u.id === Number(f.agent_id));
    if (a) upd('agent_level', a.level || levelFor(a.exclusive_deals));
  }, [f.agent_id, agents.data]); // eslint-disable-line react-hooks/exhaustive-deps

  const calcIn: DealCalcIn = useMemo(() => ({
    price: Number(f.price) || 0, commission_pct: Number(f.commission_pct), commission_total: f.commission_total === '' ? null : Number(f.commission_total),
    agent_level: Number(f.agent_level), is_exclusive: f.is_exclusive, recruit_pct: f.recruit_id ? Number(f.recruit_pct) : 0,
    client_src: f.client_src || null, deal_type: f.deal_type, is_rerented: f.is_rerented,
  }), [f]);
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
        lead_id: leadId ?? null, property_id: lead.data?.property_id ?? null,
        agent_id: Number(f.agent_id), recruit_id: f.recruit_id ? Number(f.recruit_id) : null,
        deal_type: f.deal_type, sub_type: f.deal_type === 'rent' ? f.sub_type : null,
        is_exclusive: f.is_exclusive, is_rerented: f.is_rerented,
        price: Number(f.price), commission_pct: Number(f.commission_pct),
        commission_total: f.commission_total === '' ? null : Number(f.commission_total),
        agent_level: Number(f.agent_level), recruit_pct: f.recruit_id ? Number(f.recruit_pct) : null,
        client_src: f.client_src || null,
        address: str(f.address), cadastral: str(f.cadastral), ad_link: str(f.ad_link), metro: str(f.metro),
        client_name: str(f.client_name), client_phone: str(f.client_phone), client_tg: str(f.client_tg),
        owner_name: str(f.owner_name), owner_phone: str(f.owner_phone),
        rent_from: f.deal_type === 'rent' ? f.rent_from || null : null, rent_to: f.deal_type === 'rent' ? f.rent_to || null : null,
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
    onError: (e) => toast(errText(e), true),
  });

  const r = calc.data;
  const valid = Number(f.price) > 0 && Number(f.agent_id) > 0 && (f.deal_type === 'sale' || !!f.rent_from);
  const calcBusy = debouncing || calc.isFetching;

  return (
    <div className="split">
      <div className="col gap-lg">
        <Card label="Сделка" title={leadId ? `По заявке #${leadId}` : 'Новая сделка'}>
          <div className="col">
            <div className="row row-wrap">
              <Seg value={f.deal_type} onChange={setType} options={[{ value: 'rent', label: 'Аренда' }, { value: 'sale', label: 'Продажа' }]} />
              {f.deal_type === 'rent' && <Seg value={f.sub_type} onChange={setSub} options={[{ value: 'long', label: 'Долгосрок' }, { value: 'short', label: 'Краткосрок' }, { value: 'daily', label: 'Посуточно' }, { value: 'commercial', label: 'Коммерция' }]} />}
            </div>
            <div className="form-grid">
              {isStaff ? (
                <Field label="Агент"><Select value={f.agent_id} onChange={(e) => upd('agent_id', e.target.value)} placeholder="Выбрать" options={agentList.map((a) => ({ value: a.id, label: `${a.name} (${a.level || 50}%)` }))} /></Field>
              ) : (
                <Field label="Агент"><Input value={user?.name || ''} disabled /></Field>
              )}
              <Field label="Уровень агента"><Seg value={Number(f.agent_level)} onChange={(v) => upd('agent_level', v)} options={[50, 70, 90].map((v) => ({ value: v, label: `${v}%` }))} /></Field>
              <Field label={f.deal_type === 'sale' ? 'Цена объекта, $' : 'Цена аренды, $/мес'}><Input type="number" inputMode="decimal" value={f.price} onChange={(e) => upd('price', e.target.value)} required /></Field>
              <Field label="Комиссия, % от цены"><Input type="number" value={f.commission_pct} onChange={(e) => upd('commission_pct', e.target.value)} /></Field>
              <Field label="Комиссия вручную, $" hint="если отличается от расчёта"><Input type="number" value={f.commission_total} onChange={(e) => upd('commission_total', e.target.value)} /></Field>
              <Field label="Источник клиента" hint="10% от комиссии"><Select value={f.client_src} onChange={(e) => upd('client_src', e.target.value)} options={SRC.slice(1).map((s) => ({ value: s, label: s }))} placeholder="Свой клиент" /></Field>
              <Field label="Рекрут"><Select value={f.recruit_id} onChange={(e) => upd('recruit_id', e.target.value)} placeholder="Без рекрута" options={itemsOf(recruits.data).map((u) => ({ value: u.id, label: u.name }))} /></Field>
              {f.recruit_id ? <Field label="Доля рекрута, %"><Input type="number" value={f.recruit_pct} onChange={(e) => upd('recruit_pct', e.target.value)} /></Field> : <div />}
              <div className="span2 row row-wrap gap-lg">
                <Switch checked={f.is_exclusive} onChange={(v) => upd('is_exclusive', v)} label="Эксклюзив" />
                <Switch checked={f.is_rerented} onChange={(v) => upd('is_rerented', v)} label="Повторная сдача" />
              </div>
            </div>
          </div>
        </Card>
        <Card label="Объект и стороны" title="Детали">
          <div className="form-grid">
            <Field label="Адрес" className="span2"><Input value={f.address} onChange={(e) => upd('address', e.target.value)} /></Field>
            <Field label="Кадастровый код"><Input value={f.cadastral} onChange={(e) => upd('cadastral', e.target.value)} /></Field>
            <Field label="Ссылка на объявление"><Input value={f.ad_link} onChange={(e) => upd('ad_link', e.target.value)} /></Field>
            {f.deal_type === 'rent' && <>
              <Field label="Аренда с"><Input type="date" value={f.rent_from} onChange={(e) => upd('rent_from', e.target.value)} /></Field>
              <Field label="Аренда до"><Input type="date" value={f.rent_to} onChange={(e) => upd('rent_to', e.target.value)} /></Field>
            </>}
            <Field label="Клиент"><Input value={f.client_name} onChange={(e) => upd('client_name', e.target.value)} /></Field>
            <Field label="Телефон клиента"><Input value={f.client_phone} onChange={(e) => upd('client_phone', e.target.value)} inputMode="tel" /></Field>
            <Field label="Telegram клиента"><Input value={f.client_tg} onChange={(e) => upd('client_tg', e.target.value)} /></Field>
            <Field label="Собственник"><Input value={f.owner_name} onChange={(e) => upd('owner_name', e.target.value)} /></Field>
            <Field label="Телефон собственника" className="span2"><Input value={f.owner_phone} onChange={(e) => upd('owner_phone', e.target.value)} inputMode="tel" /></Field>
          </div>
        </Card>
        <Card label="T0" title="Оценка сделки при заключении · H2O 0–3">
          <div className="col">
            <H2O label="Клиент" value={t0.c} onChange={(v) => setT0({ ...t0, c: v })} />
            <H2O label="Собственник" value={t0.o} onChange={(v) => setT0({ ...t0, o: v })} />
            <H2O label="Процесс" value={t0.p} onChange={(v) => setT0({ ...t0, p: v })} />
            <Field label="Комментарий"><Textarea value={t0.comment} onChange={(e) => setT0({ ...t0, comment: e.target.value })} /></Field>
          </div>
        </Card>
      </div>
      <div className="col gap-lg" style={{ position: 'sticky', top: 80 }}>
        <div className="calc-box">
          <div className="label" style={{ color: 'rgba(255,255,255,.7)' }}>Ваша доля</div>
          <div className="calc-big num">{r ? money(r.agent_amount) : '—'}</div>
          <div className="divider" style={{ background: 'rgba(255,255,255,.15)' }} />
          <div className="calc-row"><span>Комиссия</span><strong>{r ? money(r.commission_total) : '—'}</strong></div>
          <div className="calc-row"><span>Платформа ({100 - Number(f.agent_level)}%)</span><strong>{r ? money(r.platform_amount) : '—'}</strong></div>
          {Number(r?.recruit_amount) > 0 && <div className="calc-row"><span>Рекрут</span><strong>{money(r?.recruit_amount)}</strong></div>}
          {Number(r?.client_src_amount) > 0 && <div className="calc-row"><span>Источник клиента</span><strong>{money(r?.client_src_amount)}</strong></div>}
          <div className="calc-row"><span>Агент ({f.agent_level}%)</span><strong>{r ? money(r.agent_amount) : '—'}</strong></div>
          {calcBusy && <div style={{ fontSize: 11, opacity: .6, marginTop: 6 }}>пересчёт…</div>}
        </div>
        <Button size="lg" variant="ok" block disabled={!valid || calcBusy} loading={save.isPending} onClick={() => save.mutate()}><CheckCircle2 size={18} />Отправить на подтверждение</Button>
        <div className="muted" style={{ fontSize: 12 }}>После подтверждения оператором бот опубликует #Rented, поставит T14 и напоминания о конце договора за 30, 7 и 1 день.</div>
      </div>
    </div>
  );
}
