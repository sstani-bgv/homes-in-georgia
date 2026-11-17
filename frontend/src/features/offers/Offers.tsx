// «Авто»: hge.ge's cars (backend api/offers.py, services/offers.py). Sales are objects now («Объекты» → Продажа).
// The form follows the rent objects' generator (chips, the place on the map); photos and video sort and open as on an
// object (MediaGrid + Lightbox); one «Опубликовать» to the checked targets: the site and / or Telegram (Stanislav 11-15).
import { useEffect, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CheckCircle2, ExternalLink, Plus, RotateCcw, Send, Upload } from 'lucide-react';
import { api } from '../../api';
import { itemsOf, thumbUrl } from '../../api/client';
import type { Offer, OfferStatus } from '../../api/types';
import { useAuth } from '../../auth/AuthContext';
import { AddressField } from '../../components/AddressField';
import { errText, useToast } from '../../components/toast';
import { Badge, Button, Card, Empty, ErrorBox, Field, Input, Lightbox, Loading, PageHead, Seg, Select, Switch, Tabs, Textarea } from '../../components/ui';
import { money } from '../../lib/format';
import { pool, shrinkPhoto } from '../../lib/upload';
import { albumOf, Chips, MediaGrid, PublishTargets, type Shot } from '../properties/PropertyDetail';

const KIND = 'car' as const;
const PATH = '/cars';
const SITE: Record<OfferStatus, [string, 'ok' | 'grey' | 'warn']> = { draft: ['не на сайте', 'grey'], published: ['на сайте', 'ok'], archived: ['архив', 'warn'] };
const CURRENCY = [{ value: 'USD', label: '$ USD' }, { value: 'GEL', label: '₾ GEL' }, { value: 'EUR', label: '€ EUR' }];
const GEARBOX: Record<string, string> = { auto: 'Автомат', manual: 'Механика' };
const FUEL: Record<string, string> = { petrol: 'Бензин', diesel: 'Дизель', hybrid: 'Гибрид', plugin_hybrid: 'Plug-in гибрид', electric: 'Электро', gas: 'Газ' };
const BODY: Record<string, string> = { sedan: 'Седан', suv: 'Внедорожник', crossover: 'Кроссовер', hatchback: 'Хэтчбек', wagon: 'Универсал', coupe: 'Купе',
  convertible: 'Кабриолет', minivan: 'Минивэн', cargo_van: 'Фургон', pickup: 'Пикап', business: 'Бизнес' };
// the post generator's words (backend services/car_post.py): the value is the hashtag itself
const tags = (...xs: string[]) => Object.fromEntries(xs.map((x) => [x, x]));
const TRANSMISSION = { Automatic: 'Автомат', Manual: 'Механика', DCT: 'Робот DCT', AutomatedManual: 'Роботизированная', CVT: 'Вариатор', Robot: 'Робот' };
const DRIVE = tags('4WD', 'AWD', 'FWD', 'RWD', '2WD');
const CONDITION = { Excellent: 'Отличное', Good: 'Хорошее', Fair: 'Среднее', NeedsRepair: 'Требует ремонта' };
const COLOR = { Black: 'Чёрный', White: 'Белый', Silver: 'Серебристый', Gray: 'Серый', Blue: 'Синий', Red: 'Красный', Green: 'Зелёный',
  Brown: 'Коричневый', Beige: 'Бежевый', Yellow: 'Жёлтый', Orange: 'Оранжевый', Gold: 'Золотой', Purple: 'Фиолетовый' };
const SALE_OPTIONS = tags('AC', 'ClimateControl', 'DualClimate', 'RearviewCamera', '360Camera', 'ParkingSensors', 'MultifunctionSteering', 'Sunroof',
  'Panorama', 'HeatedSeats', 'VentilatedSeats', 'HeatedSteering', 'LeatherSeats', 'ElectricSeats', 'EcoMode', 'SportMode', 'MultimediaSystem',
  'Display', 'Navigation', 'Bluetooth', 'CarPlay', 'AndroidAuto', 'LightSensor', 'RainSensor', 'CruiseControl', 'AdaptiveCruise', 'KeylessEntry',
  'StartStop', 'AlloyWheels', 'LEDHeadlights', 'BlindSpot', 'LaneAssist');
