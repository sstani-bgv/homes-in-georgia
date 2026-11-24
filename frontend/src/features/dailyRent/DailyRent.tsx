// «Посуточная аренда» (ApartFlow inside the CRM, backend api/daily_rent.py): summary and owner payouts, apartments,
// the «Шахматка» calendar, cleanings and staff, the change journal. superadmin / admin / daily_rent_manager.
import { useConfirm } from '../../components/ActionDialog';
import { useMemo, useState } from 'react';
import { Link, Navigate, Route, Routes, useNavigate } from 'react-router-dom';
import { useMutation, useQuery } from '@tanstack/react-query';
import { ChevronDown, ChevronRight, Plus, Trash2 } from 'lucide-react';
import { thumbUrl } from '../../api/client';
import { Badge, Button, Card, Empty, ErrorBox, Field, Input, Loading, PageHead, Seg, Select, Stat, Switch, Textarea } from '../../components/ui';
import { errText, useToast } from '../../components/toast';
import { SubNav } from '../../layout/AppLayout';
import { C as COLORS, Columns, MonthPicker, MONTHS } from '../../pages/StatsCharts';
import { ApartmentPage, NewApartmentPage } from './Apartment';
import { BookingModal, useDrInvalidate } from './Booking';
import { ChessPage } from './Chess';
import { TodayPage } from './Today';
import { cm, dm, dr, HOUR_RATE, mix, periodOf, STATUS_LABEL, usd, ymNow, type DrCleaning, type PerKind, type Summary } from './api';

const NAV = [
  { to: '/daily-rent/today', label: 'Сегодня' },
  { to: '/daily-rent/summary', label: 'Сводка' }, { to: '/daily-rent/apartments', label: 'Квартиры' },
  { to: '/daily-rent/calendar', label: 'Шахматка' }, { to: '/daily-rent/cleaning', label: 'Уборки и персонал' },
  { to: '/daily-rent/journal', label: 'Журнал' },
];

export function DailyRentPage() {
  return (
    <div className="col gap-lg">
      <SubNav items={NAV} />
      <Routes>
        <Route index element={<Navigate to="today" replace />} />
        <Route path="today" element={<TodayPage />} />
        <Route path="summary" element={<SummaryPage />} />
        <Route path="apartments" element={<ApartmentsPage />} />
        <Route path="apartments/new" element={<NewApartmentPage />} />
        <Route path="apartments/:id" element={<ApartmentPage />} />
        <Route path="calendar" element={<ChessPage />} />
        <Route path="cleaning" element={<CleaningPage />} />
        <Route path="journal" element={<JournalPage />} />
        <Route path="*" element={<Navigate to="today" replace />} />
      </Routes>
    </div>
  );
}

export const useDrData = () => useQuery({ queryKey: ['dr', 'data'], queryFn: dr.data, staleTime: 30_000 });

/** ApartFlow's period chips: «Месяц ‹ › / 3 мес / Год / Всё» */
export function usePeriod() {
  const [k, setK] = useState<PerKind>('month');
  const [month, setMonth] = useState(ymNow());
  const { label, ...p } = periodOf(k, month);
  return { k, setK, month, setMonth, label, p };
}

export function PeriodBar({ k, setK, month, setMonth }: ReturnType<typeof usePeriod>) {
  return (
    <div className="row gap row-wrap">
      <Seg value={k} onChange={setK} options={[{ value: 'month', label: 'Месяц' }, { value: '3m', label: '3 мес' }, { value: 'year', label: 'Год' }, { value: 'all', label: 'Всё' }]} />
      {k === 'month' && <MonthPicker value={month} max={ymNow()} onChange={setMonth} />}
    </div>
  );
}

