// «Аналитика» (superadmins only): hge.ge's own visitor counter, moved here from the hidden page hge.ge/stats/.
// Data: GET /analytics/site → backend services/site_analytics.py → the Apps Script report (apps-script/site-analytics.gs).
import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { RefreshCw } from 'lucide-react';
import { api } from '../api';
import type { SiteListing, SiteReport } from '../api/types';
import { Button, Card, ErrorBox, Loading, PageHead, Seg, Select } from '../components/ui';

const SITE = 'https://hge.ge/';
const PERIODS = [{ value: 1, label: 'Сегодня' }, { value: 7, label: '7 дней' }, { value: 30, label: '30 дней' }, { value: 90, label: '90 дней' }];

const SVC: Record<string, string> = { rent: 'Аренда', daily: 'Посуточно', sale: 'Продажа', new: 'Новостройки', commercial: 'Коммерция', 'car-rent': 'Аренда авто', 'car-sale': 'Продажа авто', all: 'Все' };
const KEYS: Record<string, string> = {
  q: 'Поиск', district: 'Район', beds: 'Спальни', price: 'Цена', area: 'Площадь', kind: 'Тип', deal: 'Сделка', building: 'Дом', condition: 'Ремонт',
  amen: 'Удобства', metro: 'Метро', term: 'Срок', tenants: 'Жильцов', pets: 'С животными', excl: 'Эксклюзив', floor: 'Этаж', floors: 'Этажей',
  ppsqm: 'Цена за м²', deposit: 'Депозит', heating: 'Отопление', year: 'Год', mileage: 'Пробег', gearbox: 'Коробка', fuel: 'Топливо', body: 'Кузов',
  seats: 'Мест', pickup: 'Выдача', notfirst: 'Не первый этаж', notlast: 'Не последний этаж',
};
const CH: Record<string, string> = { phone: 'Звонок', whatsapp: 'WhatsApp', telegram: 'Telegram', office: 'Офис на карте', viber: 'Viber', facebook: 'Facebook', instagram: 'Instagram', copy: 'Скопировали ссылку', native: 'Меню телефона', link: 'Ссылка' };
const SRC: Record<string, string> = { direct: 'Напрямую', share: 'Поделились ссылкой', google: 'Google', instagram: 'Instagram', facebook: 'Facebook', tiktok: 'TikTok', youtube: 'YouTube', threads: 'Threads', telegram: 'Telegram', whatsapp: 'WhatsApp', viber: 'Viber', myhome: 'Myhome.ge', 'ss.ge': 'SS.ge', yandex: 'Яндекс', bing: 'Bing' };
const DEV: Record<string, string> = { mobile: 'Телефон', tablet: 'Планшет', desktop: 'Компьютер' };
const LANG: Record<string, string> = { ru: 'Русский', en: 'English', ka: 'ქართული' };
const label = (map: Record<string, string>, k: string) => map[k] || k;
const num = (n?: number) => Number(n || 0).toLocaleString('ru-RU');
const sec = (ms?: number) => (ms ? `${(ms / 1000).toFixed(1)} с` : '—');

/** "rent · district=Vake · beds=2" → "Аренда · Район: Vake · Спальни: 2". */
export function searchLabel(key: string): string {
  const parts = String(key).split(' · ');
  const svc = label(SVC, parts.shift() || '');
  return [svc, ...parts.map((p) => {
    const i = p.indexOf('=');
    if (i < 0) return p;
    const k = p.slice(0, i);
    let v = p.slice(i + 1);
    if (v === '1' && /^(pets|excl|notfirst|notlast)$/.test(k)) return label(KEYS, k);
    if (k === 'district') v = v.split(',').join(', ');
    const rg = v.match(/^(\d*)-(\d*)$/);
    if (rg) v = rg[1] && rg[2] ? `${rg[1]}–${rg[2]}` : rg[1] ? `от ${rg[1]}` : `до ${rg[2]}`;
    return `${label(KEYS, k)}: ${v}`;
  })].join(' · ');
}

function Change({ cur, prev }: { cur: number; prev: number }) {
  if (!prev) return null;
  const d = Math.round(((cur - prev) / prev) * 100);
  if (!d) return <span className="sa-ch" title="К прошлому периоду той же длины">±0%</span>;
  return <span className={`sa-ch ${d > 0 ? 'up' : 'down'}`} title="К прошлому периоду той же длины">{d > 0 ? '+' : ''}{d}% к прошлому</span>;
}