const SAFETY = tags('ABS', 'ESP', 'Airbags', 'TractionControl', 'HillAssist', 'ISOFIX', 'TPMS', 'EmergencyBraking');
const RENT_OPTIONS: [string, Record<string, string>][] = [
  ['🧊 Климат', tags('AC', 'ClimateControl', 'DualClimate', 'HeatedSeats', 'HeatedSteering', 'VentilatedSeats')],
  ['📲 Мультимедиа', tags('Bluetooth', 'Multimedia', 'USB', 'AppleCarPlay', 'AndroidAuto', 'Navigation', 'TouchScreen')],
  ['🛞 Помощники', tags('CruiseControl', 'AdaptiveCruise', 'EcoMode', 'SportMode', 'RearCamera', '360Camera', 'ParkingSensors', 'Panorama', 'Sunroof',
    'KeylessEntry', 'StartStop', 'AlloyWheels', 'LargeTrunk')],
];
const CAR_CLASS = { EconomyClass: 'Эконом', ComfortClass: 'Комфорт', BusinessClass: 'Бизнес', PremiumClass: 'Премиум', LuxuryClass: 'Люкс' };
const PETS = { allowed: 'Можно', no: 'Нельзя', agreement: 'По договорённости' };
const EXTRAS: [string, string][] = [['child_seat', '👶 Детское кресло'], ['booster', '💺 Бустер'], ['chains', '🔗 Цепи'], ['winter_tires', '❄️ Зимняя резина'],
  ['second_driver', '👥 Второй водитель'], ['with_driver', '🧑‍✈️ С водителем'], ['sim', '📶 SIM-карта']];
const DELIVERY = 'Tbilisi / Airport / City Center';
const MAX_PHOTO = 40;
const TABS: { value: OfferStatus | 'all'; label: string }[] = [{ value: 'all', label: 'Все' }, { value: 'draft', label: 'Не на сайте' }, { value: 'published', label: 'На сайте' }, { value: 'archived', label: 'Архив' }];
const siteUrl = (o: Offer) => `https://hge.ge/listing/${o.item_id}`;
const shots = (o: Offer): Shot[] => [...o.photos.map((k) => ({ id: k, kind: 'photo' as const, room: null, src: k })), ...(o.video ? [{ id: o.video, kind: 'video' as const, room: null, src: o.video }] : [])];
const what = (o: Offer | Draft) => [o.deal === 'sale' ? 'Продажа' : 'Аренда', BODY[String(o.body)]].filter(Boolean).join(' · ');
const fromChannel = (o: Offer) => o.source === 'telegram';
const priceOf = (o: Offer) => (o.price != null ? `${money(o.price, o.currency)}${o.deal === 'rent' ? ` / ${o.period === 'month' ? 'мес' : 'день'}` : ''}` : '—');

