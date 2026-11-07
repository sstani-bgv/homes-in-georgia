// /contracts and /m/contracts — contract generator (port of the old CRM tab «tab-contract»).
// Output is a printable A4 document (window.print + print CSS) and a .doc file for Word; no external services.
import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Building2, Download, FileText, Home, KeyRound, Printer, RotateCcw } from 'lucide-react';
import { api } from '../api';
import { itemsOf } from '../api/client';
import type { Deal, LeadContract } from '../api/types';
import { errText, useToast } from '../components/toast';
import { Badge, Button, Card, Field, Input, PageHead, Select, Switch, Tabs } from '../components/ui';
import { CONTRACT_STAGES, LEAD_STATUS, todayStr } from '../lib/format';
import {
  CX_LANGS, CX_TYPES, DATE_FIELDS, EMPTY, addMonthsIso, buildContract, cxFmt, cxOnem, monthsBetween, requiredFields, reviewLabels,
  type ContractDoc, type CxData, type CxLang, type CxType,
} from '../features/contracts/templates';

const STEPS = ['Тип', 'Язык', 'Данные', 'Проверка', 'Документ'];
const TYPE_ICON: Record<CxType, typeof Home> = { rent: Home, comm: Building2, sale: KeyRound };
const DRAFT_KEY = 'hig-contract-draft';

interface Draft { type: CxType | ''; lang: CxLang | ''; data: CxData; depTouched: boolean }

function loadDraft(): Draft | null {
  try { const s = localStorage.getItem(DRAFT_KEY); return s ? JSON.parse(s) as Draft : null; } catch { return null; }
}
function saveDraft(d: Draft) { try { localStorage.setItem(DRAFT_KEY, JSON.stringify(d)); } catch { /* storage unavailable */ } }
function clearDraft() { try { localStorage.removeItem(DRAFT_KEY); } catch { /* storage unavailable */ } }

/** Deal (GET /api/deals/{id}) → contract fields. */
function fromDeal(deal: Deal, prop?: { area?: number | null; floor?: number | null; cadastral_code?: string | null; address?: string | null } | null): { type: CxType; data: Partial<CxData> } {
  const type: CxType = deal.deal_type === 'sale' ? 'sale' : deal.sub_type === 'commercial' ? 'comm' : 'rent';
  const price = deal.price ? String(deal.price) : '';
  const from = deal.rent_from?.slice(0, 10) || '';
  const to = deal.rent_to?.slice(0, 10) || '';
  const data: Partial<CxData> = {
    owner_full_name: deal.owner_name || '', client_full_name: deal.client_name || '',
    cadastral_code: deal.cadastral || prop?.cadastral_code || '', street_house: deal.address || prop?.address || '',
    apartment_area: prop?.area ? String(prop.area) : '', floor: prop?.floor ? String(prop.floor) : '',
  };
  if (type === 'sale') Object.assign(data, { sale_price: price });
  else {
    Object.assign(data, { price_usd: price, deposit_usd: price, start_rent: from, end_rent: to, rent_duration_months: monthsBetween(from, to) });
    if (from) data.pay_day = String(Number(from.slice(8, 10)));
  }
  return { type, data };
}

// ---------------------------------------------------------------- document

export function ContractDocument({ doc }: { doc: ContractDoc }) {
  return (
    <article className="contract-doc" lang={doc.lang === 'GEO' ? 'ka' : doc.lang === 'RU' ? 'ru' : 'en'}>
      <h1 className="cd-title">{doc.title}</h1>
      <div className="cd-meta"><span>{doc.placeLabel}: {doc.place}</span><span>{doc.dateLabel}: {doc.date}</span></div>
      <p className="cd-p">{doc.preamble}</p>
      {doc.sections.map((s, i) => (
        <section key={i} className="cd-section">
          <h2 className="cd-h">{i + 1}. {s.h}</h2>
          {s.items.map((t, j) => <p key={j} className="cd-p"><span className="cd-n">{i + 1}.{j + 1}.</span> {t}</p>)}
        </section>
      ))}
      <section className="cd-sign">
        <h2 className="cd-h">{doc.signTitle}</h2>
        <div className="cd-sign-grid">
          {doc.parties.map((p) => (
            <div key={p.role} className="cd-party">
              <div className="cd-role">{p.role}</div>
              <div className="cd-name">{p.name}</div>
              <div>{p.idLabel}: {p.id}</div>
              {p.dob && <div>{p.dobLabel}: {p.dob}</div>}
              <div className="cd-line" />
              <div className="cd-line-label">{doc.signLabel}</div>
            </div>
          ))}
        </div>
      </section>
    </article>
  );
}