function SummaryPage() {
  const per = usePeriod();
  const q = useQuery({ queryKey: ['dr', 'summary', per.p], queryFn: () => dr.summary(per.p) });
  const nav = useNavigate();
  if (q.isLoading) return <Loading />;
  if (!q.data) return <ErrorBox error={q.error} />;
  const s = q.data, E = s.total;
  return (
    <>
      <PageHead label="Посуточная аренда" title="Сводка" actions={<PeriodBar {...per} />} />
      <div className="grid dr-kpis">
        <Stat blue label="Пришло" value={usd(E.gross_usd)} sub={mix(E.gross)} />
        <Stat label="Чистая прибыль" value={usd(E.net_usd)} sub="после расходов и налогов" />
        <Stat label="Собственникам" value={usd(E.owner_usd)} sub="80% + компенсации" />
        <Stat label="Платформе" value={usd(E.platform_usd)} sub="20% + компенсации" />
        <Stat label="Ночей" value={E.nights} sub={E.occupancy !== null ? `загрузка ≈ ${E.occupancy}%` : `${E.stays} прожив.`} />
        <Stat label="Средняя цена ночи" value={E.nights ? usd(E.adr_usd) : '—'} sub={`расходы: ${usd(E.costs_usd)}`} />
      </div>
      <p className="faint" style={{ fontSize: 12, marginTop: -6 }}>Суммы в долларах по курсу НБГ: 1 $ = {s.rate} ₾ · {per.label}</p>
      <Payouts s={s} />
      {Object.keys(s.months).length > 1 && (
        <Card title="Доход по месяцам, $">
          <Columns rows={Object.entries(s.months).map(([k, v]) => ({ k: MONTHS[Number(k.slice(5)) - 1].slice(0, 3) + ' ' + k.slice(2, 4), v: [v] }))}
            series={[{ label: 'Пришло', color: COLORS.brand }]} fmt={(v) => usd(v)} />
        </Card>
      )}
      <Card title="По квартирам">
        <div className="table-wrap"><table className="table">
          <thead><tr><th>Квартира</th><th>Прожив.</th><th>Ночей</th><th>Загрузка</th><th>Пришло</th><th>Собственнику</th><th>Платформе</th></tr></thead>
          <tbody>{s.apartments.map(({ apartment: a, econ: e }) => (
            <tr key={a.id} className="clickable" onClick={() => nav(`/daily-rent/apartments/${a.id}`)}>
              <td><b>{a.name}</b>{a.owner_name && <small className="muted"> · {a.owner_name}</small>}</td>
              <td>{e.stays}</td><td>{e.nights}</td><td>{e.occupancy !== null ? `${e.occupancy}%` : '—'}</td>
              <td>{usd(e.gross_usd)}</td><td>{usd(e.owner_usd)}</td><td>{usd(e.platform_usd)}</td>
            </tr>
          ))}</tbody>
        </table></div>
      </Card>
    </>
  );
}

