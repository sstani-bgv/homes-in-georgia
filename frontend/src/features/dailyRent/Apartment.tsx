// «Посуточная аренда» → one apartment: its stays and money for a period, the owner's period report, the card (owner,
// split, bills), photos / nightly price / description for the site, and the Airbnb / Booking calendars (iCal).
import { useConfirm } from '../../components/ActionDialog';
import { useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useMutation, useQuery } from '@tanstack/react-query';
import { ArrowLeft, ArrowRight, Copy, ImagePlus, Plus, RefreshCw, Trash2 } from 'lucide-react';
import { mediaUrl, thumbUrl } from '../../api/client';
import { Badge, Button, Card, Checkbox, DistrictSelect, Empty, ErrorBox, Field, Input, Lightbox, Loading, PageHead, Seg, Select, Stat, Switch, Textarea } from '../../components/ui';
import { errText, useToast } from '../../components/toast';
import { pool, shrinkPhoto } from '../../lib/upload';
import { BookingModal, copy, ReportView, useDrInvalidate } from './Booking';
import { cm, dm, dr, LANGS, mix, nightsLabel, PAY_LABEL, STATUS_LABEL, usd, UTIL, type DrApartment, type DrBooking, type Platform } from './api';
import { PeriodBar, usePeriod } from './DailyRent';

export function ApartmentPage() {
  const { id } = useParams();
  const aid = Number(id);
  const [tab, setTab] = useState<'stays' | 'card' | 'site' | 'ical'>('stays');
  const per = usePeriod();
  const q = useQuery({ queryKey: ['dr', 'apt', aid, per.p], queryFn: () => dr.apartment(aid, { ...per.p, label: per.label }) });
  const [open, setOpen] = useState<number | 'new' | null>(null);
  const toast = useToast();
  if (q.isLoading) return <Loading />;
  if (!q.data) return <ErrorBox error={q.error ?? new Error('Не найдено')} />;
  const a = q.data;
  const rows = a.bookings.filter((b) => !per.p.month_from || (b.check_in && b.check_in.slice(0, 7) >= per.p.month_from && b.check_in.slice(0, 7) <= per.p.month_to!));
  const E = a.econ;
  return (
    <div className="col gap-lg">
      <div><Link to="/daily-rent/apartments" className="muted" style={{ fontSize: 13 }}>← Квартиры</Link></div>
      <PageHead label="Посуточная аренда" title={a.name} actions={<>
        {!a.active && <Badge tone="grey">Неактивна</Badge>}<Badge tone={a.status === 'free' ? 'ok' : 'warn'}>{STATUS_LABEL[a.status]}</Badge>
        <Button onClick={() => setOpen('new')}><Plus size={16} />Проживание</Button>
      </>} />
      <Seg value={tab} onChange={setTab} options={[{ value: 'stays', label: 'Проживания' }, { value: 'card', label: 'Карточка' }, { value: 'site', label: 'Фото и сайт' }, { value: 'ical', label: 'Календари площадок' }]} />
      {tab === 'stays' && (
        <>
          <PeriodBar {...per} />
          <div className="grid dr-kpis dr-kpis-4">
            <Stat label="Пришло" value={usd(E.gross_usd)} sub={mix(E.gross)} />
            <Stat label={a.owner_name || 'Собственнику'} value={usd(E.owner_usd)} sub={`${a.owner_pct}% + компенсации · ${mix(E.owner)}`} />
            <Stat label="Ночей" value={E.nights} sub={E.occupancy !== null ? `загрузка ${E.occupancy}%` : `${E.stays} прожив.`} />
            <Stat label="Цена ночи" value={E.nights ? usd(E.adr_usd) : '—'} sub={`расходы и налоги ${usd(E.costs_usd)}`} />
          </div>
          <Card title={`Проживания · ${per.label}`} actions={E.to_pay_usd > 0 ? <Badge tone="warn">к выплате {mix(E.to_pay)}</Badge> : undefined}>
            {rows.length === 0 ? <Empty text="Нет проживаний" /> : <StayTable rows={rows} onOpen={setOpen} />}
          </Card>
          <Card title="Отчёт собственнику за период">
            <ReportView key={a.report_lang} reports={a.period_reports} lang={a.report_lang} toast={toast} />
          </Card>
        </>
      )}
      {tab === 'card' && <AptForm a={a} />}
      {tab === 'site' && <SiteBlock a={a} />}
      {tab === 'ical' && <Channels a={a} />}
      <BookingModal open={open} apt={a} onClose={() => setOpen(null)} />
    </div>
  );
}

