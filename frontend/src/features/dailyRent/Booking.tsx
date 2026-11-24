// «Посуточная аренда»: one stay — the form, its expenses, the money split and the report for the owner (ApartFlow's
// booking screen: «Основное / Расходы / Отчёт»).
import { useConfirm } from '../../components/ActionDialog';
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Copy, Plus, Trash2 } from 'lucide-react';
import { Badge, Button, Field, Input, Loading, Modal, Seg, Select, Textarea } from '../../components/ui';
import { errText, useToast } from '../../components/toast';
import { cm, dr, EXP_LABEL, LANGS, nightsLabel, PAY_LABEL, type Calc, type DrApartment, type DrBooking, type DrExpense, type Lang, type Platform, type Reports } from './api';

type Draft = Partial<DrBooking>;
const PLATFORMS: Platform[] = ['Airbnb', 'Booking', 'Другое'];

export function useDrInvalidate() {
  const qc = useQueryClient();
  return () => qc.invalidateQueries({ queryKey: ['dr'] });
}

export function copy(text: string, toast: (t: string, err?: boolean) => void) {
  navigator.clipboard.writeText(text).then(() => toast('Скопировано'), () => toast('Не удалось скопировать', true));
}

/** the owner's report in Russian / English / Georgian: opens in the owner's language (the apartment card → «Язык отчётов»),
 * the manager switches and copies the one she needs */
export function ReportView({ reports, lang, toast }: { reports: Reports; lang?: Lang; toast: (t: string, err?: boolean) => void }) {
  const [l, setL] = useState<Lang>(lang || 'ru');
  return (
    <div className="col gap">
      <div className="row gap-sm row-wrap row-between">
        <Seg value={l} onChange={setL} options={LANGS} />
        <Button size="sm" variant="secondary" onClick={() => copy(reports[l], toast)}><Copy size={14} />Копировать</Button>
      </div>
      <pre className="dr-report" lang={l}>{reports[l]}</pre>
    </div>
  );
}

/** open: a stay id, or 'new' with the apartment (and optional dates from the calendar) */
export function BookingModal({ open, apt, prefill, onClose }: {
  open: number | 'new' | null; apt?: DrApartment; prefill?: Draft; onClose: () => void;
}) {
  const isNew = open === 'new';
  const q = useQuery({ queryKey: ['dr', 'booking', open], queryFn: () => dr.booking(open as number), enabled: typeof open === 'number' });
  const defaults = useQuery({ queryKey: ['dr', 'defaults', apt?.id, prefill?.platform], enabled: isNew && !!apt,
    queryFn: () => dr.defaults(apt!.id, prefill?.platform) });
  if (open === null) return null;
  const b = isNew ? undefined : q.data;
  const ready = isNew ? defaults.data : b;
  return (
    <Modal open onClose={onClose} wide title={isNew ? 'Новое проживание' : b ? `${b.guest_name || 'Проживание'} · ${b.platform}` : 'Проживание'}>
      {!ready ? <Loading /> : <BookingBody key={b?.id ?? 'new'} b={b} start={{ ...(isNew ? defaults.data : {}), ...(isNew ? prefill : {}), apartment_id: apt?.id ?? b?.apartment_id }} onClose={onClose} />}
    </Modal>
  );
}

