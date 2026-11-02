// /calc — standalone deal calculator (old tab «tab-calc»: Molecula 90/10 + scenario calculator).
// Rules ported from docs/legacy/crm/main.js: COMMISSION_RULES (sc0–sc6), rerented, manual split, moleculeRows().
// The official figure for a real deal still comes from POST /api/deals/calc (shown alongside) and the deal form.
import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Handshake } from 'lucide-react';
import { api } from '../api';
import { Card, Field, Input, PageHead, Seg, Select, Switch, Tabs } from '../components/ui';
import { money } from '../lib/format';

type Row = { n: string; p: number; note?: string };

// ---------------------------------------------------------------- scenarios (COMMISSION_RULES)
const SCENARIOS = [
  { t: 'Агент', s: 'Свой объект без рекрута' },
  { t: 'Агент + показ', s: 'Рекрут показал и подписал' },
  { t: 'Агент передал', s: 'Рекрут сделал всё' },
  { t: 'Рекрут · 5 сд.', s: 'Рекрут получает 20%' },
  { t: 'Рекрут · 10 сд.', s: 'Рекрут получает 50%' },
  { t: 'Объект рекрута', s: 'По кол-ву сделок' },
  { t: 'Передача клиента', s: '10% агенту-источнику' },
  { t: 'Ручное', s: 'Свои проценты' },
];
const MANUAL_ROWS = [
  ['Агент', 'Платформа'], ['Агент (объект)', 'Рекрут / Агент (показ)', 'Платформа'], ['Агент (передал)', 'Рекрут (сделал всё)', 'Платформа'],
  ['Агент', 'Рекрут (5 сд.)', 'Платформа'], ['Агент-наставник', 'Рекрут (10 сд.)', 'Платформа'], ['Агент-наставник', 'Рекрут', 'Платформа'],
  ['Агент (получил клиента)', 'Агент-источник (10%)', 'Платформа'], ['Агент', 'Рекрут', 'Внешний агент', 'Платформа'],
];
function scenarioRows(sc: number, excl: boolean, lvl: number, recD: 'lt10' | 'gte10'): Row[] {
  switch (sc) {
    case 0: { const a = excl ? lvl : 50; return [{ n: 'Агент', p: a }, { n: 'Платформа', p: 100 - a }]; }
    case 1: { const a = excl ? (lvl === 50 ? 30 : lvl === 70 ? 50 : 70) : 30; return [{ n: 'Агент (объект)', p: a }, { n: 'Рекрут/Агент (показ)', p: 20 }, { n: 'Платформа', p: 100 - a - 20 }]; }
    case 2: { const a = excl ? (lvl === 50 ? 10 : lvl === 70 ? 30 : 50) : 10; return [{ n: 'Агент (передал)', p: a }, { n: 'Рекрут (сделал всё)', p: 40 }, { n: 'Платформа', p: 100 - a - 40 }]; }
    case 3: { const a = excl ? 70 : 30; return [{ n: 'Агент', p: a }, { n: 'Рекрут (5 сд.)', p: 20 }, { n: 'Платформа', p: 100 - a - 20 }]; }
    case 4: return [{ n: 'Агент-наставник', p: 25 }, { n: 'Рекрут (10 сд.)', p: 50 }, { n: 'Платформа', p: 25 }];
    case 5: { const lt = recD === 'lt10'; return [{ n: 'Агент-наставник', p: lt ? 25 : 15 }, { n: 'Рекрут', p: lt ? 50 : 70 }, { n: 'Платформа', p: lt ? 25 : 15 }]; }
    case 6: { const ag = excl ? lvl : 50; return [{ n: 'Агент (получил клиента)', p: ag - 10 }, { n: 'Агент-источник (10%)', p: 10 }, { n: 'Платформа', p: 100 - ag }]; }
    default: return MANUAL_ROWS[7].map((n) => ({ n, p: 0 }));
  }
}