export function StayTable({ rows, onOpen, apts }: { rows: DrBooking[]; onOpen: (id: number) => void; apts?: Map<number, string> }) {
  return (
    <div className="table-wrap"><table className="table">
      <thead><tr><th>Даты</th>{apts && <th>Квартира</th>}<th>Гость</th><th>Платформа</th><th>Пришло</th><th>Чистая</th><th>Владельцу</th><th>Оплата</th></tr></thead>
      <tbody>{rows.map((b) => (
        <tr key={b.id} className="clickable" onClick={() => onOpen(b.id)}>
          <td style={{ whiteSpace: 'nowrap' }}>{dm(b.check_in)} — {dm(b.check_out)}<small className="muted"> · {nightsLabel(b.nights)}</small></td>
          {apts && <td>{apts.get(b.apartment_id)}</td>}
          <td>{b.guest_name || '—'}</td>
          <td><Badge tone={b.platform === 'Airbnb' ? 'redsoft' : b.platform === 'Booking' ? 'blue' : 'grey'}>{b.platform}</Badge></td>
          <td>{cm(b.calc.gross, b.currency)}</td><td>{cm(b.calc.net, b.currency)}</td>
          <td>{b.paid_out ? <Badge tone="ok">✓ {cm(b.calc.owner, b.currency)}</Badge> : <b>{cm(b.calc.owner, b.currency)}</b>}</td>
          <td className="muted" style={{ whiteSpace: 'nowrap' }}>{PAY_LABEL[b.payment_status]}</td>
        </tr>
      ))}</tbody>
    </table></div>
  );
}

const PLATFORMS: Platform[] = ['Airbnb', 'Booking', 'Другое'];

export function AptForm({ a, onDone }: { a?: DrApartment; onDone?: (x: DrApartment) => void }) {
  const toast = useToast();
  const refresh = useDrInvalidate();
  const [d, setD] = useState<Partial<DrApartment>>(a ?? { name: '', platforms: ['Airbnb', 'Booking'], platform_pct: 20, active: true, status: 'free', util_rates: {} });
  const set = (k: keyof DrApartment, v: unknown) => setD((x) => ({ ...x, [k]: v }));
  const save = useMutation({
    mutationFn: () => {
      const b = { name: d.name, address: d.address || null, owner_name: d.owner_name || null, owner_contact: d.owner_contact || null,
        owner_account: d.owner_account || null, platform_pct: Number(d.platform_pct ?? 20), platforms: d.platforms, status: d.status,
        active: d.active, report_lang: d.report_lang || 'ru', rooms: d.rooms === null || d.rooms === undefined || String(d.rooms) === '' ? null : Number(d.rooms), notes: d.notes || null, util_rates: d.util_rates };
      return a ? dr.updateApt(a.id, b) : dr.createApt(b);
    },
    onSuccess: (x) => { refresh(); toast('Сохранено'); onDone?.(x); },
    onError: (e) => toast(errText(e), true),
  });
  const plats = d.platforms || [];
  const util = d.util_rates || {};
  return (
    <Card title={a ? 'Карточка квартиры' : undefined}>
      <div className="form-grid">
        <Field label="Название *" className="span2"><Input value={d.name || ''} onChange={(e) => set('name', e.target.value)} placeholder="Rustaveli Warm Corner" /></Field>
        <Field label="Адрес" className="span2"><Input value={d.address || ''} onChange={(e) => set('address', e.target.value)} /></Field>
        <Field label="Платформы" className="span2">
          <div className="row gap row-wrap">{PLATFORMS.map((p) => (
            <Checkbox key={p} checked={plats.includes(p)} onChange={(on) => set('platforms', on ? [...plats, p] : plats.filter((x) => x !== p))}>{p}</Checkbox>))}</div>
        </Field>
        <Field label="Статус"><Select value={d.status || 'free'} onChange={(e) => set('status', e.target.value)} options={Object.entries(STATUS_LABEL).map(([value, label]) => ({ value, label }))} /></Field>
        <Field label="Комнат"><Input type="number" min={0} value={d.rooms ?? ''} onChange={(e) => set('rooms', e.target.value)} /></Field>
        <div className="span2"><Switch checked={d.active !== false} onChange={(v) => set('active', v)} label="Активна (видна в списках и форме)" /></div>
        <Field label="Собственник"><Input value={d.owner_name || ''} onChange={(e) => set('owner_name', e.target.value)} /></Field>
        <Field label="Контакт"><Input value={d.owner_contact || ''} onChange={(e) => set('owner_contact', e.target.value)} placeholder="+995 599 00 00 00" /></Field>
        <Field label="Язык отчётов" hint="На нём открывается отчёт и уходит ежемесячный отчёт 1-го числа">
          <Select value={d.report_lang || 'ru'} onChange={(e) => set('report_lang', e.target.value)} options={LANGS} />
        </Field>
        <Field label="Счёт для выплат"><Input value={d.owner_account || ''} onChange={(e) => set('owner_account', e.target.value)} placeholder="GE29NB0000000101904917" style={{ fontFamily: 'monospace' }} /></Field>
        <Field label="Доля платформы, %" hint={`Собственнику ${100 - Number(d.platform_pct ?? 20)}%. Новые проживания берут эту долю; прошлые не меняются.`}>
          <Input type="number" min={0} max={100} step="1" value={d.platform_pct ?? 20} onChange={(e) => set('platform_pct', e.target.value)} />
        </Field>
        <div className="span2 col gap-sm"><span className="label">Коммунальные счета, ₾/мес</span>
          <div className="grid g3">{UTIL.map(([k, l]) => (
            <Field key={k} label={l}><Input type="number" min={0} value={String(util[k] ?? '')} onChange={(e) => set('util_rates', { ...util, [k]: e.target.value })} /></Field>
          ))}
            <Field label="Интернет-провайдер"><Select value={String(util.internet_provider ?? '')} placeholder="—" onChange={(e) => set('util_rates', { ...util, internet_provider: e.target.value })} options={['Magti', 'Silknet'].map((x) => ({ value: x, label: x }))} /></Field>
          </div>
        </div>
        <Field label="Заметки" className="span2"><Textarea rows={3} value={d.notes || ''} onChange={(e) => set('notes', e.target.value)} placeholder="Особенности, правила..." /></Field>
      </div>
      <div className="row gap-sm mt">
        <Button loading={save.isPending} disabled={!String(d.name || '').trim()} onClick={() => save.mutate()}>Сохранить</Button>
      </div>
    </Card>
  );
}