function Kpi({ title, cur, prev, suffix = '' }: { title: string; cur: number; prev: number; suffix?: string }) {
  return (
    <div className="card stat">
      <span className="label">{title}</span>
      <div className="stat-value">{num(cur)}{suffix}</div>
      <div className="stat-sub"><Change cur={Number(cur) || 0} prev={Number(prev) || 0} /></div>
    </div>
  );
}

function Nothing() { return <p className="sa-empty">Пока нет данных за этот период</p>; }

function Bars({ data, map }: { data?: Record<string, number>; map?: Record<string, string> }) {
  const list = Object.entries(data || {}).map(([k, n]) => ({ k, n })).sort((a, b) => b.n - a.n);
  if (!list.length) return <Nothing />;
  const max = list[0].n || 1;
  return (
    <div className="sa-bars">
      {list.map((x) => (
        <div key={x.k} className="sa-bar">
          <span className="ellipsis">{map ? label(map, x.k) : x.k}</span>
          <i><em style={{ width: `${Math.max(2, (x.n / max) * 100)}%` }} /></i>
          <b className="num">{num(x.n)}</b>
        </div>
      ))}
    </div>
  );
}

interface Col<T> { t: string; n?: boolean; v: (r: T) => ReactNode }
function Table<T>({ cols, rows }: { cols: Col<T>[]; rows?: T[] }) {
  if (!rows?.length) return <Nothing />;
  return (
    <div className="table-wrap">
      <table className="table sa-table">
        <thead><tr>{cols.map((c) => <th key={c.t} className={c.n ? 'n' : ''}>{c.t}</th>)}</tr></thead>
        <tbody>
          {rows.map((r, i) => <tr key={i}>{cols.map((c) => <td key={c.t} className={c.n ? 'n num' : ''}>{c.v(r)}</td>)}</tr>)}
        </tbody>
      </table>
    </div>
  );
}

const ListingLink = ({ id }: { id: string }) => <a href={`${SITE}#/listing/${encodeURIComponent(id)}`} target="_blank" rel="noopener noreferrer">{id}</a>;
const pct = (v: number) => `${v}%`;

function Chart({ daily }: { daily: SiteReport['daily'] }) {
  if (!daily?.length) return <Nothing />;
  const w = 640, h = 180, pad = 22, n = daily.length;
  const max = Math.max(1, ...daily.map((d) => d.visitors));
  const cmax = Math.max(1, ...daily.map((d) => d.contacts));
  const bw = (w - pad * 2) / n;
  const step = Math.ceil(n / 8);
  const line = daily.map((d, i) => `${pad + i * bw + bw / 2},${h - pad - (d.contacts / cmax) * (h - pad * 2)}`).join(' ');
  return (
    <>
      <svg className="sa-chart" viewBox={`0 0 ${w} ${h}`} role="img" aria-label="Посетители и обращения по дням">
        {daily.map((d, i) => {
          const bh = (d.visitors / max) * (h - pad * 2);
          return (
            <rect key={d.d} x={pad + i * bw + bw * 0.15} y={h - pad - bh} width={bw * 0.7} height={Math.max(0, bh)} rx={3} className="v">
              <title>{`${d.d}: ${d.visitors} посетителей, ${d.contacts} обращений`}</title>
            </rect>
          );
        })}
        <polyline points={line} className="c" />
        {daily.map((d, i) => (i % step ? null : (
          <text key={d.d} x={pad + i * bw + bw / 2} y={h - 6} textAnchor="middle">{`${d.d.slice(8)}.${d.d.slice(5, 7)}`}</text>
        )))}
      </svg>
      <div className="sa-legend"><span><i className="v" />Посетители</span><span><i className="c" />Обращения</span></div>
    </>
  );
}