// ---------------------------------------------------------------- Molecula 90/10 (moleculeRows)
const AXES = [
  { v: 'make', t: 'Делаю свой объект', s: 'и полный цикл' },
  { v: 'participate', t: 'Участвую', s: 'чужой объект или клиент' },
  { v: 'transfer', t: 'Передаю', s: 'обучение и поток сделок' },
  { v: 'create', t: 'Создаю', s: 'новое направление' },
  { v: 'external', t: 'Внешний контур', s: 'эксперимент с Verified Exclusive' },
] as const;
type Axis = typeof AXES[number]['v'];
const PARTICIPATE: Record<string, [string, number, string]> = {
  object_recruit_joint: ['Объект рекрута · показ + договор', 20, 'Рекрут / участник'],
  object_recruit_sign: ['Объект рекрута · только подписание', 10, 'Рекрут / участник'],
  object_agent_joint: ['Объект агента · показ + договор', 20, 'Участник'],
  object_agent_sign: ['Объект агента · только подписание', 10, 'Участник'],
  client_joint_showing: ['Клиент · совместный показ', 20, 'Источник клиента'],
  client_transfer: ['Клиент · передача контакта', 10, 'Источник клиента'],
  client_external: ['Клиент со стороны · внешний источник', 30, 'Внешний источник'],
  take_exclusive: ['Чужой эксклюзив · полный цикл', 40, 'Куратор цикла'],
};
const AXIS_NOTE: Record<Axis, string> = {
  make: 'Личная лестница: делаешь полный цикл сам.',
  participate: 'Участие в чужой сделке: оплачивается конкретный объём работы.',
  transfer: 'Передача способности и потока: это отдельная ось, не ступень личной лестницы.',
  create: 'Новое направление: сначала тест, потом доля создателя.',
  external: 'Экспериментальный контур. Целевая модель после подтверждения доверия: внешний агент 50% · внутренний куратор 25% · платформа 25%.',
};
function moleculeRows(axis: Axis, n: number, opt: string, role: string, lev: number): Row[] {
  if (axis === 'make') { const p = n > 100 ? 90 : n > 10 ? 70 : 50; return [{ n: 'Агент · собственный цикл', p }, { n: 'Платформа', p: 100 - p }]; }
  if (axis === 'participate') { const d = PARTICIPATE[opt] || PARTICIPATE.object_recruit_joint; return [{ n: d[2], p: d[1] }, { n: 'Владелец объекта / агент', p: 50 }, { n: 'Платформа', p: 100 - d[1] - 50 }]; }
  if (axis === 'transfer') {
    if (role === 'mentor') return [{ n: 'Наставник · полный цикл', p: 50 }, { n: 'Платформа', p: 50 }];
    if (role === 'stream') { const share = lev === 70 ? 15 : 25; return [{ n: 'Передающий поток', p: share }, { n: 'Агент полного цикла', p: lev }, { n: 'Платформа', p: 100 - share - lev }]; }
    const keeper = Math.min(5, Math.max(1, Math.floor(n / 100) || 1));
    return [{ n: 'Хранитель потока', p: keeper }, { n: 'Платформа', p: 100 - keeper }];
  }
  if (axis === 'create') {
    if (role === 'test') return [{ n: 'Инициатор теста', p: 50 }, { n: 'Платформа', p: 50 }];
    const creator = lev === 70 ? 15 : 25;
    return [{ n: 'Создатель направления', p: creator }, { n: 'Агент полного цикла', p: lev }, { n: 'Платформа', p: 100 - creator - lev }];
  }
  return [{ n: 'Внешний агент', p: 50 }, { n: 'Внутренний куратор', p: 25 }, { n: 'Платформа', p: 25 }];
}

function Dist({ rows, base, title }: { rows: Row[]; base: number; title: string }) {
  const total = rows.reduce((s, r) => s + r.p, 0);
  return (
    <div className="calc-box">
      <div className="row row-between"><span className="label" style={{ color: 'rgba(255,255,255,.7)' }}>{title}</span><span className="badge" style={{ background: total === 100 ? 'rgba(255,255,255,.18)' : 'var(--accent)', color: '#fff' }}>Итого {total}%</span></div>
      <div className="calc-big mt">{money(base)}</div>
      <div className="mt">
        {rows.map((r) => (
          <div key={r.n} className="calc-row"><span>{r.n} · {r.p}%{r.note ? <><br /><small style={{ opacity: .7 }}>{r.note}</small></> : null}</span><strong>{money(Math.round((base * r.p) / 100))}</strong></div>
        ))}
      </div>
    </div>
  );
}

function Base({ price, setPrice, type, setType, sub, setSub }: { price: string; setPrice: (v: string) => void; type: 'rent' | 'sale'; setType: (v: 'rent' | 'sale') => void; sub: 'long' | 'commercial'; setSub: (v: 'long' | 'commercial') => void }) {
  return (
    <div className="form-grid">
      <Field label="Сумма сделки, $"><Input type="number" inputMode="decimal" value={price} onChange={(e) => setPrice(e.target.value)} placeholder="Цена объекта" /></Field>
      <Field label="Тип сделки"><Seg value={type} onChange={setType} options={[{ value: 'rent', label: 'Аренда' }, { value: 'sale', label: 'Продажа · 3%' }]} /></Field>
      {type === 'rent' && <Field label="Подтип аренды" className="span2"><Seg value={sub} onChange={setSub} options={[{ value: 'long', label: 'Долгосрочная · 50%' }, { value: 'commercial', label: 'Коммерция · 100%' }]} /></Field>}
    </div>
  );
}