function SiteBlock({ a }: { a: DrApartment }) {
  const confirm = useConfirm();
  const toast = useToast();
  const refresh = useDrInvalidate();
  const [busy, setBusy] = useState<Record<number, number>>({});
  const [lb, setLb] = useState<number | null>(null);
  const [d, setD] = useState({ price_night: a.price_night ?? '', currency: a.currency || 'USD', description: a.description || '', district: a.district || '' });
  const run = (p: Promise<unknown>) => p.then(refresh, (e) => toast(errText(e), true));
  const pick = (files: File[]) => pool(files.slice(0, 20), 3, async (f, i) => {
    try { await dr.upload(a.id, await shrinkPhoto(f), (p) => setBusy((b) => ({ ...b, [i]: Math.round(p * 100) }))); refresh(); } catch (e) { toast(errText(e), true); }
    setBusy((b) => { const { [i]: _, ...rest } = b; return rest; });
  });
  const move = (i: number, dd: number) => { const k = [...a.photos]; [k[i], k[i + dd]] = [k[i + dd], k[i]]; run(dr.orderPhotos(a.id, k)); };
  return (
    <>
      <Card label="Для сайта hge.ge (позже)" title="Цена, район и описание">
        <div className="form-grid">
          <Field label="Цена за ночь"><Input type="number" min={0} step="any" value={d.price_night} onChange={(e) => setD({ ...d, price_night: e.target.value })} /></Field>
          <Field label="Валюта"><Seg value={d.currency as 'USD' | 'GEL'} onChange={(v) => setD({ ...d, currency: v })} options={[{ value: 'USD', label: '$ USD' }, { value: 'GEL', label: '₾ GEL' }]} /></Field>
          <Field label="Район"><DistrictSelect value={d.district} onChange={(v) => setD({ ...d, district: v })} /></Field>
          <Field label="Описание" className="span2"><Textarea rows={6} value={d.description} onChange={(e) => setD({ ...d, description: e.target.value })} /></Field>
        </div>
        <Button className="mt" onClick={() => run(dr.updateApt(a.id, { price_night: d.price_night === '' ? null : Number(d.price_night), currency: d.currency, description: d.description || null, district: d.district || null }).then(() => toast('Сохранено')))}>Сохранить</Button>
      </Card>
      <Card label="Первое фото — обложка" title={`Фото ${a.photos.length}/20`}>
        <div className="col gap">
          <div className="photo-strip">
            {a.photos.map((k, i) => (
              <div key={k} className="ph">
                <img src={thumbUrl(k)} alt="" onClick={() => setLb(i)} style={{ cursor: 'zoom-in' }} />
                <div className="row gap-sm" style={{ position: 'absolute', left: 4, bottom: 4 }}>
                  <Button size="sm" variant="secondary" disabled={i === 0} onClick={() => move(i, -1)} aria-label="Левее"><ArrowLeft size={12} /></Button>
                  <Button size="sm" variant="secondary" disabled={i === a.photos.length - 1} onClick={() => move(i, 1)} aria-label="Правее"><ArrowRight size={12} /></Button>
                </div>
                <Button size="sm" variant="danger" className="del" aria-label="Удалить" onClick={async () => { if (await confirm('Удалить фото?')) run(dr.deletePhoto(a.id, k)); }}><Trash2 size={12} /></Button>
              </div>
            ))}
            {Object.entries(busy).map(([i, p]) => <div key={`b${i}`} className="ph" style={{ display: 'grid', placeItems: 'center' }}><span className="muted">{p}%</span></div>)}
          </div>
          {a.photos.length < 20 && (
            <label className="btn btn-secondary" style={{ alignSelf: 'start' }}>
              <ImagePlus size={16} />Добавить фото
              <input type="file" accept="image/*" multiple hidden onChange={(e) => { const f = [...(e.target.files ?? [])]; e.target.value = ''; pick(f); }} />
            </label>
          )}
        </div>
      </Card>
      {lb !== null && <Lightbox items={a.photos.map((src) => ({ src: mediaUrl(src) }))} index={lb} onIndex={setLb} onClose={() => setLb(null)} />}
    </>
  );
}