const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
/** Standalone HTML (opens in Word / LibreOffice as an editable document, like the old «Редактировать DOC»). */
function docHtml(doc: ContractDoc): string {
  const body = [
    `<h1 style="text-align:center;font-size:15pt">${esc(doc.title)}</h1>`,
    `<p style="display:flex;justify-content:space-between"><span>${esc(doc.placeLabel)}: ${esc(doc.place)}</span> &nbsp; <span>${esc(doc.dateLabel)}: ${esc(doc.date)}</span></p>`,
    `<p>${esc(doc.preamble)}</p>`,
    ...doc.sections.map((s, i) => `<h2 style="font-size:12pt">${i + 1}. ${esc(s.h)}</h2>${s.items.map((t, j) => `<p>${i + 1}.${j + 1}. ${esc(t)}</p>`).join('')}`),
    `<h2 style="font-size:12pt">${esc(doc.signTitle)}</h2><table style="width:100%"><tr>${doc.parties.map((p) => `<td style="width:50%;vertical-align:top"><b>${esc(p.role)}</b><br>${esc(p.name)}<br>${esc(p.idLabel)}: ${esc(p.id)}${p.dob ? `<br>${esc(p.dobLabel || '')}: ${esc(p.dob)}` : ''}<br><br>${esc(doc.signLabel)}: ____________________</td>`).join('')}</tr></table>`,
  ].join('\n');
  return `<!doctype html><html><head><meta charset="utf-8"><title>${esc(doc.title)}</title><style>body{font-family:'Noto Sans Georgian','Montserrat',Arial,sans-serif;font-size:11pt;line-height:1.5}p{margin:0 0 6pt;text-align:justify}</style></head><body>${body}</body></html>`;
}

// ---------------------------------------------------------------- wizard