function BookingBody({ b, start, onClose }: { b?: DrBooking; start: Draft; onClose: () => void }) {
  const confirm = useConfirm();
  const toast = useToast();
  const refresh = useDrInvalidate();
  const qc = useQueryClient();
  const [d, setD] = useState<Draft>(b ?? start);
  const [tab, setTab] = useState<'main' | 'exp' | 'rpt'>('main');
  const [cur, setCur] = useState<DrBooking | undefined>(b);
  const set = (k: keyof DrBooking, v: unknown) => setD((x) => ({ ...x, [k]: v }));
  const fresh = (x: DrBooking) => { setCur(x); qc.setQueryData(['dr', 'booking', x.id], x); refresh(); };
  const save = useMutation({
    mutationFn: () => {
      const body: Draft = {
        apartment_id: d.apartment_id, platform: d.platform, guest_name: d.guest_name || null, check_in: d.check_in || null,
        check_out: d.check_out || null, gross: d.gross === undefined || d.gross === null || String(d.gross) === '' ? null : Number(d.gross),
        currency: d.currency, tax_pct: Number(d.tax_pct) || 0, payment_status: d.payment_status, assigned_staff: d.assigned_staff || null,
        comment: d.comment || null, booking_fee: d.platform === 'Booking' && d.booking_fee ? Number(d.booking_fee) : null,
      };
      return cur ? dr.updateBooking(cur.id, body) : dr.createBooking(body);
    },
    onSuccess: (x) => { fresh(x); setD(x); toast('Сохранено'); },
    onError: (e) => toast(errText(e), true),
  });
  const run = (p: Promise<unknown>, ok?: string) => p.then((x) => { if (x && typeof x === 'object' && 'calc' in x) fresh(x as DrBooking); else refresh(); if (ok) toast(ok); }, (e) => toast(errText(e), true));
  const nights = d.check_in && d.check_out ? Math.max(0, Math.round((+new Date(d.check_out) - +new Date(d.check_in)) / 86400000)) : 0;
  const C = d.currency === 'GEL' ? '₾' : '$';
  return (
    <div className="col gap-lg">
      <Seg value={tab} onChange={setTab} options={[{ value: 'main', label: 'Основное' }, { value: 'exp', label: `Расходы${cur ? ` (${cur.expenses.length})` : ''}` }, { value: 'rpt', label: 'Отчёт' }]} />
      {tab === 'main' && (
        <>
          <div className="form-grid">
            <Field label="Платформа" className="span2">
              <Seg value={(d.platform || 'Airbnb') as Platform} onChange={(p) => setD((x) => ({ ...x, platform: p, currency: p === 'Airbnb' ? 'USD' : 'GEL', tax_pct: p === 'Airbnb' ? 1 : 0 }))}
                options={PLATFORMS.map((p) => ({ value: p, label: p }))} />
            </Field>
            <Field label="Заезд"><Input type="date" value={d.check_in || ''} onChange={(e) => set('check_in', e.target.value)} /></Field>
            <Field label="Выезд" hint={nights ? nightsLabel(nights) : undefined}><Input type="date" value={d.check_out || ''} onChange={(e) => set('check_out', e.target.value)} /></Field>
            <Field label="Гость"><Input value={d.guest_name || ''} onChange={(e) => set('guest_name', e.target.value)} /></Field>
            <Field label="Уборщик / сотрудник"><Input value={d.assigned_staff || ''} onChange={(e) => set('assigned_staff', e.target.value)} /></Field>
            <Field label={`Пришло, ${C}`}><Input type="number" step="any" min={0} value={d.gross ?? ''} onChange={(e) => set('gross', e.target.value)} /></Field>
            <Field label="Валюта"><Seg value={(d.currency || 'USD') as 'USD' | 'GEL'} onChange={(v) => set('currency', v)} options={[{ value: 'USD', label: '$ USD' }, { value: 'GEL', label: '₾ GEL' }]} /></Field>
            <Field label="Налог, %" hint="Airbnb: 1%"><Input type="number" step="0.1" min={0} max={100} value={d.tax_pct ?? 0} onChange={(e) => set('tax_pct', e.target.value)} /></Field>
            {d.platform === 'Booking' && <Field label={`Booking fee, ${C}`} hint="Идёт в долю платформы"><Input type="number" step="any" min={0} value={d.booking_fee ?? ''} onChange={(e) => set('booking_fee', e.target.value)} /></Field>}
            <Field label="Статус оплаты"><Select value={d.payment_status || 'received'} onChange={(e) => set('payment_status', e.target.value)} options={Object.entries(PAY_LABEL).map(([value, label]) => ({ value, label }))} /></Field>
            <Field label="Комментарий" className="span2"><Textarea rows={2} value={d.comment || ''} onChange={(e) => set('comment', e.target.value)} /></Field>
          </div>
          {cur && <CalcBox c={cur.calc} ownerName="Собственник" />}
          <div className="row gap-sm row-wrap">
            <Button loading={save.isPending} onClick={() => save.mutate()}>{cur ? 'Сохранить' : 'Создать'}</Button>
            {cur && (cur.paid_out
              ? <><Badge tone="ok">✓ Выплачено {cur.paid_out_at ? new Date(cur.paid_out_at).toLocaleDateString('ru-RU') : ''}</Badge>
                  <Button variant="ghost" size="sm" onClick={() => run(dr.pay({ booking_ids: [cur.id], paid: false }).then(() => dr.booking(cur.id)), 'Выплата отменена')}>Отменить</Button></>
              : <Button variant="ok" disabled={cur.calc.owner <= 0} onClick={() => run(dr.pay({ booking_ids: [cur.id] }).then(() => dr.booking(cur.id)), 'Отмечено: выплачено')}>💸 Выплачено владельцу</Button>)}
            {cur && <Button variant="ghost" onClick={async () => { if (await confirm('Удалить проживание вместе с его расходами?')) dr.deleteBooking(cur.id).then(() => { refresh(); toast('Удалено'); onClose(); }, (e) => toast(errText(e), true)); }}><Trash2 size={15} />Удалить</Button>}
          </div>
        </>
      )}
      {tab === 'exp' && (cur ? <Expenses b={cur} run={run} /> : <p className="muted">Сначала сохраните проживание.</p>)}
      {tab === 'rpt' && (cur?.reports ? <ReportView reports={cur.reports} lang={cur.report_lang} toast={toast} />
        : <p className="muted">Сначала сохраните проживание.</p>)}
    </div>
  );
}

