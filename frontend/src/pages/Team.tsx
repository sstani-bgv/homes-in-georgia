import { useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Search, Target } from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../api';
import { itemsOf } from '../api/client';
import type { AgentStat, CounterPair } from '../api/types';
import { Avatar, Badge, Button, Card, Drawer, Empty, ErrorBox, Input, KV, Loading, PageHead, Progress, Seg, Tabs } from '../components/ui';
import { GROWTH_LADDER, KNOWLEDGE_ITEMS, LADDER_VIEWS, PRINCIPLES, QUIZ_ALL } from '../features/team/content';
import { ROLE_LABEL, fmtDate, lastMonths, monthKey, monthLabel, nextLevelAt } from '../lib/format';
import { useUserNames } from '../lib/useUserNames';

export function LevelProgress({ deals, level }: { deals: number; level?: number | null }) {
  const lvl = level || (deals < 11 ? 50 : deals < 101 ? 70 : 90);
  const next = nextLevelAt(deals);
  const prev = lvl === 50 ? 0 : lvl === 70 ? 11 : 101;
  if (!next) return <div className="muted" style={{ fontSize: 12 }}>Максимальный уровень 90%</div>;
  return (
    <div className="col gap-sm">
      <Progress value={deals - prev} max={next - prev} />
      <div className="muted" style={{ fontSize: 12 }}>до уровня {lvl === 50 ? 70 : 90}%: <b>{next - deals}</b> сделок</div>
    </div>
  );
}

function Leaderboard({ onOpen }: { onOpen: (a: AgentStat) => void }) {
  const { data, isLoading, error } = useQuery({ queryKey: ['analytics', 'agents'], queryFn: api.analytics.agents });
  const items = itemsOf(data).sort((a, b) => b.exclusive_deals - a.exclusive_deals);
  if (isLoading) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  if (!items.length) return <Empty />;
  return (
    <div className="grid" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))' }}>
      {items.map((a, i) => (
        <button key={a.id} className={`card card-pad ${i === 0 ? 'card-blue' : ''}`} style={{ textAlign: 'left', cursor: 'pointer', color: i === 0 ? '#fff' : undefined }} onClick={() => onOpen(a)}>
          <div className="row">
            <span className={`rank ${i < 3 ? 'top' : ''}`} style={i === 0 ? { background: '#fff', color: 'var(--brand)' } : undefined}>{i + 1}</span>
            <Avatar name={a.name} />
            <div className="grow">
              <div style={{ fontWeight: 800 }}>{a.name}</div>
              <div className="label">{a.role ? ROLE_LABEL[a.role] : 'Агент'}</div>
            </div>
            <span className="badge" style={i === 0 ? { background: 'var(--accent)', color: '#fff' } : undefined}>{a.level || 50}%</span>
          </div>
          <div className="row row-between mt" style={{ fontSize: 13 }}>
            <span><b className="num" style={{ fontSize: 20, fontWeight: 900 }}>{a.exclusive_deals}</b> <span className="muted">эксклюзивов</span></span>
            <span className="muted">{a.deals_month ?? 0} за месяц</span>
          </div>
          <div className="mt"><LevelProgress deals={a.exclusive_deals} level={a.level} /></div>
          <div className="row row-wrap gap-sm mt" style={{ fontSize: 12 }}>
            {a.active_leads !== undefined && <Badge tone="grey">заявок: {a.active_leads}</Badge>}
            {a.active_properties !== undefined && <Badge tone="grey">объектов: {a.active_properties}</Badge>}
          </div>
        </button>
      ))}
    </div>
  );
}

function Counter({ label, c }: { label: string; c?: CounterPair }) {
  if (!c) return null;
  return (
    <div className="col gap-sm">
      <div className="row row-between" style={{ fontSize: 12 }}><span className="label">{label}</span><b className="num">{c.done}/{c.plan}</b></div>
      <Progress value={c.done} max={c.plan} />
    </div>
  );
}