export function ContractWizard({ compact }: { compact?: boolean }) {
  const toast = useToast();
  const [params, setParams] = useSearchParams();
  const dealId = Number(params.get('deal_id')) || 0;
  const leadId = Number(params.get('lead_id')) || 0;
  const draft = useMemo(() => (dealId || leadId ? null : loadDraft()), [dealId, leadId]);

  const [step, setStep] = useState(1);
  const [type, setType] = useState<CxType | ''>(draft?.type || '');
  const [lang, setLang] = useState<CxLang | ''>(draft?.lang || '');
  const [d, setD] = useState<CxData>(draft?.data || { ...EMPTY, timestamp: todayStr() });
  const [depTouched, setDepTouched] = useState(!!draft?.depTouched);
  const [tab, setTab] = useState<'parties' | 'property' | 'money'>('parties');
  const [err, setErr] = useState('');
  const [missing, setMissing] = useState<Set<keyof CxData>>(new Set());
  const [prefilled, setPrefilled] = useState<{ key: string; text: string } | null>(null);
  const [petTouched, setPetTouched] = useState(false);

  useEffect(() => { saveDraft({ type, lang, data: d, depTouched }); }, [type, lang, d, depTouched]);

  // ---- prefill from /contracts?deal_id=
  const deal = useQuery({ queryKey: ['deal', dealId], queryFn: () => api.deals.get(dealId), enabled: !!dealId });
  const prop = useQuery({ queryKey: ['property', deal.data?.property_id], queryFn: () => api.properties.get(deal.data!.property_id!), enabled: !!deal.data?.property_id, retry: false });
  useEffect(() => {
    if (!deal.data || prefilled?.key === `deal:${deal.data.id}` || (deal.data.property_id && prop.isLoading)) return;
    applyDeal(deal.data, prop.data);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deal.data, prop.data, prop.isLoading]);

  function apply(type: CxType, data: Partial<CxData>, key: string, text: string) {
    setType(type);
    const filled = Object.fromEntries(Object.entries(data).filter(([, v]) => v !== '')) as Partial<CxData>;
    if (filled.start_rent && filled.end_rent && !filled.rent_duration_months) filled.rent_duration_months = monthsBetween(filled.start_rent, filled.end_rent);
    setD((cur) => ({ ...EMPTY, timestamp: cur.timestamp || todayStr(), ...filled }));
    setDepTouched(false);
    setPrefilled({ key, text });
    setStep((s) => (s < 2 ? 2 : s));
    toast(`Данные подставлены из ${key.startsWith('lead') ? 'заявки' : 'сделки'} #${key.split(':')[1]}`);
  }
  function applyDeal(x: Deal, p?: Parameters<typeof fromDeal>[1]) {
    const r = fromDeal(x, p);
    apply(r.type, r.data, `deal:${x.id}`, `Данные подставлены из сделки #${x.id}${x.address ? ` · ${x.address}` : ''}.`);
  }

  // ---- prefill from /contracts?lead_id= (lead card «Оформить договор», or the picker below)
  const leadC = useQuery({ queryKey: ['lead-contract', leadId], queryFn: () => api.leads.contract(leadId), enabled: !!leadId });
  useEffect(() => {
    const x = leadC.data;
    if (!x || prefilled?.key === `lead:${x.lead_id}`) return;
    applyLead(x);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [leadC.data]);
  function applyLead(x: LeadContract) {
    const who = (label: string, c: { name?: string | null; phone?: string | null; username?: string | null }) => {
      const parts = [c.name, c.phone, c.username ? `@${c.username}` : null].filter(Boolean);
      return parts.length ? ` ${label}: ${parts.join(' · ')}.` : '';
    };
    apply(x.type, x.data as Partial<CxData>, `lead:${x.lead_id}`,
      `Данные подставлены из заявки #${x.lead_id}${x.data.street_house ? ` · ${x.data.street_house}` : ''}${x.district ? `, ${x.district}` : ''}.`
      + who('Клиент', x.client) + who('Собственник', x.owner));
  }
  const pickable = useQuery({ queryKey: ['leads', 'list', 'contract'], queryFn: () => api.leads.list({ status: CONTRACT_STAGES.join(',') }) });
  function pickLead(id: number) {
    setParams((sp) => { sp.delete('deal_id'); sp.set('lead_id', String(id)); return sp; }, { replace: true });
  }

  // ---- field updates with the old auto-sync rules
  const set = (k: keyof CxData, v: string | boolean) => {
    setMissing((m) => { if (!m.has(k)) return m; const n = new Set(m); n.delete(k); return n; });
    setD((x) => {
      const n: CxData = { ...x, [k]: v };
      if (k === 'price_usd' && !depTouched) n.deposit_usd = String(v);
      if ((k === 'price_usd' || k === 'deposit_usd') && n.with_pets && !petTouched) {
        const dep = parseFloat(n.deposit_usd) || 0;
        if (dep > 0) n.pet_deposit = String(Math.round(dep * 0.5));
      }
      if (k === 'with_pets' && v && !n.pet_deposit) {
        const dep = parseFloat(n.deposit_usd) || 0;
        if (dep > 0) n.pet_deposit = String(Math.round(dep * 0.5));
      }
      if (k === 'start_rent' || k === 'end_rent') {
        const m = monthsBetween(n.start_rent, n.end_rent);
        if (m) n.rent_duration_months = m;
      }
      if (k === 'rent_duration_months' && n.start_rent && Number(v) > 0) n.end_rent = addMonthsIso(n.start_rent, Number(v));
      return n;
    });
    if (k === 'deposit_usd') setDepTouched(true);
  };
  const sale = type === 'sale';
  const req = useMemo(() => new Set(type ? requiredFields(type) : []), [type]);
  const doc = useMemo(() => (type && lang ? buildContract(type, lang, d) : null), [type, lang, d]);

  function next() {
    setErr('');
    if (step === 1) { if (!type) { setErr('Выберите тип договора'); return; } setStep(2); return; }
    if (step === 2) { if (!lang) { setErr('Выберите язык договора'); return; } setStep(3); return; }
    if (step === 3) {
      const miss = requiredFields(type as CxType).filter((k) => !String(d[k] ?? '').trim());
      if (miss.length) {
        setMissing(new Set(miss));
        setErr('Заполните все обязательные поля');
        const parties: (keyof CxData)[] = ['timestamp', 'owner_full_name', 'owner_personal_number', 'owner_birth_day', 'client_full_name', 'client_personal_number', 'client_birth_day'];
        const property: (keyof CxData)[] = ['cadastral_code', 'street_house', 'apartment_area'];
        setTab(miss.some((k) => parties.includes(k)) ? 'parties' : miss.some((k) => property.includes(k)) ? 'property' : 'money');
        return;
      }
      setStep(4); return;
    }
    if (step === 4) { setStep(5); return; }
  }
  function back() { setErr(''); setStep((s) => Math.max(1, s - 1)); }
  function reset() {
    clearDraft();
    setType(''); setLang(''); setD({ ...EMPTY, timestamp: todayStr() }); setDepTouched(false); setPetTouched(false);
    setStep(1); setErr(''); setMissing(new Set()); setPrefilled(null);
    if (dealId || leadId) setParams((sp) => { sp.delete('deal_id'); sp.delete('lead_id'); return sp; }, { replace: true });
  }

  const fileBase = `Contract_${type || 'x'}_${lang || 'x'}_${(d.cadastral_code || d.client_full_name || 'draft').replace(/[^\w.-]+/g, '_')}`;
  function print() {
    const prev = document.title;
    document.title = fileBase;
    window.print();
    setTimeout(() => { document.title = prev; }, 500);
  }
  function download() {
    if (!doc) return;
    const blob = new Blob(['﻿', docHtml(doc)], { type: 'application/msword' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `${fileBase}.doc`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  }

  // ---- field helper
  const F = ({ k, label, type: t = 'text', placeholder }: { k: keyof CxData; label: string; type?: string; placeholder?: string }) => (
    <Field label={`${label}${req.has(k) ? ' *' : ''}`}>
      <Input type={t} value={String(d[k] ?? '')} placeholder={placeholder} inputMode={t === 'number' ? 'decimal' : undefined}
        aria-invalid={missing.has(k) || undefined} style={missing.has(k) ? { borderColor: 'var(--accent)' } : undefined}
        onChange={(e) => set(k, e.target.value)} />
    </Field>
  );

  return (
    <div className="col gap-lg contract-wizard">
      <div className="no-print">
        {!compact && (
          <PageHead label="Генератор договоров" title="Договоры" actions={<Button variant="secondary" onClick={reset}><RotateCcw size={16} />Новый договор</Button>} />
        )}
        <div className="stepper" style={{ marginBottom: 6 }}>
          {STEPS.map((s, i) => (
            <div key={s} className={`step ${i + 1 < step ? 'done' : i + 1 === step ? 'current' : ''}`}>
              <span className="dot">{i + 1}</span><span className="step-label">{s}</span>
            </div>
          ))}
        </div>
        {prefilled && <div className="alert alert-info mt">{prefilled.text} Проверьте и дополните поля (личные номера сторон вводятся вручную).</div>}
        {deal.error && <div className="alert alert-warn mt">Не удалось загрузить сделку #{dealId}: {errText(deal.error)}</div>}
        {leadC.error && <div className="alert alert-warn mt">Не удалось загрузить заявку #{leadId}: {errText(leadC.error)}</div>}
        {step < 4 && (
          <Card label="Заявка или сделка" title="Подставить данные" className="mt">
            {itemsOf(pickable.data).length === 0 ? (
              <div className="muted" style={{ fontSize: 13 }}>{pickable.isLoading ? 'Загрузка…' : 'Нет заявок после просмотра. Заполните договор вручную.'}</div>
            ) : (
              <Select aria-label="Заявка" value={leadId || ''} onChange={(e) => e.target.value && pickLead(Number(e.target.value))} placeholder="Выбрать заявку — или заполните вручную"
                options={itemsOf(pickable.data).map((l) => ({ value: l.id, label: `#${l.id} · ${l.listing?.address || l.listing?.title || 'объект'} · ${l.client?.name || 'клиент'} · ${LEAD_STATUS[l.status]}${l.agent?.name ? ` · ${l.agent.name}` : ''}` }))} />
            )}
          </Card>
        )}
      </div>

      {step === 1 && (
        <Card label="Шаг 1" title="Тип договора" className="no-print">
          <div className="grid g3">
            {(Object.keys(CX_TYPES) as CxType[]).map((t) => {
              const Icon = TYPE_ICON[t];
              const on = type === t;
              return (
                <button key={t} type="button" className={`choice ${on ? 'on' : ''}`} onClick={() => { setType(t); setErr(''); }} aria-pressed={on}>
                  <Icon size={22} /><span className="grow"><b>{CX_TYPES[t].title}</b><small>{CX_TYPES[t].sub}</small></span>
                </button>
              );
            })}
          </div>
        </Card>
      )}

      {step === 2 && (
        <Card label="Шаг 2" title="Язык договора" className="no-print">
          <div className="grid g3">
            {(Object.keys(CX_LANGS) as CxLang[]).map((l) => {
              const on = lang === l;
              return (
                <button key={l} type="button" className={`choice ${on ? 'on' : ''}`} onClick={() => { setLang(l); setErr(''); }} aria-pressed={on}>
                  <span className="grow"><b lang={l === 'GEO' ? 'ka' : undefined}>{CX_LANGS[l].title}</b><small>{CX_LANGS[l].sub}</small></span><Badge tone={on ? 'solid' : 'blue'}>{l}</Badge>
                </button>
              );
            })}
          </div>
        </Card>
      )}

      {step === 3 && type && (
        <Card label={`Шаг 3 · ${CX_TYPES[type].title} · ${lang}`} title="Данные договора" className="no-print">
          <Tabs value={tab} onChange={setTab} tabs={[{ value: 'parties', label: 'Стороны' }, { value: 'property', label: 'Объект' }, { value: 'money', label: sale ? 'Цена' : 'Финансы' }]} />
          {tab === 'parties' && (
            <div className="form-grid">
              <div className="span2">{F({ k: 'timestamp', label: 'Дата договора', type: 'date' })}</div>
              {F({ k: 'owner_full_name', label: sale ? 'Продавец (ФИО)' : 'Собственник (ФИО)', placeholder: 'Полное имя' })}
              {F({ k: 'owner_personal_number', label: sale ? 'Личный номер продавца' : 'Личный номер собственника', placeholder: 'Personal ID' })}
              {sale && <div className="span2">{F({ k: 'owner_birth_day', label: 'Дата рождения продавца', type: 'date' })}</div>}
              {F({ k: 'client_full_name', label: sale ? 'Покупатель (ФИО)' : 'Арендатор (ФИО)', placeholder: 'Полное имя' })}
              {F({ k: 'client_personal_number', label: sale ? 'Личный номер покупателя' : 'Личный номер арендатора', placeholder: 'Personal ID' })}
              {sale && <div className="span2">{F({ k: 'client_birth_day', label: 'Дата рождения покупателя', type: 'date' })}</div>}
            </div>
          )}
          {tab === 'property' && (
            <div className="col gap-lg">
              <div className="form-grid">
                <div className="span2">{F({ k: 'cadastral_code', label: 'Кадастровый код', placeholder: '01.10.10.02.20.0.01.01.84A' })}</div>
                <div className="span2">{F({ k: 'street_house', label: 'Адрес', placeholder: 'Улица, номер дома' })}</div>
                {F({ k: 'floor', label: 'Этаж', placeholder: '2' })}
                {F({ k: 'apartment_number', label: 'Квартира №', placeholder: 'Номер' })}
                <div className="span2">{F({ k: 'apartment_area', label: 'Площадь, м²', placeholder: '45 или 45.05' })}</div>
              </div>
            </div>
          )}
          {tab === 'money' && (sale ? (
            <div className="form-grid">
              {F({ k: 'sale_price', label: 'Цена продажи, USD', type: 'number', placeholder: 'Итоговая цена' })}
              {F({ k: 'transfer_date', label: 'Дата передачи', type: 'date' })}
            </div>
          ) : (
            <div className="col gap-lg">
              <div className="form-grid">
                {F({ k: 'start_rent', label: 'Начало аренды', type: 'date' })}
                {F({ k: 'end_rent', label: 'Окончание аренды', type: 'date' })}
                {F({ k: 'rent_duration_months', label: 'Срок, месяцев', type: 'number', placeholder: 'Количество месяцев' })}
                {F({ k: 'pay_day', label: 'День оплаты (1–31)', type: 'number', placeholder: 'Число месяца' })}
                {F({ k: 'price_usd', label: 'Цена в месяц, USD', type: 'number', placeholder: 'Ежемесячная плата' })}
                {F({ k: 'deposit_usd', label: 'Депозит, USD', type: 'number', placeholder: 'Сумма депозита' })}
              </div>
              {d.start_rent && <div className="muted" style={{ fontSize: 13 }}>Первый платёж за период {cxFmt(d.start_rent)} – {cxOnem(d.start_rent)}</div>}
              <div className="outcome-card col">
                <Switch checked={d.with_pets} onChange={(v) => set('with_pets', v)} label="С животными" />
                <span className="muted" style={{ fontSize: 12 }}>Добавляет пункт о депозите за животных</span>
                {d.with_pets && (
                  <Field label="Депозит за животных, USD" hint="По умолчанию 50% основного депозита">
                    <Input type="number" value={d.pet_deposit} onChange={(e) => { setPetTouched(true); set('pet_deposit', e.target.value); }} />
                  </Field>
                )}
              </div>
            </div>
          ))}
        </Card>
      )}

      {step === 4 && type && (
        <Card label="Шаг 4" title="Проверка данных" className="no-print" actions={<Button size="sm" variant="ghost" onClick={() => setStep(3)}>Изменить</Button>}>
          <dl className="kv">
            <dt>Тип</dt><dd>{CX_TYPES[type].title}</dd>
            <dt>Язык</dt><dd>{lang && CX_LANGS[lang].title}</dd>
            {reviewLabels(type).map(([k, label]) => {
              let v = k === 'one_month' ? cxOnem(d.start_rent) : String(d[k] ?? '');
              if (k !== 'one_month' && DATE_FIELDS.includes(k)) v = cxFmt(v);
              return <div key={k} style={{ display: 'contents' }}><dt>{label}</dt><dd>{v || '—'}</dd></div>;
            })}
            {!sale && <><dt>С животными</dt><dd>{d.with_pets ? `да, депозит ${d.pet_deposit || 0} USD` : 'нет'}</dd></>}
          </dl>
        </Card>
      )}

      {step === 5 && doc && (
        <>
          <div className="row row-wrap no-print">
            <Button onClick={print}><Printer size={16} />Печать или PDF</Button>
            <Button variant="secondary" onClick={download}><Download size={16} />Скачать .doc</Button>
            <Button variant="ghost" onClick={() => setStep(3)}>Изменить данные</Button>
            <span className="muted grow" style={{ fontSize: 12, minWidth: 200 }}>В окне печати выберите «Сохранить как PDF», чтобы скачать файл</span>
          </div>
          <div className="contract-paper-wrap"><ContractDocument doc={doc} /></div>
        </>
      )}

      {err && <div className="alert alert-warn no-print" role="alert">{err}</div>}

      {step < 5 && (
        <div className="row no-print" style={{ justifyContent: 'flex-end' }}>
          {step > 1 && <Button variant="secondary" onClick={back}>Назад</Button>}
          <Button onClick={next}>{step === 4 ? <><FileText size={16} />Создать договор</> : 'Далее'}</Button>
        </div>
      )}
      {step === 5 && compact && <Button variant="secondary" className="no-print" onClick={reset}><RotateCcw size={16} />Новый договор</Button>}
    </div>
  );
}

export function ContractsPage() { return <ContractWizard />; }
export function MContracts() { return <ContractWizard compact />; }