function LinkBuilder() {
  const [target, setTarget] = useState('');
  const [src, setSrc] = useState('instagram');
  const [cmp, setCmp] = useState('');
  const [copied, setCopied] = useState(false);
  const url = useMemo(() => {
    const c = cmp.trim().toLowerCase().replace(/[^a-z0-9а-яё_-]+/gi, '-').replace(/^-+|-+$/g, '');
    const t = target.trim().replace(/^#?\/?listing\//, '');
    return `${SITE}?utm_source=${src}${c ? `&utm_campaign=${encodeURIComponent(c)}` : ''}${t ? `#/listing/${encodeURIComponent(t)}` : ''}`;
  }, [target, src, cmp]);
  useEffect(() => { setCopied(false); }, [url]);
  const copy = () => { navigator.clipboard?.writeText(url).then(() => setCopied(true), () => setCopied(false)); };
  return (
    <div className="sa-lb">
      <label className="field"><span className="label">Куда ведёт</span>
        <input className="input" value={target} onChange={(e) => setTarget(e.target.value)} placeholder="Номер объекта, например rent-548893, или пусто — главная" /></label>
      <label className="field"><span className="label">Где публикуете</span>
        <Select value={src} onChange={(e) => setSrc(e.target.value)} options={['instagram', 'facebook', 'tiktok', 'youtube', 'threads', 'telegram', 'whatsapp'].map((s) => ({ value: s, label: label(SRC, s) }))} /></label>
      <label className="field"><span className="label">Название публикации</span>
        <input className="input" value={cmp} onChange={(e) => setCmp(e.target.value)} placeholder="Например reels-vake-2br" /></label>
      <div className="sa-lb-out">
        <input className="input" readOnly value={url} aria-label="Готовая ссылка" onFocus={(e) => e.target.select()} />
        <Button onClick={copy}>{copied ? 'Скопировано' : 'Копировать'}</Button>
      </div>
    </div>
  );
}

/** A report block: title, an optional one-line note, the content. */
function Sec({ title, note, wide, children }: { title: string; note?: string; wide?: boolean; children: ReactNode }) {
  return (
    <Card className={wide ? 'sa-wide' : ''} title={title}>
      {note && <p className="sa-note">{note}</p>}
      {children}
    </Card>
  );
}

const listingCols = (extra: Col<SiteListing>[]): Col<SiteListing>[] => [{ t: 'Объект', v: (x) => <ListingLink id={x.id} /> }, ...extra];

export function AnalyticsPage() {
  const [days, setDays] = useState(7);
  const [fresh, setFresh] = useState(0);
  const q = useQuery({
    queryKey: ['analytics', 'site', days, fresh],
    queryFn: () => api.analytics.site(days, fresh > 0),
    staleTime: 120_000,
    retry: false,
  });
  const r = q.data;
  const refresh = () => setFresh((x) => x + 1);

  return (
    <div className="col">
      <PageHead label="Только для суперадминов" title="Аналитика"
        actions={<>
          <Seg value={days} onChange={setDays} options={PERIODS} />
          <Button variant="secondary" onClick={refresh} loading={q.isFetching} title="Обновить"><RefreshCw size={16} /><span className="hide-mobile">Обновить</span></Button>
        </>} />
      <p className="muted" style={{ marginTop: -6 }}>Сайт hge.ge · свой счётчик, без Google и cookie. Данные обновляются раз в 2 минуты.</p>

      {q.isLoading ? <Loading /> : q.error ? (
        <div className="col"><ErrorBox error={q.error} /><div><Button variant="secondary" onClick={refresh}>Повторить</Button></div></div>
      ) : r ? <Report r={r} /> : null}
    </div>
  );
}

function Report({ r }: { r: SiteReport }) {
  const t = r.totals, p = r.prev, perf = r.perf || ({} as SiteReport['perf']);
  return (
    <>
      <div className="grid sa-kpis">
        <Kpi title="Посетители" cur={t.visitors} prev={p.visitors} />
        <Kpi title="Новые" cur={t.newVisitors} prev={p.newVisitors} />
        <Kpi title="Просмотры объектов" cur={t.listingViews} prev={p.listingViews} />
        <Kpi title="Обращения" cur={t.contacts} prev={p.contacts} />
        <Kpi title="Конверсия" cur={t.conversion} prev={p.conversion} suffix="%" />
        <Kpi title="Поделились" cur={t.shares} prev={p.shares} />
        <Kpi title="В избранное" cur={t.favs} prev={p.favs} />
      </div>
      <div className="grid sa-grid">
        <Sec wide title="По дням" note="Столбцы — посетители, линия — обращения"><Chart daily={r.daily} /></Sec>
        <Sec title="Как связываются" note="Звонок, WhatsApp, Telegram, офис"><Bars data={r.contactsBy} map={CH} /></Sec>
        <Sec title="Откуда приходят">
          <Table rows={r.sources} cols={[
            { t: 'Источник', v: (x) => label(SRC, x.name) },
            { t: 'Визиты', n: true, v: (x) => num(x.sessions) },
            { t: 'Обращения', n: true, v: (x) => num(x.contacts) },
            { t: 'Конв.', n: true, v: (x) => pct(x.conversion) }]} />
        </Sec>
        <Sec title="Какие публикации сработали" note="Ссылки с меткой и из «Поделиться»">
          <Table rows={r.campaigns} cols={[
            { t: 'Публикация', v: (x) => label(CH, x.name) },
            { t: 'Где', v: (x) => label(SRC, x.source || '') },
            { t: 'Визиты', n: true, v: (x) => num(x.sessions) },
            { t: 'Обращения', n: true, v: (x) => num(x.contacts) }]} />
        </Sec>
        <Sec title="Объекты с обращениями">
          <Table rows={r.bestListings} cols={listingCols([
            { t: 'Смотрели', n: true, v: (x) => num(x.viewers) },
            { t: 'Обращения', n: true, v: (x) => num(x.contacts) },
            { t: 'Конв.', n: true, v: (x) => pct(x.conversion) }])} />
        </Sec>
        <Sec title="Смотрят, но не пишут" note="5+ человек открыли, никто не связался: проверьте цену и фото">
          <Table rows={r.noResponse} cols={listingCols([
            { t: 'Раздел', v: (x) => label(SVC, x.service) },
            { t: 'Смотрели', n: true, v: (x) => num(x.viewers) },
            { t: 'В избр.', n: true, v: (x) => num(x.favs) }])} />
        </Sec>
        <Sec title="Агенты" note="По объектам, которые смотрели за период">
          <Table rows={r.agents} cols={[
            { t: 'Агент', v: (x) => (x.name === '—' ? 'Без агента' : `@${x.name}`) },
            { t: 'Объектов', n: true, v: (x) => num(x.listings) },
            { t: 'Просмотры', n: true, v: (x) => num(x.views) },
            { t: 'Обращения', n: true, v: (x) => num(x.contacts) },
            { t: 'Конв.', n: true, v: (x) => pct(x.conversion) }]} />
        </Sec>
        <Sec title="Искали и не нашли" note="Какие объекты искать у собственников">
          <Table rows={r.zeroSearches} cols={[{ t: 'Запрос', v: (x) => searchLabel(x.name) }, { t: 'Раз', n: true, v: (x) => num(x.n) }]} />
        </Sec>
        <Sec title="Что ищут чаще всего">
          <Table rows={r.searches} cols={[{ t: 'Запрос', v: (x) => searchLabel(x.name) }, { t: 'Раз', n: true, v: (x) => num(x.n) }]} />
        </Sec>
        <Sec title="Самые просматриваемые">
          <Table rows={r.listings?.slice(0, 15)} cols={listingCols([
            { t: 'Раздел', v: (x) => label(SVC, x.service) },
            { t: 'Смотрели', n: true, v: (x) => num(x.viewers) },
            { t: 'Обращения', n: true, v: (x) => num(x.contacts) }])} />
        </Sec>
        <Sec title="Чем делятся"><Bars data={r.sharesBy} map={CH} /></Sec>
        <Sec title="Переходы в соцсети и каналы"><Bars data={r.socialBy} /></Sec>
        <Sec title="Устройства"><Bars data={r.devices} map={DEV} /></Sec>
        <Sec title="Язык сайта"><Bars data={r.langs} map={LANG} /></Sec>
        <Sec title="Скорость сайта" note="Обычно: половина посетителей быстрее; у медленных: четверть медленнее">
          <div className="sa-perf">
            <div><span>Загрузка (обычно)</span><b>{sec(perf.loadMedian)}</b></div>
            <div><span>Загрузка (у медленных)</span><b>{sec(perf.loadP75)}</b></div>
            <div><span>Главная картинка</span><b>{sec(perf.lcpMedian)}</b></div>
            {Object.entries(perf.byDevice || {}).map(([k, x]) => <div key={k}><span>{label(DEV, k)}</span><b>{sec(x.median)}</b></div>)}
          </div>
        </Sec>
        <Sec wide title="Ошибки у посетителей" note="Если здесь что-то появилось, покажите это Claude">
          <Table rows={r.errors} cols={[
            { t: 'Ошибка', v: (x) => <code>{x.name}</code> },
            { t: 'Где', v: (x) => x.page },
            { t: 'Раз', n: true, v: (x) => num(x.n) },
            { t: 'Людей', n: true, v: (x) => num(x.sessions) }]} />
        </Sec>
        <Sec wide title="Ссылка с меткой для поста или Reels" note="В отчёте будет видно, сколько клиентов она привела"><LinkBuilder /></Sec>
      </div>
    </>
  );
}