function Recruits() {
  const [month, setMonth] = useState(monthKey());
  const { data, isLoading, error } = useQuery({ queryKey: ['counters', month], queryFn: () => api.reports.counters(month) });
  const items = itemsOf(data).filter((r) => r.role === 'recruit');
  const nameOf = useUserNames();
  return (
    <>
      <select className="select" style={{ width: 'auto', marginBottom: 14 }} value={month} onChange={(e) => setMonth(e.target.value)}>
        {lastMonths(12).map((m) => <option key={m} value={m}>{monthLabel(m)}</option>)}
      </select>
      {isLoading ? <Loading /> : error ? <ErrorBox error={error} /> : items.length === 0 ? <Empty text="Рекрутов нет" /> : (
        <div className="grid" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))' }}>
          {items.map((r) => (
            <div key={r.user_id} className="card card-pad col">
              <div className="row"><Avatar name={r.name} /><div className="grow"><b>{r.name}</b><div className="muted" style={{ fontSize: 12 }}>Наставник: {r.mentor_id ? nameOf(r.mentor_id) : '—'}</div></div></div>
              <Counter label="Фото" c={r.photo} />
              <Counter label="Показы" c={r.showing} />
              <Counter label="Расклейка" c={r.stickers} />
              <Counter label="Сдачи" c={r.rental} />
            </div>
          ))}
        </div>
      )}
    </>
  );
}

function Standards() {
  const [quiz, setQuiz] = useState(false);
  const [lvl, setLvl] = useState(0);
  const [view, setView] = useState<keyof typeof LADDER_VIEWS>('growth');
  if (quiz) return <Quiz onClose={() => setQuiz(false)} />;
  const v = LADDER_VIEWS[view];
  const cur = GROWTH_LADDER[lvl];
  return (
    <div className="col gap-lg">
      <div className="card card-blue card-pad row row-between row-wrap">
        <div><div className="label">Platform 90/10</div><h2 style={{ color: '#fff' }}>Проверь знание стандартов</h2><div className="muted" style={{ marginTop: 4 }}>10 случайных вопросов из 20</div></div>
        <Button variant="secondary" onClick={() => setQuiz(true)}><Target size={16} />Викторина</Button>
      </div>
      <div className="split">
        <Card label="Интерактивно" title="Лестница роста 90/10">
          <div className="muted" style={{ fontSize: 13, marginBottom: 10 }}>Нажми на уровень, чтобы увидеть критерии и следующий шаг.</div>
          <div className="progress" style={{ marginBottom: 12 }}><div style={{ width: `${((lvl + 1) / GROWTH_LADDER.length) * 100}%` }} /></div>
          <div className="col gap-sm">
            {GROWTH_LADDER.map((g, i) => (
              <button key={g.title} type="button" className={`choice ${i === lvl ? 'on' : ''}`} style={{ padding: '10px 12px' }} onClick={() => setLvl(i)} aria-pressed={i === lvl}>
                <span className={`rank ${i <= lvl ? 'top' : ''}`}>{i + 1}</span>
                <span className="grow"><b>{g.title}</b><small>{g.short}</small></span>
                <span className="badge">{g.pct}</span>
              </button>
            ))}
          </div>
          <div className="alert alert-info mt"><div><b>{cur.title} · {cur.pct}</b><div style={{ fontWeight: 500, marginTop: 4 }}>{cur.text}</div></div></div>
        </Card>
        <Card label="Карта системы" title={v.label}>
          <Seg value={view} onChange={setView} options={Object.entries(LADDER_VIEWS).map(([k, x]) => ({ value: k as keyof typeof LADDER_VIEWS, label: x.label }))} />
          <p className="muted mt" style={{ fontSize: 13 }}>{v.intro}</p>
          {v.flow && (
            <div className="grid mt" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(120px, 1fr))' }}>
              {v.flow.map(([n, t, s]) => <div key={n} className="outcome-card"><span className="rank top">{n}</span><b style={{ display: 'block', marginTop: 8 }}>{t}</b><span className="muted" style={{ fontSize: 12 }}>{s}</span></div>)}
            </div>
          )}
          {v.cards && (
            <div className="col mt gap-sm">
              {v.cards.map(([rate, t, text], i) => (
                <details key={t} className="outcome-card" open={i === 0}>
                  <summary style={{ cursor: 'pointer', fontWeight: 700 }}><span className="badge" style={{ marginRight: 8 }}>{rate}</span>{t}</summary>
                  <p className="mt" style={{ fontSize: 13 }}>{text}</p>
                </details>
              ))}
            </div>
          )}
          {v.rule && <div className="alert alert-info mt"><div><b>Важно:</b> {v.rule}</div></div>}
        </Card>
      </div>
      <div className="grid" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))' }}>
        {PRINCIPLES.map((p) => (
          <Card key={p.title} label="Стандарт" title={p.title}>
            <ul className="std-list">{p.items.map((t) => <li key={t}>{t}</li>)}</ul>
          </Card>
        ))}
      </div>
      <Card label="Как мы работаем" title="Регламент воронок">
        <div className="col" style={{ fontSize: 14 }}>
          <div><b>Объекты.</b> Взял объявление — позвони в течение часа. Не дозвонился — бот напомнит через 2 часа. Без проверки оператором ничего не публикуем.</div>
          <div><b>Съёмка.</b> Все комнаты по чек-листу, вид из окна, подъезд. Горизонтальные фото, без людей и беспорядка.</div>
          <div><b>Заявки.</b> 30 минут на свободное время от собственника. Контакты клиента приходят после выбора времени.</div>
          <div><b>Сделки.</b> Форма сделки в день подписания. День 0 сразу, День 14 через 14 дней.</div>
          <div><b>Уровни.</b> До 10 эксклюзивов — 50%, 11–100 — 70%, от 101 — 90%.</div>
        </div>
      </Card>
    </div>
  );
}