export function CalcPage() {
  const [tab, setTab] = useState<'molecula' | 'scenarios'>('molecula');
  const [price, setPrice] = useState('');
  const [type, setType] = useState<'rent' | 'sale'>('rent');
  const [sub, setSub] = useState<'long' | 'commercial'>('long');
  const pct = type === 'sale' ? 3 : sub === 'commercial' ? 100 : 50;
  const p = Math.max(0, parseFloat(price) || 0);
  const comm = Math.round((p * pct) / 100);

  // molecula
  const [axis, setAxis] = useState<Axis>('make');
  const [count, setCount] = useState('0');
  const [opt, setOpt] = useState('object_recruit_joint');
  const [role, setRole] = useState('mentor');
  const [lev, setLev] = useState(50);
  useEffect(() => { setRole(axis === 'create' ? 'test' : 'mentor'); }, [axis]);
  const molRows = moleculeRows(axis, parseInt(count) || 0, opt, role, lev);

  // scenarios
  const [sc, setSc] = useState(0);
  const [excl, setExcl] = useState(true);
  const [lvl, setLvl] = useState(50);
  const [recD, setRecD] = useState<'lt10' | 'gte10'>('lt10');
  const [rerented, setRerented] = useState(false);
  const [manual, setManual] = useState<Record<string, string>>({});
  const [notes, setNotes] = useState<Record<string, string>>({});
  const scRows = useMemo<Row[]>(() => {
    if (rerented) return [{ n: 'Платформа', p: 0 }, { n: 'Агент', p: 0 }];
    const has = MANUAL_ROWS[sc].some((_, i) => manual[`${sc}_${i}`]);
    if (has || sc === 7) return MANUAL_ROWS[sc].map((n, i) => ({ n, p: parseFloat(manual[`${sc}_${i}`]) || 0, note: notes[`${sc}_${i}`] }));
    return scenarioRows(sc, excl, lvl, recD);
  }, [sc, excl, lvl, recD, rerented, manual, notes]);

  const official = useQuery({
    queryKey: ['calc', p, type, sub, excl, lvl, rerented],
    queryFn: () => api.deals.calc({ price: p, commission_pct: pct, agent_level: lvl, is_exclusive: excl, deal_type: type, is_rerented: rerented }),
    enabled: tab === 'scenarios' && p > 0, retry: false,
  });

  return (
    <>
      <PageHead label="Система 90/10" title="Калькулятор сделки" actions={<Link to="/deals/new" className="btn btn-secondary"><Handshake size={16} />Зафиксировать сделку</Link>} />
      <p className="muted" style={{ marginTop: -8, marginBottom: 16, maxWidth: 720 }}>Процент — это объём ответственности. Выбери роль в конкретной сделке и получи прозрачное распределение комиссии.</p>
      <Tabs value={tab} onChange={setTab} tabs={[{ value: 'molecula', label: 'Молекула 90/10' }, { value: 'scenarios', label: 'Сценарии сдачи' }]} />
      <div className="split">
        <div className="col gap-lg">
          <Card label="1" title="База расчёта">
            <Base price={price} setPrice={setPrice} type={type} setType={setType} sub={sub} setSub={setSub} />
            <div className="muted mt" style={{ fontSize: 12 }}>Аренда: 50% от суммы сделки, коммерция: 100%, продажа: 3%. Распределение считается от комиссии: <b>{money(comm)}</b>.</div>
          </Card>
          {tab === 'molecula' ? (
            <>
              <Card label="2" title="Что происходит в сделке">
                <div className="grid g2">
                  {AXES.map((a) => (
                    <button key={a.v} type="button" className={`choice ${axis === a.v ? 'on' : ''}`} onClick={() => setAxis(a.v)} aria-pressed={axis === a.v}>
                      <span className="grow"><b>{a.t}</b><small>{a.s}</small></span>
                    </button>
                  ))}
                </div>
              </Card>
              <Card label="3" title="Детали сценария">
                <div className="col">
                  {(axis === 'make' || axis === 'transfer') && (
                    <Field label={axis === 'make' ? 'Эксклюзивные сделки агента' : 'Сделок в потоке'} hint={axis === 'make' ? '1–10: 50% · 11–100: 70% · после 100: 90%. Неэксклюзивные сделки не ограничены.' : undefined}>
                      <Input type="number" value={count} onChange={(e) => setCount(e.target.value)} />
                    </Field>
                  )}
                  {axis === 'participate' && (
                    <Field label="Вариант участия"><Select value={opt} onChange={(e) => setOpt(e.target.value)} options={Object.entries(PARTICIPATE).map(([k, v]) => ({ value: k, label: `${v[0]} — ${v[1]}%` }))} /></Field>
                  )}
                  {axis === 'transfer' && (
                    <Field label="Роль в передаче"><Select value={role} onChange={(e) => setRole(e.target.value)} options={[
                      { value: 'mentor', label: 'Обучаю агента полному циклу · 50%' }, { value: 'stream', label: 'Передаю поток · 25% с агентом 50% / 15% с агентом 70%' },
                      { value: 'keeper', label: 'Хранитель потока · 1–5%' }]} /></Field>
                  )}
                  {axis === 'create' && (
                    <Field label="Состояние направления"><Select value={role} onChange={(e) => setRole(e.target.value)} options={[{ value: 'test', label: 'Тест направления · инициатор' }, { value: 'after', label: 'После успешного теста · 25% / 15%' }]} /></Field>
                  )}
                  {(axis === 'transfer' || axis === 'create') && (
                    <Field label="Уровень агента полного цикла"><Seg value={lev} onChange={setLev} options={[{ value: 50, label: '50%' }, { value: 70, label: '70%' }]} /></Field>
                  )}
                  <div className="alert alert-info">{AXIS_NOTE[axis]}</div>
                </div>
              </Card>
            </>
          ) : (
            <>
              <Card label="2" title="Сценарий">
                <div className="grid g2">
                  {SCENARIOS.map((x, i) => (
                    <button key={x.t} type="button" className={`choice ${sc === i && !rerented ? 'on' : ''}`} onClick={() => { setSc(i); setRerented(false); }} aria-pressed={sc === i}>
                      <span className="grow"><b>{x.t}</b><small>{x.s}</small></span>
                    </button>
                  ))}
                  <button type="button" className={`choice ${rerented ? 'on' : ''}`} onClick={() => setRerented(!rerented)} aria-pressed={rerented}>
                    <span className="grow"><b>Rerented</b><small>Новый клиент · комиссия 0$ · без счётчика</small></span>
                  </button>
                </div>
                {rerented && <div className="alert alert-info mt">Сделка фиксируется без комиссии. В счётчик и аналитику не идёт.</div>}
              </Card>
              <Card label="3" title="Участники">
                <div className="col">
                  <Switch checked={excl} onChange={setExcl} label={excl ? 'Эксклюзив' : 'Неэксклюзив'} />
                  {(sc <= 3 || sc === 6) && <Field label="Уровень агента"><Seg value={lvl} onChange={setLvl} options={[50, 70, 90].map((v) => ({ value: v, label: `${v}%` }))} /></Field>}
                  {sc === 5 && <Field label="Сделок у рекрута"><Seg value={recD} onChange={setRecD} options={[{ value: 'lt10', label: 'До 10 сделок' }, { value: 'gte10', label: '10 — 90 сделок' }]} /></Field>}
                  <details className="outcome-card" open={sc === 7}>
                    <summary style={{ cursor: 'pointer', fontWeight: 700 }}>Ручное распределение %</summary>
                    <div className="col mt gap-sm">
                      {MANUAL_ROWS[sc].map((n, i) => (
                        <div key={n} className="grid" style={{ gridTemplateColumns: 'minmax(0,1.2fr) 70px minmax(0,1fr)', gap: 6, alignItems: 'center' }}>
                          <span style={{ fontSize: 12, fontWeight: 600 }}>{n}</span>
                          <Input type="number" placeholder="%" value={manual[`${sc}_${i}`] || ''} onChange={(e) => setManual({ ...manual, [`${sc}_${i}`]: e.target.value })} />
                          <Input placeholder="комментарий" value={notes[`${sc}_${i}`] || ''} onChange={(e) => setNotes({ ...notes, [`${sc}_${i}`]: e.target.value })} />
                        </div>
                      ))}
                    </div>
                  </details>
                </div>
              </Card>
            </>
          )}
        </div>
        <div className="col gap-lg" style={{ position: 'sticky', top: 76 }}>
          <Dist rows={tab === 'molecula' ? molRows : scRows} base={rerented && tab === 'scenarios' ? 0 : comm} title="Распределение комиссии" />
          {tab === 'scenarios' && p > 0 && (
            <Card label="CRM" title="Расчёт по правилам платформы">
              {official.isLoading ? <div className="muted">Считаем</div> : official.data ? (
                <dl className="kv">
                  <dt>Комиссия</dt><dd>{money(official.data.commission_total)}</dd>
                  <dt>Платформа</dt><dd>{money(official.data.platform_amount)}</dd>
                  <dt>Агент</dt><dd>{money(official.data.agent_amount)}</dd>
                  {!!official.data.recruit_amount && <><dt>Рекрут</dt><dd>{money(official.data.recruit_amount)}</dd></>}
                </dl>
              ) : <div className="muted" style={{ fontSize: 13 }}>Недоступно</div>}
              <div className="faint mt" style={{ fontSize: 12 }}>Эти суммы попадут в сделку при сохранении формы.</div>
            </Card>
          )}
        </div>
      </div>
    </>
  );
}