function Expenses({ b, run }: { b: DrBooking; run: (p: Promise<unknown>, ok?: string) => void }) {
  const confirm = useConfirm();
  const [e, setE] = useState<Partial<DrExpense>>({ category: 'cleaning', currency: 'GEL', payer: 'platform' });
  const ok = Number(e.amount) > 0;
  return (
    <div className="col gap">
      {b.expenses.length === 0 ? <p className="muted">Расходов нет</p> : (
        <div className="table-wrap"><table className="table">
          <thead><tr><th>Расход</th><th>Сумма</th><th>Кто оплатил</th><th>Комментарий</th><th /></tr></thead>
          <tbody>{b.expenses.map((x) => (
            <tr key={x.id}>
              <td>{EXP_LABEL[x.category] || x.category}</td><td>{cm(x.amount, x.currency)}</td>
              <td><Select value={x.payer} onChange={(ev) => run(dr.updateExpense(x.id, { payer: ev.target.value as DrExpense['payer'] }))} options={[{ value: 'platform', label: 'Платформа' }, { value: 'owner', label: 'Собственник' }]} /></td>
              <td className="muted">{x.comment}</td>
              <td><Button size="sm" variant="ghost" aria-label="Удалить" onClick={async () => { if (await confirm('Удалить расход?')) run(dr.deleteExpense(x.id)); }}><Trash2 size={14} /></Button></td>
            </tr>
          ))}</tbody>
        </table></div>
      )}
      <div className="form-grid">
        <Field label="Категория"><Select value={e.category} onChange={(ev) => setE({ ...e, category: ev.target.value })} options={Object.entries(EXP_LABEL).map(([value, label]) => ({ value, label }))} /></Field>
        <Field label="Сумма"><Input type="number" step="any" min={0} value={e.amount ?? ''} onChange={(ev) => setE({ ...e, amount: ev.target.value as unknown as number })} /></Field>
        <Field label="Валюта"><Seg value={(e.currency || 'GEL') as 'USD' | 'GEL'} onChange={(v) => setE({ ...e, currency: v })} options={[{ value: 'GEL', label: '₾' }, { value: 'USD', label: '$' }]} /></Field>
        <Field label="Кто оплатил"><Seg value={e.payer || 'platform'} onChange={(v) => setE({ ...e, payer: v })} options={[{ value: 'platform', label: 'Платформа' }, { value: 'owner', label: 'Собственник' }]} /></Field>
        <Field label="Комментарий" className="span2"><Input value={e.comment || ''} onChange={(ev) => setE({ ...e, comment: ev.target.value })} /></Field>
      </div>
      <Button variant="secondary" disabled={!ok} style={{ alignSelf: 'start' }}
        onClick={() => { run(dr.addExpense({ ...e, booking_id: b.id, amount: Number(e.amount) }), 'Расход добавлен'); setE({ ...e, amount: undefined, comment: '' }); }}>
        <Plus size={15} />Добавить расход
      </Button>
      <CalcBox c={b.calc} ownerName="Собственник" />
    </div>
  );
}

/** ApartFlow's calcBox: where the money of the stay goes. */
export function CalcBox({ c, ownerName }: { c: Calc; ownerName: string }) {
  const S = (v: number) => cm(v, c.currency);
  const row = (l: string, v: string, tone?: string) => <div className="row row-between"><span className="muted">{l}</span><b style={tone ? { color: tone } : undefined}>{v}</b></div>;
  return (
    <div className="dr-calc">
      {row('Пришло', S(c.gross))}
      {c.tax > 0 && row(`Налог ${c.tax_pct}%`, '-' + S(c.tax), 'var(--accent)')}
      {c.booking_fee > 0 && row('Booking fee', '-' + S(c.booking_fee), 'var(--accent)')}
      {c.total_expenses > 0 && row('Расходы', '-' + S(c.total_expenses), 'var(--accent)')}
      {row('Чистая прибыль', S(c.net), c.net >= 0 ? 'var(--ok)' : 'var(--accent)')}
      <div className="dr-calc-split">
        <div><span className="label">Платформа {c.platform_pct}%</span><b>{S(c.platform)}</b>
          <small className="muted">{S(c.platform_base)}{c.exp_platform > 0 ? ` + ${S(c.exp_platform)} расходы` : ''}{c.booking_fee > 0 ? ` + ${S(c.booking_fee)} fee` : ''}</small></div>
        <div><span className="label">{ownerName} {c.owner_pct}%</span><b style={{ color: 'var(--ok)' }}>{S(c.owner)}</b>
          <small className="muted">{S(c.owner_base)}{c.exp_owner > 0 ? ` + ${S(c.exp_owner)} расходы` : ''}</small></div>
      </div>
    </div>
  );
}