function shuffle<T>(a: T[]): T[] { return a.map((x) => [Math.random(), x] as const).sort((p, q) => p[0] - q[0]).map(([, x]) => x); }
function makeQuiz() {
  return shuffle(QUIZ_ALL).slice(0, 10).map((q) => { const right = q.a[q.c]; const a = shuffle(q.a); return { q: q.q, a, c: a.indexOf(right) }; });
}

function Quiz({ onClose }: { onClose: () => void }) {
  const [qs, setQs] = useState(makeQuiz);
  const [i, setI] = useState(0);
  const [score, setScore] = useState(0);
  const [picked, setPicked] = useState<number | null>(null);
  const done = i >= qs.length;
  const answer = (k: number) => {
    if (picked !== null) return;
    setPicked(k);
    if (k === qs[i].c) setScore((s) => s + 1);
    setTimeout(() => { setPicked(null); setI((x) => x + 1); }, 800);
  };
  const restart = () => { setQs(makeQuiz()); setI(0); setScore(0); setPicked(null); };
  const pct = Math.round((score / qs.length) * 100);
  return (
    <Card label="Викторина" title={done ? 'Результат' : `Вопрос ${i + 1} из ${qs.length}`} actions={<Button size="sm" variant="ghost" onClick={onClose}>К стандартам</Button>} className="quiz">
      {done ? (
        <div className="col" style={{ alignItems: 'flex-start' }}>
          <div className="stat-value">{score}/{qs.length} · {pct}%</div>
          <div className="muted">{pct >= 80 ? 'Отлично, ты знаешь стандарты' : pct >= 60 ? 'Хороший результат' : 'Нужно повторить стандарты'}</div>
          <Button className="mt" onClick={restart}>Пройти ещё раз</Button>
        </div>
      ) : (
        <div className="col">
          <Progress value={i} max={qs.length} />
          <h3 style={{ fontSize: 17, margin: '8px 0' }}>{qs[i].q}</h3>
          {qs[i].a.map((a, k) => {
            const cls = picked === null ? '' : k === qs[i].c ? 'right' : k === picked ? 'wrong' : '';
            return <button key={a} type="button" className={`choice quiz-a ${cls}`} disabled={picked !== null} onClick={() => answer(k)}><b>{a}</b></button>;
          })}
        </div>
      )}
    </Card>
  );
}