/** ApartFlow's «К выплате»: per apartment, the owner's money of stays not paid yet; «Выплатить всё» marks the ready ones. */
function Payouts({ s }: { s: Summary }) {
  const confirm = useConfirm();
  const toast = useToast();
  const refresh = useDrInvalidate();
  const [open, setOpen] = useState<number | null>(null);
  const [stay, setStay] = useState<number | null>(null);
  const pay = useMutation({
    mutationFn: (b: { apartment_id?: number; booking_ids?: number[] }) => dr.pay(b),
    onSuccess: (r) => { refresh(); toast(`Отмечено выплаченными: ${r.changed}`); },
    onError: (e) => toast(errText(e), true),
  });
  return (
    <Card label="Собственникам" title="К выплате">
      {s.payouts.length === 0 ? <Empty text="Всё выплачено ✓" /> : (
        <div className="col gap">
          {s.payouts.map((p) => (
            <div key={p.apartment.id} className="dr-payout">
              <div className="row row-between row-wrap" style={{ cursor: 'pointer' }} onClick={() => setOpen(open === p.apartment.id ? null : p.apartment.id)}>
                <span className="row gap-sm">{open === p.apartment.id ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                  <b>{p.apartment.name}</b><span className="muted">{p.apartment.owner_name}</span></span>
                <span className="row gap-sm">
                  {Object.keys(p.ready).length > 0 && <Badge tone="warn">к выплате {mix(p.ready)}</Badge>}
                  {Object.keys(p.waiting).length > 0 && <Badge tone="grey" title="Деньги от гостя ещё не получены">ожидается {mix(p.waiting)}</Badge>}
                  {p.ready_count > 0 && (
                    <Button size="sm" variant="ok" loading={pay.isPending} onClick={async (e) => {
                      e.stopPropagation();
                      if (await confirm(`Отметить выплаченными ${p.ready_count} прожив. для «${p.apartment.name}»?\n\nСумма собственнику: ${mix(p.ready)}${p.apartment.owner_account ? `\nСчёт: ${p.apartment.owner_account}` : ''}`)) pay.mutate({ apartment_id: p.apartment.id });
                    }}>💸 Выплатить всё</Button>
                  )}
                </span>
              </div>
              {open === p.apartment.id && (
                <div className="col gap-sm mt">
                  {p.apartment.owner_account && <span className="muted">Счёт: <code>{p.apartment.owner_account}</code></span>}
                  {p.bookings.map((b) => {
                    const pend = b.payment_status === 'pending';
                    return (
                      <div key={b.id} className="row row-between row-wrap dr-payout-row">
                        <span style={{ cursor: 'pointer' }} onClick={() => setStay(b.id)}>{dm(b.check_in)} – {dm(b.check_out)} · {b.guest_name || '—'}
                          <small className="muted"> · {pend ? 'деньги ещё не получены' : b.payment_status === 'partial' ? 'получено частично' : 'деньги получены'}</small></span>
                        <span className="row gap-sm"><b>{cm(b.calc.owner, b.currency)}</b>
                          {!pend && <Button size="sm" variant="secondary" onClick={() => pay.mutate({ booking_ids: [b.id] })}>Выплачено</Button>}</span>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
      <BookingModal open={stay} onClose={() => setStay(null)} />
    </Card>
  );
}

function ApartmentsPage() {
  const q = useDrData();
  const nav = useNavigate();
  const [all, setAll] = useState(false);
  if (q.isLoading) return <Loading />;
  if (!q.data) return <ErrorBox error={q.error} />;
  const apts = q.data.apartments.filter((a) => all || a.active);
  const hidden = q.data.apartments.length - q.data.apartments.filter((a) => a.active).length;
  return (
    <>
      <PageHead label="Посуточная аренда" title="Квартиры" actions={<Button onClick={() => nav('/daily-rent/apartments/new')}><Plus size={16} />Новая квартира</Button>} />
      {hidden > 0 && <Switch checked={all} onChange={setAll} label={`Показать неактивные (${hidden})`} />}
      {apts.length === 0 ? <Empty text="Квартир пока нет" /> : (
        <div className="dr-apts">
          {apts.map((a) => (
            <Link key={a.id} to={`/daily-rent/apartments/${a.id}`} className="card dr-apt">
              <div className="dr-apt-cover">{a.photos[0] ? <img src={thumbUrl(a.photos[0])} alt="" /> : <span>🏠</span>}</div>
              <div className="col gap-sm" style={{ padding: 14 }}>
                <div className="row row-between"><b>{a.name}</b><Badge tone={!a.active ? 'grey' : a.status === 'free' ? 'ok' : 'warn'}>{a.active ? STATUS_LABEL[a.status] : 'Неактивна'}</Badge></div>
                <span className="muted" style={{ fontSize: 13 }}>{a.address || '—'}</span>
                <span style={{ fontSize: 13 }}>👤 {a.owner_name || '—'} · {a.platforms.join(', ')}{a.price_night ? ` · ${cm(a.price_night, a.currency)}/ночь` : ''}</span>
              </div>
            </Link>
          ))}
        </div>
      )}
    </>
  );
}

function CleaningPage() {
  const confirm = useConfirm();
  const data = useDrData();
  const q = useQuery({ queryKey: ['dr', 'cleanings'], queryFn: dr.cleanings });
  const toast = useToast();
  const refresh = useDrInvalidate();
  const [c, setC] = useState<Partial<DrCleaning>>({ date: new Date().toISOString().slice(0, 10), hourly_rate: HOUR_RATE });
  const [st, setSt] = useState({ name: '', role: '', contact: '' });
  const apts = data.data?.apartments ?? [];
  const names = useMemo(() => new Map(apts.map((a) => [a.id, a.name])), [apts]);
  // ApartFlow: start/finish → hours × rate
  const mins = c.started_at && c.finished_at ? (+c.finished_at.slice(0, 2) * 60 + +c.finished_at.slice(3)) - (+c.started_at.slice(0, 2) * 60 + +c.started_at.slice(3)) : 0;
  const hours = mins > 0 ? Math.round(mins / 60 * 100) / 100 : null;
  const amount = c.amount ?? (hours ? Math.round(hours * (Number(c.hourly_rate) || HOUR_RATE) * 100) / 100 : null);
  const run = (p: Promise<unknown>, ok: string) => p.then(() => { refresh(); toast(ok); }, (e) => toast(errText(e), true));
  const staff = data.data?.staff ?? [];
  return (
    <>
      <PageHead label="Посуточная аренда" title="Уборки и персонал" />
      <div className="split">
        <Card title="Уборки">
          {q.isLoading ? <Loading /> : (q.data?.items.length ?? 0) === 0 ? <Empty text="Уборок пока нет" /> : (
            <div className="table-wrap"><table className="table">
              <thead><tr><th>Дата</th><th>Квартира</th><th>Уборщик</th><th>Часы</th><th>Сумма</th><th /></tr></thead>
              <tbody>{q.data!.items.map((x) => (
                <tr key={x.id}><td>{dm(x.date)}</td><td>{names.get(x.apartment_id)}</td><td>{x.cleaner}</td>
                  <td>{x.hours ?? '—'}{x.started_at ? <small className="muted"> {x.started_at}–{x.finished_at}</small> : null}</td><td>{cm(x.amount, 'GEL')}</td>
                  <td><Button size="sm" variant="ghost" aria-label="Удалить" onClick={async () => { if (await confirm('Удалить уборку?')) run(dr.deleteCleaning(x.id), 'Удалено'); }}><Trash2 size={14} /></Button></td></tr>
              ))}</tbody>
            </table></div>
          )}
        </Card>
        <div className="col gap-lg">
          <Card title="Новая уборка">
            <div className="form-grid">
              <Field label="Квартира" className="span2"><Select value={String(c.apartment_id ?? '')} placeholder="— Квартира —" onChange={(e) => setC({ ...c, apartment_id: Number(e.target.value) || undefined })}
                options={apts.filter((a) => a.active).map((a) => ({ value: a.id, label: a.name }))} /></Field>
              <Field label="Уборщик" className="span2"><Input list="dr-staff" value={c.cleaner || ''} onChange={(e) => setC({ ...c, cleaner: e.target.value })} placeholder="Выбрать или ввести" />
                <datalist id="dr-staff">{staff.filter((s) => s.active).map((s) => <option key={s.id} value={s.name.trim()} />)}</datalist></Field>
              <Field label="Дата" className="span2"><Input type="date" value={c.date || ''} onChange={(e) => setC({ ...c, date: e.target.value })} /></Field>
              <Field label="Начало"><Input type="time" value={c.started_at || ''} onChange={(e) => setC({ ...c, started_at: e.target.value, amount: undefined })} /></Field>
              <Field label="Конец"><Input type="time" value={c.finished_at || ''} onChange={(e) => setC({ ...c, finished_at: e.target.value, amount: undefined })} /></Field>
              <Field label="Ставка ₾/ч"><Input type="number" step="any" value={c.hourly_rate ?? ''} onChange={(e) => setC({ ...c, hourly_rate: Number(e.target.value), amount: undefined })} /></Field>
              <Field label="Итого ₾" hint={hours ? `${hours} ч` : undefined}><Input type="number" step="any" value={amount ?? ''} onChange={(e) => setC({ ...c, amount: e.target.value === '' ? undefined : Number(e.target.value) })} /></Field>
              <Field label="Заметки" className="span2"><Textarea rows={2} value={c.notes || ''} onChange={(e) => setC({ ...c, notes: e.target.value })} /></Field>
            </div>
            <Button className="mt" disabled={!c.apartment_id} onClick={() => run(dr.createCleaning({ ...c, hours, amount }), 'Уборка добавлена').then(() => setC({ date: c.date, hourly_rate: c.hourly_rate }))}>Сохранить</Button>
          </Card>
          <Card title="Персонал">
            <div className="col gap-sm">
              {staff.map((s) => (
                <div key={s.id} className="row row-between">
                  <span><b>{s.name}</b>{s.role && <small className="muted"> · {s.role}</small>}{s.contact && <small className="muted"> · {s.contact}</small>}</span>
                  <Switch checked={s.active} onChange={(v) => run(dr.updateStaff(s.id, { active: v }), v ? 'Активен' : 'Неактивен')} />
                </div>
              ))}
              <div className="form-grid mt">
                <Field label="Имя"><Input value={st.name} onChange={(e) => setSt({ ...st, name: e.target.value })} /></Field>
                <Field label="Роль"><Input value={st.role} onChange={(e) => setSt({ ...st, role: e.target.value })} placeholder="Cleaning" /></Field>
                <Field label="Контакт" className="span2"><Input value={st.contact} onChange={(e) => setSt({ ...st, contact: e.target.value })} /></Field>
              </div>
              <Button variant="secondary" disabled={!st.name.trim()} style={{ alignSelf: 'start' }} onClick={() => run(dr.createStaff(st), 'Добавлено').then(() => setSt({ name: '', role: '', contact: '' }))}><Plus size={15} />Добавить</Button>
            </div>
          </Card>
        </div>
      </div>
    </>
  );
}

const ENTITY: Record<string, string> = { dr_apartment: 'Квартира', dr_booking: 'Проживание', dr_expense: 'Расход', dr_cleaning: 'Уборка', dr_staff: 'Сотрудник', dr_block: 'Блокировка', dr_channel: 'Календарь' };
const ACTION: Record<string, string> = { create: 'создано', update: 'изменено', delete: 'удалено', restore: 'восстановлено', fill_blanks: 'дополнено', cancel: 'снято',
  photos_added: 'фото добавлены', photo_deleted: 'фото удалено', photos_reordered: 'порядок фото' };

function JournalPage() {
  const [offset, setOffset] = useState(0);
  const q = useQuery({ queryKey: ['dr', 'journal', offset], queryFn: () => dr.journal(offset) });
  const what = (d: Record<string, unknown>) => {
    const ch = d.changes as Record<string, [unknown, unknown]> | undefined;
    if (ch) return Object.entries(ch).map(([k, [a, b]]) => `${k}: ${a ?? '—'} → ${b ?? '—'}`).join('; ');
    const x = (d.after || d.before) as Record<string, unknown> | undefined;
    return x ? String(x.guest_name || x.name || x.apartment_name || x.category || '') : '';
  };
  return (
    <>
      <PageHead label="Посуточная аренда" title="Журнал изменений" />
      <Card>
        {q.isLoading ? <Loading /> : !q.data ? <ErrorBox error={q.error} /> : q.data.total === 0 ? <Empty text="Изменений пока нет" /> : (
          <>
            <div className="table-wrap"><table className="table">
              <thead><tr><th>Когда</th><th>Кто</th><th>Что</th><th>Изменения</th></tr></thead>
              <tbody>{q.data.items.map((e) => (
                <tr key={e.id}>
                  <td style={{ whiteSpace: 'nowrap' }}>{new Date(e.at).toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit' })}</td>
                  <td>{e.actor}</td><td>{ENTITY[e.entity] || e.entity} #{e.entity_id} {ACTION[e.action] || e.action}</td>
                  <td className="muted" style={{ fontSize: 12, maxWidth: 520 }}>{what(e.data)}</td>
                </tr>
              ))}</tbody>
            </table></div>
            <div className="row gap-sm mt">
              <Button size="sm" variant="secondary" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 100))}>Новее</Button>
              <span className="muted">{offset + 1}–{Math.min(offset + 100, q.data.total)} из {q.data.total}</span>
              <Button size="sm" variant="secondary" disabled={offset + 100 >= q.data.total} onClick={() => setOffset(offset + 100)}>Старее</Button>
            </div>
          </>
        )}
      </Card>
    </>
  );
}