export function OffersPage() {
  const nav = useNavigate();
  const [tab, setTab] = useState<OfferStatus | 'all'>('all');
  const [q, setQ] = useState('');
  const [view, setView] = useState<{ o: Offer; i: number } | null>(null);
  const list = useQuery({ queryKey: ['offers', KIND, tab, q], queryFn: () => api.offers.list(KIND, { status: tab === 'all' ? undefined : tab, q: q || undefined }) });
  const rows = itemsOf(list.data);
  return (
    <div className="col gap-lg">
      <PageHead label="Объекты сайта" title="Авто" actions={<Button onClick={() => nav(`${PATH}/new`)}><Plus size={16} />Новое авто</Button>} />
      <Card>
        <div className="col gap">
          <Tabs value={tab} onChange={setTab} tabs={TABS} />
          <Input placeholder="Поиск по модели" value={q} onChange={(e) => setQ(e.target.value)} />
          {list.isLoading ? <Loading /> : list.error ? <ErrorBox error={list.error} /> : rows.length === 0 ? <Empty text="Пока пусто" /> : (
            <div className="table-wrap">
              <table className="table">
                <thead><tr><th /><th>Модель</th><th>Тип</th><th>Цена</th><th>Сайт</th><th>Telegram</th></tr></thead>
                <tbody>
                  {rows.map((o) => (
                    <tr key={o.id} style={{ cursor: 'pointer' }} onClick={() => nav(`${PATH}/${o.id}`)}>
                      <td style={{ width: 56 }}>{o.photos[0] ? (
                        <img src={thumbUrl(o.photos[0])} alt="" width={48} height={48} style={{ objectFit: 'cover', borderRadius: 6, cursor: 'zoom-in' }}
                          onClick={(e) => { e.stopPropagation(); setView({ o, i: 0 }); }} />) : null}</td>
                      <td><b>{o.title}</b>{o.year ? <small className="muted"> · {String(o.year)}</small> : null}{fromChannel(o) && <> <Badge tone="blue">из канала</Badge></>}</td>
                      <td>{what(o)}</td>
                      <td>{priceOf(o)}</td>
                      <td>{o.public ? <a href={siteUrl(o)} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}><Badge tone="ok">на сайте</Badge></a> : <Badge tone={SITE[o.status][1]}>{SITE[o.status][0]}</Badge>}</td>
                      <td>{o.post_url ? <a href={o.post_url} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}>пост <ExternalLink size={12} /></a> : <span className="faint">—</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </Card>
      {view && <Lightbox items={shots(view.o)} index={view.i} onIndex={(i) => setView({ ...view, i })} onClose={() => setView(null)} />}
    </div>
  );
}

type Draft = Record<string, unknown>;
type PF = Record<string, unknown>;
type Extra = { key: string; price: number | string };
// the rent channel's usual terms; a sale gets the after-sale block
const RENT_PF: PF = { insurance_basic: 10, insurance_full: 15, pets: 'allowed', unlimited_mileage: true, delivery: DELIVERY, drive: '4WD',
  options: ['AC', 'ClimateControl', 'HeatedSeats', 'Bluetooth', 'Multimedia', 'USB', 'CruiseControl', 'EcoMode'],
  extras: EXTRAS.slice(0, 5).map(([key]) => ({ key, price: 3 })) };
const SALE_PF: PF = { after_sale: true };
const NEW: Draft = { deal: 'rent', period: 'day', currency: 'USD', post_fields: RENT_PF };
const KEYS = ['deal', 'title', 'period', 'deposit', 'year', 'gearbox', 'fuel', 'body', 'seats', 'mileage', 'pickup', 'lat', 'lng', 'price', 'currency', 'description', 'is_exclusive'];
const NUM = new Set(['price', 'deposit', 'year', 'seats', 'mileage']);
const PF_NUM = new Set(['hp', 'fuel_use', 'trunk', 'insurance_basic', 'insurance_full']);
const s = (v: unknown) => (v == null ? '' : String(v));
const pfOf = (d: Draft): PF => (d.post_fields as PF) || {};
const lines = (v: unknown) => (Array.isArray(v) ? v.join('\n') : s(v));
const toLines = (v: string) => v.split('\n').map((x) => x.replace(/^[✅•\-\s]+/, '').trim()).filter(Boolean);

/** the form's values -> the request body (empty string = cleared field) */
function body(d: Draft): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const k of KEYS) {
    const v = d[k];
    out[k] = k === 'is_exclusive' ? !!v : v === '' || v == null ? null : NUM.has(k) ? Number(v) : v;
  }
  if (d.deal === 'sale') Object.assign(out, { period: null, deposit: null });
  else out.mileage = null;
  const pf: PF = {};
  for (const [k, v] of Object.entries(pfOf(d))) {
    pf[k] = v === '' || v == null ? null : PF_NUM.has(k) ? Number(v)
      : k === 'extras' ? (v as Extra[]).map((e) => ({ key: e.key, price: Number(e.price) || 0 })) : v;
  }
  out.post_fields = pf;
  return out;
}

/** Several values at once (options, safety): a tap toggles the hashtag. */
function MultiChips({ options, value, onChange }: { options: Record<string, string>; value: string[]; onChange: (v: string[]) => void }) {
  const on = new Set(value);
  return (
    <div className="row row-wrap gap-sm">
      {Object.entries(options).map(([v, l]) => (
        <button key={v} type="button" className={`chip ${on.has(v) ? 'active' : ''}`} aria-pressed={on.has(v)}
          onClick={() => onChange(on.has(v) ? value.filter((x) => x !== v) : [...value, v])}>{on.has(v) ? <CheckCircle2 size={14} /> : null}{l}</button>
      ))}
    </div>
  );
}

function CarForm({ d, set }: { d: Draft; set: (patch: Draft) => void }) {
  const rent = d.deal !== 'sale';
  const pf = pfOf(d);
  const setPf = (patch: PF) => set({ post_fields: { ...pf, ...patch } });
  const list = (k: string) => (Array.isArray(pf[k]) ? (pf[k] as string[]) : []);
  const point = d.lat != null && d.lng != null ? { lat: Number(d.lat), lng: Number(d.lng) } : null;
  const num = (label: string, k: string, hint?: string) => (
    <Field label={label} hint={hint}><Input type="number" inputMode="decimal" value={s(pf[k])} onChange={(e) => setPf({ [k]: e.target.value })} /></Field>);
  const extras = (Array.isArray(pf.extras) ? pf.extras : []) as Extra[];
  const deal = (v: string) => set({ deal: v, post_fields: { ...(v === 'sale' ? SALE_PF : RENT_PF), ...pf } });
  return (
    <>
      <div className="gen-block">
        <span className="gen-title">1 · Основное</span>
        <Seg value={rent ? 'rent' : 'sale'} onChange={deal} options={[{ value: 'rent', label: '🔑 Аренда' }, { value: 'sale', label: '💼 Продажа' }]} />
        <Switch checked={!!d.is_exclusive} onChange={(v) => set({ is_exclusive: v })} label="❗️ Exclusive listing" />
        <div className="form-grid">
          <Field label="Марка и модель *" hint="Марка — хештег: #Toyota, Mercedes-Benz → #Mercedes"><Input value={s(d.title)} placeholder={rent ? 'Jeep Compass' : 'Ford Escape Titanium'} onChange={(e) => set({ title: e.target.value })} /></Field>
          <Field label="Год"><Input type="number" inputMode="numeric" value={s(d.year)} onChange={(e) => set({ year: e.target.value })} /></Field>
        </div>
      </div>
      {rent ? (
        <>
          <div className="gen-block">
            <span className="gen-title">2 · Авто</span>
            <Field label="Класс" hint="В заголовке поста: #BusinessClass; без класса — кузов"><Chips options={CAR_CLASS} value={s(pf.car_class) || undefined} onChange={(v) => setPf({ car_class: v ?? '' })} /></Field>
            <Field label="Кузов"><Chips options={BODY} value={s(d.body) || undefined} onChange={(v) => set({ body: v ?? '' })} /></Field>
            <Field label="Коробка"><Chips options={GEARBOX} value={s(d.gearbox) || undefined} onChange={(v) => set({ gearbox: v ?? '' })} /></Field>
            <Field label="Топливо"><Chips options={FUEL} value={s(d.fuel) || undefined} onChange={(v) => set({ fuel: v ?? '' })} /></Field>
            <Field label="Привод"><Chips options={DRIVE} value={s(pf.drive) || undefined} onChange={(v) => setPf({ drive: v ?? '' })} /></Field>
            <div className="form-grid">
              <Field label="Мест"><Input type="number" inputMode="numeric" value={s(d.seats)} onChange={(e) => set({ seats: e.target.value })} /></Field>
              {num('Багажник, л', 'trunk')}
              {num('Расход, л / 100 км', 'fuel_use')}
            </div>
          </div>
          <div className="gen-block">
            <span className="gen-title">3 · Опции</span>
            {RENT_OPTIONS.map(([label, opts]) => (
              <Field key={label} label={label}><MultiChips options={opts} value={list('options')} onChange={(v) => setPf({ options: v })} /></Field>
            ))}
          </div>
          <div className="gen-block">
            <span className="gen-title">4 · Условия</span>
            <Field label="🐶 Животные"><Chips options={PETS} value={s(pf.pets) || undefined} onChange={(v) => setPf({ pets: v ?? '' })} /></Field>
            <Switch checked={pf.smoking === true} onChange={(v) => setPf({ smoking: v })} label="🚬 Курить можно" />
            <Switch checked={pf.unlimited_mileage !== false} onChange={(v) => setPf({ unlimited_mileage: v })} label="❗️ Без лимита пробега (UnlimitedMileage)" />
          </div>
          <div className="gen-block">
            <span className="gen-title">5 · Цена</span>
            <div className="form-grid">
              <Field label="Цена"><Input type="number" inputMode="numeric" value={s(d.price)} onChange={(e) => set({ price: e.target.value })} /></Field>
              <Field label="Валюта"><Select value={s(d.currency) || 'USD'} onChange={(e) => set({ currency: e.target.value })} options={CURRENCY} /></Field>
              <Field label="Цена за"><Seg value={s(d.period) || 'day'} onChange={(v) => set({ period: v })} options={[{ value: 'day', label: 'день' }, { value: 'month', label: 'месяц' }]} /></Field>
              <Field label="Залог"><Input type="number" inputMode="numeric" value={s(d.deposit)} onChange={(e) => set({ deposit: e.target.value })} /></Field>
              {num('Базовая страховка', 'insurance_basic')}
              {num('Полная страховка', 'insurance_full')}
            </div>
          </div>
          <div className="gen-block">
            <span className="gen-title">6 · Доп. услуги</span>
            <div className="col gap-sm">
              {EXTRAS.map(([key, label]) => {
                const e = extras.find((x) => x.key === key);
                const put = (next: Extra[]) => setPf({ extras: EXTRAS.map(([k]) => next.find((x) => x.key === k)).filter(Boolean) });
                return (
                  <div key={key} className="row gap-sm" style={{ alignItems: 'center' }}>
                    <div style={{ flex: 1 }}><Switch checked={!!e} onChange={(on) => put(on ? [...extras, { key, price: 3 }] : extras.filter((x) => x.key !== key))} label={label} /></div>
                    {e && <Input type="number" inputMode="numeric" style={{ width: 96 }} value={s(e.price)} placeholder="0 = Free"
                      onChange={(ev) => put(extras.map((x) => (x.key === key ? { ...x, price: ev.target.value } : x)))} />}
                    {e && <span className="muted" style={{ fontSize: 12 }}>/ день</span>}
                  </div>
                );
              })}
            </div>
          </div>
          <div className="gen-block">
            <span className="gen-title">7 · Где забрать</span>
            <AddressField value={s(d.pickup)} onChange={(v) => set({ pickup: v })} point={point} onPoint={(p) => set({ lat: p?.lat ?? null, lng: p?.lng ?? null })} />
            <Field label="📍 FREE Pick-up & drop-off" hint="Пусто — строки не будет">
              <Input value={pf.delivery === '-' ? '' : s(pf.delivery)} placeholder={DELIVERY} onChange={(e) => setPf({ delivery: e.target.value || '-' })} />
            </Field>
          </div>
        </>
      ) : (
        <>
          <div className="gen-block">
            <span className="gen-title">2 · Двигатель и коробка</span>
            <Field label="Топливо"><Chips options={FUEL} value={s(d.fuel) || undefined} onChange={(v) => set({ fuel: v ?? '' })} /></Field>
            <div className="form-grid">
              <Field label="Двигатель" hint="Первое слово — хештег: I4 → #I4, V8 → #V8"><Input value={s(pf.engine)} placeholder="I4 2.0L EcoBoost Turbocharged" onChange={(e) => setPf({ engine: e.target.value })} /></Field>
              {num('Мощность, HP', 'hp')}
            </div>
            <Field label="Коробка"><Chips options={TRANSMISSION} value={s(pf.transmission) || undefined}
              onChange={(v) => set({ gearbox: v ? (v === 'Manual' ? 'manual' : 'auto') : '', post_fields: { ...pf, transmission: v ?? '' } })} /></Field>
            <Field label="Привод"><Chips options={DRIVE} value={s(pf.drive) || undefined} onChange={(v) => setPf({ drive: v ?? '' })} /></Field>
          </div>
          <div className="gen-block">
            <span className="gen-title">3 · Кузов и пробег</span>
            <Field label="Кузов"><Chips options={BODY} value={s(d.body) || undefined} onChange={(v) => set({ body: v ?? '' })} /></Field>
            <Field label="Цвет"><Chips options={COLOR} value={s(pf.color) || undefined} onChange={(v) => setPf({ color: v ?? '' })} /></Field>
            <div className="form-grid">
              <Field label="Пробег, км" hint="Хештег пробега: #From200000"><Input type="number" inputMode="numeric" value={s(d.mileage)} onChange={(e) => set({ mileage: e.target.value })} /></Field>
              <Field label="Мест"><Input type="number" inputMode="numeric" value={s(d.seats)} onChange={(e) => set({ seats: e.target.value })} /></Field>
            </div>
          </div>
          <div className="gen-block">
            <span className="gen-title">4 · Опции и безопасность</span>
            <Field label="✨ OPTIONS"><MultiChips options={SALE_OPTIONS} value={list('options')} onChange={(v) => setPf({ options: v })} /></Field>
            <Field label="🛡 SAFETY"><MultiChips options={SAFETY} value={list('safety')} onChange={(v) => setPf({ safety: v })} /></Field>
          </div>
          <div className="gen-block">
            <span className="gen-title">5 · Состояние</span>
            <Field label="🔧 CONDITION"><Chips options={CONDITION} value={s(pf.condition) || undefined} onChange={(v) => setPf({ condition: v ?? '' })} /></Field>
            <Field label="Описание состояния" hint="Абзац под CONDITION; он же — описание на сайте">
              <Textarea rows={4} value={s(d.description)} placeholder="The engine runs well, the transmission shifts smoothly…" onChange={(e) => set({ description: e.target.value })} />
            </Field>
            <Field label="✅ Что сделано" hint="Каждая строка — отдельный пункт ✅">
              <Textarea rows={3} value={lines(pf.maintenance)} placeholder="Recent maintenance includes an oil change" onChange={(e) => setPf({ maintenance: toLines(e.target.value) })} />
            </Field>
            <Field label="Cosmetic imperfections" hint="Каждая строка — пункт •">
              <Textarea rows={3} value={lines(pf.cosmetic)} placeholder="Minor chips on the alloy wheels" onChange={(e) => setPf({ cosmetic: toLines(e.target.value) })} />
            </Field>
            <Field label="🔎 VIN"><Input value={s(pf.vin)} maxLength={20} style={{ textTransform: 'uppercase' }} onChange={(e) => setPf({ vin: e.target.value.toUpperCase() })} /></Field>
            <Switch checked={pf.after_sale !== false} onChange={(v) => setPf({ after_sale: v })} label="🛠 Блок AFTER-SALE SERVICES" />
          </div>
          <div className="gen-block">
            <span className="gen-title">6 · Цена</span>
            <div className="form-grid">
              <Field label="Цена" hint="Хештеги цены: #Price5000to10000"><Input type="number" inputMode="numeric" value={s(d.price)} onChange={(e) => set({ price: e.target.value })} /></Field>
              <Field label="Валюта"><Select value={s(d.currency) || 'USD'} onChange={(e) => set({ currency: e.target.value })} options={CURRENCY} /></Field>
            </div>
          </div>
          <div className="gen-block">
            <span className="gen-title">7 · Где посмотреть</span>
            <AddressField value={s(d.pickup)} onChange={(v) => set({ pickup: v })} point={point} onPoint={(p) => set({ lat: p?.lat ?? null, lng: p?.lng ?? null })} />
          </div>
        </>
      )}
    </>
  );
}

/** The post as it will look in the channel, rebuilt from the form while typing (POST /offers/car/preview, nothing saved). */
function PostPreview({ d }: { d: Draft }) {
  const [text, setText] = useState('');
  const [err, setErr] = useState(false);
  const json = JSON.stringify(body(d));
  useEffect(() => {
    const t = setTimeout(() => {
      api.offers.preview(KIND, JSON.parse(json)).then((r) => { setText(r.text); setErr(false); }, () => setErr(true));
    }, 400);
    return () => clearTimeout(t);
  }, [json]);
  return (
    <Card label={d.deal === 'sale' ? 'Канал CarsSaleInGeorgia' : 'Канал CarsRentInGeorgia'} title="Предпросмотр поста">
      {err ? <div className="alert alert-warn">Не удалось собрать пост — проверьте поля</div>
        : <pre style={{ whiteSpace: 'pre-wrap', fontFamily: 'inherit', fontSize: 13, lineHeight: 1.45, margin: 0 }}>{text || '…'}</pre>}
    </Card>
  );
}

/** Photos and video as on an object: up to 9 photos + 1 video, drag to sort, a tap opens the Lightbox. */
function Media({ o, onChange }: { o: Offer; onChange: (o: Offer) => void }) {
  const toast = useToast();
  const file = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState<Record<number, number>>({});
  const run = (p: Promise<Offer>) => p.then(onChange, (e) => toast(errText(e), true));
  const pick = async (files: File[]) => {
    let ph = o.photos.length, vd = o.video ? 1 : 0;
    const isVideo = (f: File) => f.type.startsWith('video/');
    const fit = files.filter((f) => (isVideo(f) ? ++vd <= 1 : ++ph <= MAX_PHOTO));
    if (fit.length < files.length) toast(`Не добавлено ${files.length - fit.length}: максимум ${MAX_PHOTO} фото и 1 видео`, true);
    await pool(fit, 3, async (f, i) => {
      try {
        const small = await shrinkPhoto(f);
        onChange(await api.offers.upload(KIND, o.id, small, (p) => setBusy((b) => ({ ...b, [i]: Math.round(p * 100) }))));
      } catch (e) { toast(errText(e), true); }
      setBusy((b) => { const { [i]: _, ...rest } = b; return rest; });
    });
  };
  const full = o.photos.length >= MAX_PHOTO && !!o.video;
  const pending = Object.values(busy);
  return (
    <Card label="Сайт и пост берут первое фото обложкой" title={`Фото ${o.photos.length}/${MAX_PHOTO} · Видео ${o.video ? 1 : 0}/1`}>
      <div className="col gap">
        <input ref={file} type="file" accept="image/*,video/*" multiple hidden onChange={(e) => { const f = [...(e.target.files ?? [])]; e.target.value = ''; if (f.length) pick(f); }} />
        <Button block loading={pending.length > 0} disabled={full} onClick={() => file.current?.click()}><Upload size={16} />{full ? 'Загружено максимум файлов' : 'Загрузить фото и видео'}</Button>
        {pending.length > 0 && <div className="muted" style={{ fontSize: 12 }}>Загружаем: {pending.map((p) => `${p}%`).join(' · ')}</div>}
        <MediaGrid items={shots(o)} grouped
          onOrder={(ids) => run(api.offers.order(KIND, o.id, ids.map(String).filter((k) => k !== o.video)))}
          onDelete={(k) => { if (confirm('Удалить файл?')) run(api.offers.deleteFile(KIND, o.id, String(k))); }} />
      </div>
    </Card>
  );
}

/** Where the car is (site / Telegram) and one «Опубликовать» to the checked targets; the post text and its files picked
 * as on an object (drag, the checkbox, a tap opens it). Telegram: operators, as for objects. */
function Publish({ o, dirty, onChange }: { o: Offer; dirty: boolean; onChange: (o: Offer) => void }) {
  const toast = useToast();
  const { isStaff } = useAuth();
  const post = useQuery({ queryKey: ['offer-post', KIND, o.id, o.updated_at], queryFn: () => api.offers.post(KIND, o.id) });
  const [edited, setEdited] = useState<string | null>(null);
  const [toSite, setToSite] = useState(true);
  const [toTg, setToTg] = useState(true);
  const [order, setOrder] = useState<string[] | null>(null);
  const [off, setOff] = useState<Set<string>>(() => new Set());
  const own = shots(o);
  const known = new Set(own.map((m) => String(m.id)));
  const ordered = [...(order || []).filter((k) => known.has(k)), ...own.map((m) => String(m.id)).filter((k) => !(order || []).includes(k))];
  const items = ordered.map((k) => own.find((m) => m.id === k)!);
  const picked = albumOf(ordered.filter((k) => !off.has(k)), (k) => own.find((m) => m.id === k)?.kind === 'video');
  const text = edited ?? post.data?.text ?? '';
  const tg = toTg && isStaff;
  const publish = useMutation({
    mutationFn: () => api.offers.publish(KIND, o.id, { text: edited ?? undefined, tg, site: toSite, keys: tg ? picked : undefined }),
    onSuccess: (x) => { onChange(x); toast('Опубликовано'); },
    onError: (e) => toast(errText(e), true),
  });
  const status = useMutation({
    mutationFn: (st: OfferStatus) => api.offers.setStatus(KIND, o.id, st),
    onSuccess: (x) => { onChange(x); setToSite(x.status !== 'published'); toast(x.status === 'draft' ? 'Снято с сайта' : 'В архиве'); },
    onError: (e) => toast(errText(e), true),
  });
  const noPhoto = !o.photos.length;
  return (
    <Card title="Публикация">
      <div className="col gap-lg">
        <div className="row row-wrap gap-sm">
          <span className="label">Где опубликовано</span>
          {o.public ? <a href={siteUrl(o)} target="_blank" rel="noreferrer"><Badge tone="ok">на сайте hge.ge</Badge></a> : <Badge tone={SITE[o.status][1]}>{SITE[o.status][0]}</Badge>}
          {o.post_url ? <a href={o.post_url} target="_blank" rel="noreferrer"><Badge tone="ok">в Telegram</Badge></a> : <Badge tone="grey">нет поста в Telegram</Badge>}
          {o.status === 'published' && <Button size="sm" variant="ghost" loading={status.isPending} onClick={() => status.mutate('draft')}>Снять с сайта</Button>}
          {o.status !== 'archived' && <Button size="sm" variant="ghost" loading={status.isPending} onClick={() => { if (confirm('Убрать в архив? С сайта пропадёт.')) status.mutate('archived'); }}>В архив</Button>}
        </div>
        {dirty && <div className="alert alert-warn">Есть несохранённые правки — сначала «Сохранить».</div>}
        {noPhoto && <div className="alert alert-warn">Чтобы опубликовать, добавьте хотя бы одно фото.</div>}
        {tg && (
          <>
            {post.isLoading ? <Loading /> : (
              <Field label="Текст поста" hint="Собран из полей выше, как в генераторе постов.">
                <Textarea value={text} rows={12} onChange={(e) => setEdited(e.target.value)} style={{ fontFamily: 'inherit', lineHeight: 1.45 }} />
              </Field>
            )}
            {edited !== null && <Button size="sm" variant="ghost" onClick={() => setEdited(null)}><RotateCcw size={14} />Исходный текст</Button>}
            <div>
              <div className="row row-between"><span className="label">Фото и видео в посте</span><span className="muted num" style={{ fontWeight: 700 }}>{picked.length}/{albumOf(ordered, (k) => own.find((m) => m.id === k)?.kind === 'video').length}</span></div>
              <div className="muted" style={{ fontSize: 12, margin: '4px 0 8px' }}>Перетащите, чтобы поменять порядок; галочка — файл в посте; нажмите, чтобы открыть. Первый файл — обложка.</div>
              <MediaGrid items={items} grouped off={new Set<Shot['id']>(ordered.filter((k) => !picked.includes(k)))} onOrder={(ids) => setOrder(ids.map(String))}
                onToggle={(k) => setOff((x) => { const n = new Set(x); if (picked.includes(String(k))) n.add(String(k)); else n.delete(String(k)); return n; })} />
            </div>
          </>
        )}
        <PublishTargets site={toSite} tg={tg} onSite={setToSite} onTg={setToTg} tgLocked={isStaff ? undefined : 'В Telegram публикует оператор'} />
        <Button variant="ok" size="lg" loading={publish.isPending} disabled={dirty || noPhoto || (!toSite && !tg) || (tg && !picked.length)}
          onClick={() => { if (!tg || !o.post_url || confirm('Пост уже есть в канале. Опубликовать ещё раз?')) publish.mutate(); }}>
          <Send size={18} />Опубликовать
        </Button>
      </div>
    </Card>
  );
}

export function OfferCardPage() {
  const { id } = useParams();
  const isNew = id === 'new';
  const nav = useNavigate();
  const toast = useToast();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ['offer', KIND, id], queryFn: () => api.offers.get(KIND, Number(id)), enabled: !isNew });
  const [draft, setDraft] = useState<Draft | null>(null);
  const o = q.data;
  const d: Draft = draft ?? (isNew ? NEW : (o as Draft) ?? {});
  const set = (patch: Draft) => setDraft((x) => ({ ...(x ?? d), ...patch }));
  const fresh = (x: Offer) => { qc.setQueryData(['offer', KIND, String(x.id)], x); qc.invalidateQueries({ queryKey: ['offers', KIND] }); };
  const save = useMutation({
    mutationFn: () => (isNew ? api.offers.create(KIND, body(d)) : api.offers.update(KIND, Number(id), body(d))),
    onSuccess: (x) => { setDraft(null); fresh(x); toast('Сохранено'); if (isNew) nav(`${PATH}/${x.id}`, { replace: true }); },
    onError: (e) => toast(errText(e), true),
  });
  if (!isNew && q.isLoading) return <Loading />;
  if (!isNew && !o) return <ErrorBox error={q.error ?? new Error('Не найдено')} />;
  const ready = !!s(d.title).trim();
  return (
    <div className="col gap-lg">
      <div><Link to={PATH} className="muted" style={{ fontSize: 13 }}>← Авто</Link></div>
      <PageHead label={['Авто', what(d)].filter(Boolean).join(' · ')} title={isNew ? 'Новое авто' : String(o!.title)}
        actions={o && <><Badge tone={SITE[o.status][1]}>{SITE[o.status][0]}</Badge>{o.post_url && <Badge tone="blue">в Telegram</Badge>}</>} />
      <Card>
        <div className="col gap-lg">
          {o && fromChannel(o) && <div className="alert">Авто из канала{o.post_url ? <> (<a href={o.post_url} target="_blank" rel="noreferrer">пост</a>)</> : null}: правка поста в канале обновит карточку при следующей синхронизации (каждые 15 минут).</div>}
          <CarForm d={d} set={set} />
          {d.deal !== 'sale' && <Field label="Описание для сайта" hint="В пост не входит"><Textarea rows={4} value={s(d.description)} onChange={(e) => set({ description: e.target.value })} /></Field>}
          <div className="row gap-sm row-wrap">
            <Button size="lg" loading={save.isPending} disabled={!ready || (!isNew && !draft)} onClick={() => save.mutate()}>Сохранить</Button>
            {!ready && <span className="faint" style={{ fontSize: 12, alignSelf: 'center' }}>Укажите модель</span>}
          </div>
        </div>
      </Card>
      <PostPreview d={d} />
      {isNew ? <Empty text="Фото, видео и публикация — после сохранения" /> : o && <>
        <Media o={o} onChange={fresh} />
        <Publish o={o} dirty={!!draft} onChange={fresh} />
      </>}
    </div>
  );
}