const CH_LABEL = { airbnb: 'Airbnb', booking: 'Booking', other: 'Другая площадка' } as const;

function Channels({ a }: { a: DrApartment }) {
  const confirm = useConfirm();
  const toast = useToast();
  const refresh = useDrInvalidate();
  const [urls, setUrls] = useState<Record<number, string>>({});
  const [add, setAdd] = useState<'airbnb' | 'booking' | 'other'>('airbnb');
  const run = (p: Promise<unknown>, ok?: string) => p.then((x) => { refresh(); if (ok) toast(ok); return x; }, (e) => toast(errText(e), true));
  return (
    <Card label="iCal: площадки забирают нашу занятость, мы — их" title="Календари площадок">
      <div className="col gap-lg">
        {a.channels.length === 0 && <Empty text="Календарей нет: добавьте Airbnb или Booking" />}
        {a.channels.map((ch) => (
          <div key={ch.id} className="col gap-sm dr-channel">
            <div className="row row-between row-wrap">
              <b>{CH_LABEL[ch.channel]}</b>
              <span className="row gap-sm">
                {ch.last_status === 'ok' && <Badge tone="ok">✓ {ch.events ?? 0} событий · {ch.last_sync_at ? new Date(ch.last_sync_at).toLocaleString('ru-RU') : ''}</Badge>}
                {ch.last_status === 'error' && <Badge tone="red" title={ch.last_error || ''}>Ошибка: {ch.last_error}</Badge>}
                <Button size="sm" variant="secondary" disabled={!ch.import_url} onClick={() => run(dr.syncChannel(ch.id)).then((r) => { const x = r as { error?: string; events?: number } | undefined; if (x) toast(x.error ? `Ошибка: ${x.error}` : `Загружено событий: ${x.events ?? 0}`, !!x.error); })}><RefreshCw size={14} />Обновить</Button>
                <Button size="sm" variant="ghost" aria-label="Удалить" onClick={async () => { if (await confirm('Удалить календарь? Его брони снимутся.')) run(dr.deleteChannel(ch.id)); }}><Trash2 size={14} /></Button>
              </span>
            </div>
            <Field label={`Наша ссылка для ${CH_LABEL[ch.channel]} (вставить в «Импорт календаря» площадки)`}>
              <div className="row gap-sm"><Input readOnly value={ch.export_url} onFocus={(e) => e.target.select()} /><Button size="sm" variant="secondary" onClick={() => copy(ch.export_url, toast)}><Copy size={14} /></Button></div>
            </Field>
            <Field label={`Ссылка календаря ${CH_LABEL[ch.channel]} («Экспорт календаря» площадки)`}>
              <div className="row gap-sm">
                <Input value={urls[ch.id] ?? ch.import_url ?? ''} placeholder="https://www.airbnb.com/calendar/ical/....ics" onChange={(e) => setUrls({ ...urls, [ch.id]: e.target.value })} />
                <Button size="sm" disabled={urls[ch.id] === undefined} onClick={() => run(dr.updateChannel(ch.id, urls[ch.id]), 'Сохранено')}>Сохранить</Button>
              </div>
            </Field>
          </div>
        ))}
        <div className="row gap-sm row-wrap">
          <Seg value={add} onChange={setAdd} options={Object.entries(CH_LABEL).map(([value, label]) => ({ value: value as typeof add, label }))} />
          <Button variant="secondary" onClick={() => run(dr.createChannel({ apartment_id: a.id, channel: add }), 'Календарь добавлен')}><Plus size={15} />Добавить календарь</Button>
        </div>
      </div>
    </Card>
  );
}

export function NewApartmentPage() {
  const nav = useNavigate();
  return (
    <div className="col gap-lg">
      <div><Link to="/daily-rent/apartments" className="muted" style={{ fontSize: 13 }}>← Квартиры</Link></div>
      <PageHead label="Посуточная аренда" title="Новая квартира" />
      <AptForm onDone={(x) => nav(`/daily-rent/apartments/${x.id}`, { replace: true })} />
    </div>
  );
}