function Knowledge() {
  const [cat, setCat] = useState('all');
  const [q, setQ] = useState('');
  const cats = ['all', ...Array.from(new Set(KNOWLEDGE_ITEMS.map((k) => k.cat)))];
  const needle = q.trim().toLowerCase();
  const items = KNOWLEDGE_ITEMS.filter((k) => (cat === 'all' || k.cat === cat) && (!needle || `${k.title} ${k.cat} ${k.body}`.toLowerCase().includes(needle)));
  return (
    <div className="col gap-lg" style={{ maxWidth: 860 }}>
      <div className="input-icon"><Search size={16} /><Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Поиск по базе знаний" /></div>
      <div className="row row-wrap gap-sm">{cats.map((c) => <button key={c} type="button" className={`chip ${cat === c ? 'active' : ''}`} onClick={() => setCat(c)}>{c === 'all' ? 'Все' : c}</button>)}</div>
      {items.length === 0 ? <Empty text="По этому запросу ничего не найдено" /> : items.map((k, i) => (
        <details key={k.id} className="card card-pad" open={i === 0}>
          <summary style={{ cursor: 'pointer' }} className="row row-between"><span><b>{k.title}</b><span className="label" style={{ display: 'block', marginTop: 2 }}>{k.cat}</span></span></summary>
          <p className="mt" style={{ fontSize: 14 }}>{k.body}</p>
        </details>
      ))}
    </div>
  );
}

function News() {
  const news = useQuery({ queryKey: ['news'], queryFn: api.news });
  const items = itemsOf(news.data);
  if (news.isLoading) return <Loading />;
  if (news.error) return <ErrorBox error={news.error} />;
  return (
    <div className="col" style={{ maxWidth: 860 }}>
      {items.length === 0 ? <Empty text="Новостей пока нет" /> : items.map((n) => (
        <div key={n.id} className="card card-pad">
          <div className="label">{fmtDate(n.date)}</div>
          <div className="mt" style={{ whiteSpace: 'pre-wrap' }}>{n.text}</div>
        </div>
      ))}
    </div>
  );
}

function UserCard({ a }: { a: AgentStat }) {
  const stats = useQuery({ queryKey: ['user-stats', a.id], queryFn: () => api.users.stats(a.id) });
  const s = stats.data || {};
  return (
    <div className="col gap-lg">
      <div className="row"><Avatar size="lg" name={a.name} /><div><h2>{a.name}</h2><div className="label">{a.role ? ROLE_LABEL[a.role] : ''} · {a.level || 50}%</div></div></div>
      <Card label="Уровень"><LevelProgress deals={a.exclusive_deals} level={a.level} /></Card>
      <KV items={[
        ['Эксклюзивов', a.exclusive_deals], ['Сделок за месяц', a.deals_month ?? '—'], ['Заявок в работе', a.active_leads ?? '—'], ['Объектов в работе', a.active_properties ?? '—'],
        ...Object.entries(s).filter(([, v]) => typeof v === 'number' || typeof v === 'string').slice(0, 8).map(([k, v]) => [k, String(v)] as [string, string]),
      ]} />
    </div>
  );
}

export function TeamPage() {
  const [params, setParams] = useSearchParams();
  const tab = (params.get('tab') as 'agents' | 'recruits' | 'standards' | 'knowledge' | 'news') || 'agents';
  const setTab = (t: string) => setParams((sp) => { sp.set('tab', t); return sp; }, { replace: true });
  const [open, setOpen] = useState<AgentStat | null>(null);
  return (
    <>
      <PageHead label="Лидерборд по эксклюзивным сделкам" title="Команда" />
      <Tabs value={tab} onChange={setTab} tabs={[{ value: 'agents', label: 'Агенты' }, { value: 'recruits', label: 'Рекруты' }, { value: 'standards', label: 'Стандарты' }, { value: 'knowledge', label: 'Знания' }, { value: 'news', label: 'Новости' }]} />
      {tab === 'agents' && <Leaderboard onOpen={setOpen} />}
      {tab === 'recruits' && <Recruits />}
      {tab === 'standards' && <Standards />}
      {tab === 'knowledge' && <Knowledge />}
      {tab === 'news' && <News />}
      <Drawer open={!!open} onClose={() => setOpen(null)} title="Карточка агента">{open && <UserCard a={open} />}</Drawer>
    </>
  );
}
