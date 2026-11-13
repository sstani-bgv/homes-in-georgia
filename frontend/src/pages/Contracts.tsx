// /contracts and /m/contracts — contract generator (port of the old CRM tab «tab-contract»).
// Output: «Сохранить PDF» (the server prints this HTML with headless Chrome → stored contract, features/contracts/
// ContractCard), the printable A4 preview (window.print + the same print CSS) and a .doc file for Word.
import { useEffect, useMemo, useRef, useState } from 'react';
import { cadastralError, cadastralHint, cadastralInput } from '../lib/cadastral';
import { useSearchParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Building2, Copy, Download, ExternalLink, FileText, Home, KeyRound, Plus, Printer, RotateCcw, Save, Send, UserCheck } from 'lucide-react';
import { Link } from 'react-router-dom';
import { api } from '../api';
import { itemsOf } from '../api/client';
import type { Contract, Deal, LeadContract } from '../api/types';
import { ContractActions, ContractList, ContractStatusBadge, ContractView } from '../features/contracts/ContractCard';
import { insideTelegram, tgWebApp, useAuth } from '../auth/AuthContext';
import { errText, useToast } from '../components/toast';
import { Badge, Button, Card, Field, Input, Modal, PageHead, Select, Switch, Tabs } from '../components/ui';
import { REG_RU, RegistryApps, useSettledId } from '../components/RegistryApps';
import { CONTRACT_STAGES, LEAD_STATUS, todayStr } from '../lib/format';
import {
  CX_LANGS, CX_TYPES, DATE_FIELDS, EMPTY, addMonthsIso, buildContract, cxFmt, cxOnem, langsFor, monthsBetween, requiredFields, reviewLabels,
  type ContractDoc, type CxData, type CxLang, type CxType, type DraftBlock, type DraftCell, type DraftDoc, type DraftKey, ENABLED_TYPES } from '../features/contracts/templates';

const STEPS = ['Тип', 'Язык', 'Данные', 'Проверка', 'Документ'];
const TYPE_ICON: Record<CxType, typeof Home> = { rent: Home, comm: Building2, sale: KeyRound };
const AUTOSAVE_MS = 800;

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

const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
/** "**bold**" → <b>; an unclosed ** runs to the end of the piece. */
const inline = (s: string) => esc(s).split('**').map((x, i) => (i % 2 ? `<b>${x}</b>` : x)).join('');

/** One paragraph. David's drafts push the date / the second signature to the right with long runs of spaces:
 *  those become a space-between row (bold markers carried across the split). */
function para(text: string, a?: 'c' | 'r'): string {
  if (!text) return '<div class="cd-gap"></div>';
  let open = false;
  const parts = text.split(/ {8,}/).map((x) => {
    const y = (open ? '**' : '') + x;
    open = (y.match(/\*\*/g) || []).length % 2 === 1;
    return y;
  }).filter((x) => x.replace(/\*\*/g, '').trim());
  const body = parts.length > 1 ? `<span class="cd-spread">${parts.map((x) => `<span>${inline(x)}</span>`).join('')}</span>` : inline(parts[0] ?? '');
  return `<p class="cd-p${a ? ` cd-${a}` : ''}">${body}</p>`;
}

function blocks(bs: DraftBlock[]): string {
  return bs.map((b) => {
    if (typeof b === 'string') return para(b);
    if ('p' in b) return para(b.p, b.a);
    // the bilingual drafts are one two-column table (Georgian | Russian/English); others are bordered grids
    const cols = b.t.every((r) => r.length === 2);
    const cell = (c: DraftCell) => (Array.isArray(c) ? `<td>${blocks(c)}</td>` : `<td colspan="${c.s}">${blocks(c.c)}</td>`);
    return `<table class="${cols ? 'cd-cols' : 'cd-grid'}">${b.t.map((r) => `<tr>${r.map(cell).join('')}</tr>`).join('')}</table>`;
  }).join('');
}

const DOC_CSS = `.contract-doc .cd-p{margin:0 0 3px;text-align:justify;white-space:pre-line}.contract-doc .cd-c{text-align:center}
.contract-doc .cd-r{text-align:right}.contract-doc .cd-gap{height:.7em}.contract-doc .cd-spread{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap}
.contract-doc{position:relative;background:url(/brand/contract-watermark.png) top center/100% auto repeat-y}
.contract-doc .cd-wm{display:none}.contract-doc .cd-head .cd-p{display:inline;font-weight:700;text-decoration:underline;color:#111}
.contract-doc .cd-flag{height:13px;margin-left:6px;vertical-align:-1px}.contract-doc .cd-head{margin-bottom:10px}
.contract-doc .cd-hf{font-size:8.5pt;color:#444}.contract-doc .cd-hf .cd-p{text-align:left}.contract-doc .cd-foot{margin-top:18px}
.contract-doc table{width:100%;border-collapse:collapse;margin:6px 0}.contract-doc .cd-cols{table-layout:fixed;font-size:9.5pt}
.contract-doc .cd-cols td{width:50%;vertical-align:top;padding:0 8px}.contract-doc .cd-grid td{border:1px solid #111;padding:3px 6px;vertical-align:top}
.contract-doc .cd-grid .cd-p{margin:0}
@media screen and (max-width:767px){.contract-doc .cd-cols td{display:block;width:auto;padding:0 0 12px}}`;

const FLAG = '<img class="cd-flag" src="/brand/ge-flag.svg" alt="">';
const WATERMARK = '/brand/contract-watermark.png';  // David's drafts: the round Rent in Tbilisi logo, faint, on every page

function docInner(doc: ContractDoc): string {
  const hf = (xs: string[], cls: string, flag = '') => (xs.length ? `<div class="cd-hf ${cls}">${xs.map((x) => para(x)).join('')}${flag}</div>` : '');
  return `<img class="cd-wm" src="${WATERMARK}" alt="">` + hf(doc.header, 'cd-head', FLAG) + blocks(doc.body) + hf(doc.footer, 'cd-foot');
}

/** Print: the header (+ flag) and the page number in the top margin, the initials line at the bottom of every page;
 *  the inline copies are hidden then (Chrome @page margin boxes). */
function pageCss(doc: ContractDoc): string {
  const str = (x: string) => `"${x.replace(/\*\*/g, '').replace(/ {8,}/g, '        ').replace(/["\\]/g, '\\$&')}"`;
  const head = doc.header.length ? `@top-left{content:${str(doc.header.join(' '))} url(/brand/ge-flag.svg);font:700 10pt Montserrat,Arial,sans-serif;text-decoration:underline;vertical-align:bottom}` : '';
  const foot = doc.footer.length ? `@bottom-center{content:${str(doc.footer.join(' '))};font:8.5pt 'Noto Sans Georgian',Arial,sans-serif;color:#444;white-space:pre}` : '';
  return `@media print{@page{size:A4;margin:18mm 15mm 16mm;${head}@top-right{content:counter(page);font:9pt Arial,sans-serif}${foot}}
.contract-doc .cd-head,.contract-doc .cd-foot{display:none}.contract-doc .cd-wm{display:block;position:fixed;inset:0;width:100%;height:100%;object-fit:contain;z-index:-1}}`;
}

export function ContractDocument({ doc, editable }: { doc: ContractDoc; editable?: boolean }) {
  return (
    <article className="contract-doc" lang="ka">
      <style>{DOC_CSS + pageCss(doc)}</style>
      {/* editable before printing / downloading: a clause can be changed for one client without touching the drafts */}
      <div className="cd-body" contentEditable={editable} suppressContentEditableWarning spellCheck={false}
           dangerouslySetInnerHTML={{ __html: docInner(doc) }} />
    </article>
  );
}

/** Standalone HTML (opens in Word / LibreOffice as an editable document, like the old «Редактировать DOC»). */
function docHtml(doc: ContractDoc): string {
  // the text as edited in the preview (Irakli: «иногда приходится редактировать договоры»), else as generated
  const inner = document.querySelector('.contract-doc .cd-body')?.innerHTML || docInner(doc);
  return `<!doctype html><html><head><meta charset="utf-8"><title>${esc(doc.title)}</title><style>body{font-family:'Noto Sans Georgian','Montserrat',Arial,sans-serif;font-size:10.5pt;line-height:1.45}${DOC_CSS}</style></head><body><div class="contract-doc">${inner}</div></body></html>`;
}

/** The page the server prints to PDF: the edited text + the print CSS of «Печать» (header, page numbers, initials,
 *  watermark). Brand images stay /brand/… links: the server inlines them. */
function pdfHtml(doc: ContractDoc): string {
  const inner = document.querySelector('.contract-doc .cd-body')?.innerHTML || docInner(doc);
  const base = `body{margin:0;background:#fff;color:#111}.contract-doc{font-family:'Noto Sans Georgian','Montserrat',Arial,sans-serif;font-size:10.5pt;line-height:1.55}`;
  return `<!doctype html><html lang="ka"><head><meta charset="utf-8"><title>${esc(doc.title)}</title><style>${base}${DOC_CSS}${pageCss(doc)}</style></head><body><article class="contract-doc" lang="ka"><div class="cd-body">${inner}</div></article></body></html>`;
}

/** David's drafts are ~370 KB of text: loaded only when the contract page needs them (own chunk). */
function useDrafts() {
  const [drafts, setDrafts] = useState<Record<DraftKey, DraftDoc> | null>(null);
  useEffect(() => { import('../features/contracts/templates-david').then((m) => setDrafts(m.DRAFTS)); }, []);
  return drafts;
}

// ---------------------------------------------------------------- wizard

export function ContractWizard({ compact }: { compact?: boolean }) {
  const toast = useToast();
  const [params, setParams] = useSearchParams();
  const qc = useQueryClient();
  const dealId = Number(params.get('deal_id')) || 0;
  const leadId = Number(params.get('lead_id')) || 0;
  const editId = Number(params.get('edit')) || 0;  // the stored contract (a draft being filled or a generated one)

  const [step, setStep] = useState(1);
  const [type, setType] = useState<CxType | ''>('');
  const [lang, setLang] = useState<CxLang | ''>('');
  const [d, setD] = useState<CxData>({ ...EMPTY, timestamp: todayStr() });
  const [depTouched, setDepTouched] = useState(false);
  const [endTouched, setEndTouched] = useState(false); // the agent typed the end date: no more auto end
  const [tab, setTab] = useState<'parties' | 'property' | 'money'>('parties');
  const [err, setErr] = useState('');
  const [missing, setMissing] = useState<Set<keyof CxData>>(new Set());
  const [prefilled, setPrefilled] = useState<{ key: string; text: string } | null>(null);
  const [petTouched, setPetTouched] = useState(false);

  // ---- the contract lives in the deal: the form autosaves into it (contracts.autosave) and reopens from it
  const [contract, setContract] = useState<Contract | null>(null);  // the server's copy: status, PDF, versions
  const saved = contract?.has_pdf ? contract : null;
  const cidRef = useRef(editId);
  const restored = useRef(0);
  const touched = useRef(false);  // only the agent's input (or a lead prefill) saves, not opening the form
  const chain = useRef<Promise<unknown>>(Promise.resolve());  // autosaves one after another: one draft, no races
  const [autoState, setAutoState] = useState<{ at?: string; err?: string }>({});
  // the deal's open draft: «Оформить договор» again continues it instead of starting over
  const dealDraft = useQuery({ queryKey: ['contracts', 'draft-of', leadId, dealId],
    queryFn: () => api.contracts.list({ status: 'draft', ...(leadId ? { lead_id: leadId } : { deal_id: dealId }) }),
    enabled: !editId && !!(leadId || dealId) });
  const existing = itemsOf(dealDraft.data)[0];
  useEffect(() => {
    if (existing && !editId) setParams((sp) => { sp.set('edit', String(existing.id)); return sp; }, { replace: true });
  }, [existing?.id]);  // eslint-disable-line react-hooks/exhaustive-deps
  const fresh = !editId && dealDraft.isSuccess && !existing;  // no draft yet: prefill from the lead / deal
  const stored = useQuery({ queryKey: ['contracts', 'one', editId], queryFn: () => api.contracts.get(editId), enabled: !!editId });
  useEffect(() => {
    const c = stored.data;
    if (!c || restored.current === c.id) return;
    restored.current = c.id;
    restore(c);
  }, [stored.data]);  // eslint-disable-line react-hooks/exhaustive-deps
  function restore(c: Contract) {
    const x = c.data || {};
    const data = Object.fromEntries(Object.entries(x).filter(([k]) => !k.startsWith('_'))) as Partial<CxData>;
    setType(c.kind && ENABLED_TYPES.includes(c.kind) ? c.kind : '');
    setLang(c.lang || '');
    setD({ ...EMPTY, timestamp: todayStr(), ...data });
    setDepTouched(!!x._dep); setEndTouched(!!x._end);
    setStep(c.has_pdf ? 3 : Math.min(5, Number(x._step) || (c.kind ? 2 : 1)));
    cidRef.current = c.id;
    setContract(c);
    touched.current = false;
    setPrefilled({ key: `contract:${c.id}`, text: c.has_pdf
      ? `Договор #${c.id} · ${c.status_label}. Поменяйте данные и нажмите «Обновить PDF»: договор перегенерируется, прошлая версия PDF сохранится.`
      : `Черновик договора #${c.id} восстановлен — продолжайте с того же места.` });
  }
  const current = contract ?? stored.data;
  const locked = !!current && current.status !== 'draft';  // signed / closed: not edited
  const linkLead = leadId || contract?.lead_id || 0;
  const linkDeal = dealId || contract?.deal_id || 0;

  // ---- prefill from /contracts?deal_id=
  const deal = useQuery({ queryKey: ['deal', dealId], queryFn: () => api.deals.get(dealId), enabled: !!dealId && fresh });
  const prop = useQuery({ queryKey: ['property', deal.data?.property_id], queryFn: () => api.properties.get(deal.data!.property_id!), enabled: !!deal.data?.property_id, retry: false });
  useEffect(() => {
    if (!deal.data || prefilled?.key === `deal:${deal.data.id}` || (deal.data.property_id && prop.isLoading)) return;
    applyDeal(deal.data, prop.data);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deal.data, prop.data, prop.isLoading]);

  function apply(type: CxType, data: Partial<CxData>, key: string, text: string) {
    touched.current = true;  // the lead's data is the draft's start: saved right away
    setErr('');
    setType(type);
    const filled = Object.fromEntries(Object.entries(data).filter(([, v]) => v !== '')) as Partial<CxData>;
    if (filled.start_rent && filled.end_rent && !filled.rent_duration_months) filled.rent_duration_months = monthsBetween(filled.start_rent, filled.end_rent);
    if (filled.with_pets && !filled.pet_deposit && Number(filled.deposit_usd) > 0) filled.pet_deposit = String(Math.round(Number(filled.deposit_usd) * 0.5));
    setD((cur) => ({ ...EMPTY, timestamp: cur.timestamp || todayStr(), ...filled }));
    setDepTouched(false);
    setEndTouched(false);
    setPrefilled({ key, text });
    setStep((s) => (s < 2 ? 2 : s));
    toast(`Данные подставлены из ${key.startsWith('lead') ? 'заявки' : 'сделки'} #${key.split(':')[1]}`);
  }
  function applyDeal(x: Deal, p?: Parameters<typeof fromDeal>[1]) {
    const r = fromDeal(x, p);
    apply(r.type, r.data, `deal:${x.id}`, `Данные подставлены из сделки #${x.id}${x.address ? ` · ${x.address}` : ''}.`);
  }

  // ---- prefill from /contracts?lead_id= (lead card «Оформить договор», or the picker below)
  const leadC = useQuery({ queryKey: ['lead-contract', leadId], queryFn: () => api.leads.contract(leadId), enabled: !!leadId && fresh });
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
    cidRef.current = 0; restored.current = 0; touched.current = false; setContract(null);
    setParams({ lead_id: String(id) }, { replace: true });
  }

  // ---- autosave: debounced, from a partly filled form; the wizard's step / sync flags go along (_step, _dep, _end)
  const payloadData = () => ({ ...d, _step: step, _dep: depTouched, _end: endTouched });
  useEffect(() => {
    if (!touched.current || locked) return;  // a standalone contract (no lead / deal) autosaves too (11-13)
    const t = setTimeout(() => {
      const body = { id: cidRef.current || null, kind: type, lang, lead_id: linkLead || null, deal_id: linkDeal || null, data: payloadData() };
      chain.current = chain.current.then(() => api.contracts.autosave(body).then((c) => {
        const first = !cidRef.current;
        cidRef.current = c.id; restored.current = c.id;
        setContract(c);
        setAutoState({ at: new Date().toLocaleTimeString('ru', { hour: '2-digit', minute: '2-digit' }) });
        if (first) {
          setParams((sp) => { sp.set('edit', String(c.id)); return sp; }, { replace: true });
          qc.invalidateQueries({ queryKey: ['contracts', 'list'] });
        }
      }, (e) => setAutoState({ err: errText(e) })));
    }, AUTOSAVE_MS);
    return () => clearTimeout(t);
  }, [type, lang, d, depTouched, endTouched, step]);  // eslint-disable-line react-hooks/exhaustive-deps

  // ---- field updates with the old auto-sync rules
  const set = (k: keyof CxData, v: string | boolean) => {
    touched.current = true;
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
      // the term drives the dates: start defaults to today, end = start + term until the agent types the end himself
      if (k === 'rent_duration_months' && !n.start_rent && Number(v) > 0) n.start_rent = new Date().toLocaleDateString('sv');
      if ((k === 'rent_duration_months' || k === 'start_rent') && !endTouched && Number(n.rent_duration_months) > 0 && n.start_rent) {
        n.end_rent = addMonthsIso(n.start_rent, Number(n.rent_duration_months));
      } else if (k === 'start_rent' || k === 'end_rent') {
        const m = monthsBetween(n.start_rent, n.end_rent);
        if (m) n.rent_duration_months = m;
      }
      // pay day = the start's day, while it is empty or still the old start's day
      const dayOf = (iso: string) => (iso ? String(Number(iso.slice(8, 10))) : '');
      if (n.start_rent !== x.start_rent && (!x.pay_day || x.pay_day === dayOf(x.start_rent))) n.pay_day = dayOf(n.start_rent);
      return n;
    });
    if (k === 'deposit_usd') setDepTouched(true);
    if (k === 'end_rent') setEndTouched(!!v);
  };
  const sale = type === 'sale';
  const req = useMemo(() => new Set(type ? requiredFields(type) : []), [type]);
  const ownerPid = useSettledId(String(d.owner_personal_number ?? ''));  // → the owner's NAPR applications under the cadastral field
  const drafts = useDrafts();
  const doc = useMemo(() => (drafts && type && lang ? buildContract(drafts, type, lang, d) : null), [drafts, type, lang, d]);
  // David has no Commercial GEO+ENG and only a Georgian sale draft: drop a language the chosen type doesn't have
  useEffect(() => { if (type && lang && !langsFor(type).includes(lang)) setLang(''); }, [type, lang]);

  function next() {
    setErr('');
    if (step === 1) {
      if (!type) { setErr('Выберите тип договора'); return; }
      setStep(2); return;
    }
    if (step === 2) { if (!lang) { setErr('Выберите язык договора'); return; } setStep(3); return; }
    if (step === 3) {
      // «Далее» walks the sub-tabs first, validation only from the last one (11-13: it jumped straight to step 4)
      if (tab !== 'money') { goTab(tab === 'parties' ? 'property' : 'money'); return; }
      const miss = requiredFields(type as CxType).filter((k) => !String(d[k] ?? '').trim());
      if (miss.length) {
        setMissing(new Set(miss));
        setErr('Заполните все обязательные поля');
        const parties: (keyof CxData)[] = ['timestamp', 'owner_full_name', 'owner_personal_number', 'owner_birth_day', 'client_full_name', 'client_personal_number', 'client_birth_day'];
        const property: (keyof CxData)[] = ['cadastral_code', 'street_house', 'apartment_area'];
        goTab(miss.some((k) => parties.includes(k)) ? 'parties' : miss.some((k) => property.includes(k)) ? 'property' : 'money');
        return;
      }
      if (cadastralError(d.cadastral_code)) { setErr('Проверьте кадастровый код'); goTab('property'); return; }
      setStep(4); return;
    }
    if (step === 4) { setStep(5); return; }
  }
  function back() {
    setErr('');
    if (step === 3 && tab !== 'parties') { goTab(tab === 'money' ? 'property' : 'parties'); return; }  // mirrors «Далее»
    setStep((s) => Math.max(1, s - 1));
  }
  // a sub-tab switch scrolls to the card's top: on a phone «Далее» is at the bottom of a long form
  function goTab(t: typeof tab) {
    setTab(t);
    requestAnimationFrame(() => document.querySelector('.cx-data')?.scrollIntoView({ behavior: 'smooth', block: 'start' }));
  }
  function reset() {
    // the current draft stays in its deal (Кабинет → Договоры); the form starts a new one
    cidRef.current = 0; restored.current = 0; touched.current = false; setContract(null); setAutoState({});
    setType(''); setLang(''); setD({ ...EMPTY, timestamp: todayStr() }); setDepTouched(false); setPetTouched(false);
    setStep(1); setErr(''); setMissing(new Set()); setPrefilled(null);
    setParams({ new: '1' }, { replace: true });
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
  // Telegram's webview ignores blob / <a download>: the bot sends the same file to the agent's chat instead
  const tg = insideTelegram();
  // «Сохранить PDF» generates the autosaved draft; after an edit in the form «Обновить PDF» regenerates it (the old
  // PDF is kept as a version) while it is a draft
  const save = useMutation({
    mutationFn: async () => {
      const b = { kind: type as CxType, lang: lang as CxLang, html: pdfHtml(doc!), title: doc!.title, cadastral: d.cadastral_code,
        address: d.street_house, client_name: d.client_full_name, owner_name: d.owner_full_name,
        lead_id: linkLead || leadC.data?.lead_id || null, deal_id: linkDeal || leadC.data?.deal_id || null,
        data: payloadData() };  // the wizard's CxData: the deal page prefills from the signed contract (contracts.data)
      await chain.current;  // a pending autosave lands first
      return cidRef.current ? api.contracts.update(cidRef.current, b) : api.contracts.create(b);
    },
    onSuccess: (c) => {
      const first = !cidRef.current;
      cidRef.current = c.id; restored.current = c.id;
      setContract(c);
      if (first) setParams((sp) => { sp.set('edit', String(c.id)); return sp; }, { replace: true });
      qc.invalidateQueries({ queryKey: ['contracts'] });
      toast(c.versions?.length ? `PDF обновлён: договор #${c.id}, версия ${c.versions.length + 1}` : `PDF сохранён: договор #${c.id}`);
    },
    onError: (e) => toast(errText(e), true),
  });
  const sendTg = useMutation({
    mutationFn: () => api.contracts.send(`${fileBase}.doc`, docHtml(doc!)),
    onSuccess: () => toast('Договор отправлен вам в Telegram'),
    onError: (e) => toast(errText(e), true),
  });

  // ---- field helper
  // red only while still empty: the term fills the pay day and the dates itself, they must not stay red (11-13)
  const bad = (k: keyof CxData) => missing.has(k) && !String(d[k] ?? '').trim();
  const F = ({ k, label, type: t = 'text', placeholder }: { k: keyof CxData; label: string; type?: string; placeholder?: string }) => (
    <Field label={`${label}${req.has(k) ? ' *' : ''}`} hint={k === 'cadastral_code' ? cadastralHint(String(d[k] ?? '')) : undefined}>
      <Input type={t} value={String(d[k] ?? '')} placeholder={placeholder} inputMode={t === 'number' ? 'decimal' : undefined}
        aria-invalid={bad(k) || undefined} style={bad(k) ? { borderColor: 'var(--accent)' } : undefined}
        onChange={(e) => set(k, k === 'cadastral_code' ? cadastralInput(e) : e.target.value)} />
    </Field>
  );

  if (locked && current) return (
    <div className="alert alert-info">
      Договор #{current.id} · {current.status_label}: подписанный или закрытый договор не меняется.{' '}
      <Link to={`?id=${current.id}`}>Открыть договор</Link>
    </div>
  );

  return (
    <div className="col gap-lg contract-wizard">
      <div className="no-print">
        {!compact && (
          <PageHead label="Генератор договоров" title="Договоры" actions={<Button variant="secondary" onClick={reset}><RotateCcw size={16} />Новый договор</Button>} />
        )}
        {(contract || autoState.err) && (
          <div className="row row-wrap gap-sm" style={{ fontSize: 12, marginBottom: 6 }} aria-live="polite">
            {contract && <ContractStatusBadge c={contract} />}
            {contract && <span className="muted">Договор #{contract.id}{linkLead ? ` · заявка #${linkLead}` : ''}{linkDeal ? ` · сделка #${linkDeal}` : ''}</span>}
            {autoState.err ? <span style={{ color: 'var(--accent)' }}>Черновик не сохранён: {autoState.err}</span>
              : autoState.at && <span className="faint">Сохранено в {autoState.at}</span>}
          </div>
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
        {prefilled && step < 4 && !prefilled.key.startsWith('contract') && <Button size="sm" variant="ghost" className="no-print" onClick={() => setPrefilled(null)}>Подставить из другой заявки</Button>}
        {step < 4 && !prefilled && (
          <Card label="Сделка · необязательно" title="Заявка договора" className="mt">
            {itemsOf(pickable.data).length === 0 ? (
              <div className="muted" style={{ fontSize: 13 }}>{pickable.isLoading ? 'Загрузка…' : 'Нет заявок после просмотра: договор можно оформить и без заявки, привязать позже из карточки договора.'}</div>
            ) : (
              <Select aria-label="Заявка" value={leadId || ''} onChange={(e) => e.target.value && pickLead(Number(e.target.value))} placeholder="Без заявки (можно привязать позже) — или выберите заявку"
                options={itemsOf(pickable.data).map((l) => ({ value: l.id, label: `#${l.id} · ${l.listing?.address || l.listing?.title || 'объект'} · ${l.client?.name || 'клиент'} · ${LEAD_STATUS[l.status]}${l.agent?.name ? ` · ${l.agent.name}` : ''}` }))} />
            )}
          </Card>
        )}
      </div>

      {step === 1 && (
        <Card label="Шаг 1" title="Тип договора" className="no-print">
          <div className="grid g3">
            {ENABLED_TYPES.map((t) => {
              const Icon = TYPE_ICON[t];
              const on = type === t;
              return (
                <button key={t} type="button" className={`choice ${on ? 'on' : ''}`} onClick={() => { touched.current = true; setType(t); setErr(''); }} aria-pressed={on}>
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
              const off = !!type && !langsFor(type).includes(l);
              return (
                <button key={l} type="button" className={`choice ${on ? 'on' : ''}`} disabled={off} style={off ? { opacity: 0.45, cursor: 'not-allowed' } : undefined}
                  onClick={() => { touched.current = true; setLang(l); setErr(''); }} aria-pressed={on}>
                  <span className="grow"><b lang={l === 'GEO' ? 'ka' : undefined}>{CX_LANGS[l].title}</b><small>{off ? 'Нет такого шаблона для этого типа договора' : CX_LANGS[l].sub}</small></span><Badge tone={on ? 'solid' : 'blue'}>{l}</Badge>
                </button>
              );
            })}
          </div>
        </Card>
      )}

      {step === 3 && type && (
        <Card label={`Шаг 3 · ${CX_TYPES[type].title} · ${lang}`} title="Данные договора" className="no-print cx-data">
          <Tabs value={tab} onChange={setTab} tabs={[{ value: 'parties', label: 'Стороны' }, { value: 'property', label: 'Объект' }, { value: 'money', label: sale ? 'Цена' : 'Финансы' }]} />
          {tab === 'parties' && (
            <div className="form-grid">
              <div className="span2">{F({ k: 'timestamp', label: 'Дата договора', type: 'date' })}</div>
              {F({ k: 'owner_full_name', label: sale ? 'Продавец (ФИО)' : 'Собственник (ФИО)', placeholder: 'Полное имя' })}
              <div>{F({ k: 'owner_personal_number', label: sale ? 'Личный номер продавца' : 'Личный номер собственника', placeholder: 'Personal ID' })}
                <CecCheck pid={String(d.owner_personal_number ?? '')} fullName={String(d.owner_full_name ?? '')} /></div>
              {sale && <div className="span2">{F({ k: 'owner_birth_day', label: 'Дата рождения продавца', type: 'date' })}</div>}
              {F({ k: 'client_full_name', label: sale ? 'Покупатель (ФИО)' : 'Арендатор (ФИО)', placeholder: 'Полное имя' })}
              <div>{F({ k: 'client_personal_number', label: sale ? 'Личный номер покупателя' : 'Личный номер арендатора', placeholder: 'Personal ID' })}
                <CecCheck pid={String(d.client_personal_number ?? '')} fullName={String(d.client_full_name ?? '')} /></div>
              {sale && <div className="span2">{F({ k: 'client_birth_day', label: 'Дата рождения покупателя', type: 'date' })}</div>}
            </div>
          )}
          {tab === 'property' && (
            <div className="col gap-lg">
              <div className="form-grid">
                <div className="span2">{F({ k: 'cadastral_code', label: 'Кадастровый код', placeholder: '01.10.13.015.029.01.150' })}</div>
                <div className="span2">
                  {ownerPid ? <RegistryApps base="/registry/applications" personalId={ownerPid} t={REG_RU}
                    onCode={(c) => { if (c !== d.cadastral_code) { set('cadastral_code', c); toast(`Из реестра: ${c}`); } }} />
                    : <span className="faint" style={{ fontSize: 12 }}>Введите личный номер собственника на вкладке «Стороны» — здесь появятся его заявки в Публичном реестре с кадастровым кодом.</span>}
                </div>
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
                {F({ k: 'rent_duration_months', label: 'Срок, месяцев', type: 'number', placeholder: 'Количество месяцев' })}
                {F({ k: 'pay_day', label: 'День оплаты (1–31)', type: 'number', placeholder: 'Число месяца' })}
                {F({ k: 'price_usd', label: 'Цена в месяц, USD', type: 'number', placeholder: 'Ежемесячная плата' })}
                {F({ k: 'deposit_usd', label: 'Депозит, USD', type: 'number', placeholder: 'Равен цене, можно изменить' })}
              </div>
              {type === 'rent' && <div className="outcome-card col">
                <Switch checked={d.with_pets} onChange={(v) => set('with_pets', v)} label="С животными" />
                <span className="muted" style={{ fontSize: 12 }}>Договор по шаблону Pet DRAFT (с депозитом за животных)</span>
                {d.with_pets && (
                  <Field label="Депозит за животных, USD" hint="По умолчанию 50% основного депозита">
                    <Input type="number" value={d.pet_deposit} onChange={(e) => { setPetTouched(true); set('pet_deposit', e.target.value); }} />
                  </Field>
                )}
              </div>}
              <div className="form-grid">
                {F({ k: 'start_rent', label: 'Начало аренды', type: 'date' })}
                {F({ k: 'end_rent', label: 'Окончание аренды', type: 'date' })}
              </div>
              <div className="muted" style={{ fontSize: 13 }}>
                {d.start_rent ? `Первый платёж за период ${cxFmt(d.start_rent)} – ${cxOnem(d.start_rent)}. ` : ''}
                {endTouched ? 'Окончание введено вручную.' : 'Окончание = начало + срок.'}
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
            {type === 'rent' && <><dt>С животными</dt><dd>{d.with_pets ? `да, депозит ${d.pet_deposit || 0} USD` : 'нет'}</dd></>}
          </dl>
        </Card>
      )}

      {step === 5 && !doc && <div className="muted no-print">Загрузка шаблона…</div>}
      {step === 5 && doc && (
        <>
          <div className="row row-wrap no-print">
            <Button loading={save.isPending} onClick={() => save.mutate()}><Save size={16} />{saved ? 'Обновить PDF' : 'Сохранить PDF'}</Button>
            {!tg && <Button variant="secondary" onClick={print}><Printer size={16} />Печать</Button>}
            {tg ? <Button variant="secondary" loading={sendTg.isPending} onClick={() => sendTg.mutate()}><Send size={16} />.doc в Telegram</Button>
              : <Button variant="secondary" onClick={download}><Download size={16} />Скачать .doc</Button>}
            <Button variant="ghost" onClick={() => setStep(3)}>Изменить данные</Button>
          </div>
          {saved && (
            <Card className="no-print" label={`Договор #${saved.id}`} title={<span className="row gap-sm">PDF сохранён <ContractStatusBadge c={saved} /></span>}
              actions={<Link className="btn btn-sm btn-ghost" to={`?id=${saved.id}`}>Открыть договор</Link>}>
              {saved.pdf_stale && <div className="alert alert-warn" style={{ marginBottom: 8 }}>Данные изменены после генерации: нажмите «Обновить PDF», иначе в PDF старый текст.</div>}
              <ContractActions c={saved} onChange={setContract} />
            </Card>
          )}
          <div className="muted no-print" style={{ fontSize: 12 }}>Текст договора можно поправить прямо здесь: правка уйдёт в PDF{saved ? ' (нажмите «Обновить PDF»)' : ''}, в печать и в .doc.</div>
          <div className="contract-paper-wrap"><ContractDocument doc={doc} editable /></div>
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

/** /contracts: the list (active / history); ?id= one contract; ?new=1 (or ?lead_id= / ?deal_id=) the wizard, ?edit= the
 *  wizard on a stored draft / generated contract (autosaved; «Обновить PDF» regenerates it). */
// Optional check of a party on the CEC voter list (11-13). voters.cec.gov.ge sends X-Frame-Options: SAMEORIGIN (no iframe),
// ignores URL params (no pre-fill) and sits behind Cloudflare Turnstile + an SMS code, so we only open it in a popup
// and hand the agent the values to paste: personal number, surname, and the agent's own phone (the code goes to whoever checks).
const CEC_URL = 'https://voters.cec.gov.ge/';
function CecCheck({ pid, fullName }: { pid: string; fullName: string }) {
  const toast = useToast();
  const { user } = useAuth();
  const [open, setOpen] = useState(false);
  const id = pid.replace(/\D/g, '');
  const surname = fullName.trim().split(/\s+/).pop() ?? '';  // ponytail: "Имя Фамилия" order assumed
  const phone = (user?.phone ?? '').replace(/\D/g, '').slice(-9);  // the site wants 5XXXXXXXX without +995
  const copy = (v: string, what: string) => navigator.clipboard?.writeText(v).then(() => toast(`${what} скопирован${what === 'Фамилия' ? 'а' : ''}`), () => toast('Не удалось скопировать', true));
  const go = () => {
    void navigator.clipboard?.writeText(id).catch(() => undefined);  // before the popup takes focus
    const wa = insideTelegram() ? tgWebApp() : null;
    if (wa?.openLink) wa.openLink(CEC_URL);
    else window.open(CEC_URL, 'cec', 'popup,width=520,height=780');  // blocked → the link in the modal
    setOpen(true);
  };
  const row = (label: string, v: string, what: string) => (
    <div className="row gap-sm" style={{ justifyContent: 'space-between' }}>
      <span><span className="faint">{label}: </span><b>{v}</b></span>
      <Button variant="ghost" size="sm" onClick={() => copy(v, what)}><Copy size={14} />Копировать</Button>
    </div>
  );
  return (
    <>
      <Button variant="ghost" size="sm" disabled={!id} onClick={go} style={{ marginTop: 4 }}><UserCheck size={14} />Проверить в ЦИК</Button>
      <Modal open={open} onClose={() => setOpen(false)} title="Проверка в ЦИК">
        <div className="col gap-sm">
          <span>Личный номер скопирован — вставьте его на сайте ЦИК, затем фамилию и телефон, и введите код из SMS.</span>
          {row('Личный номер', id, 'Номер')}
          {surname && row('Фамилия', surname, 'Фамилия')}
          {phone ? row('Телефон', phone, 'Телефон') : <span className="faint">В профиле нет вашего телефона — укажите номер, на который придёт код.</span>}
          <a href={CEC_URL} target="_blank" rel="noreferrer" className="row gap-sm"><ExternalLink size={14} />Открыть voters.cec.gov.ge</a>
          <span className="faint" style={{ fontSize: 12 }}>Проверка необязательна. Фамилию на сайте вводят по-грузински.</span>
        </div>
      </Modal>
    </>
  );
}

function Contracts({ compact }: { compact?: boolean }) {
  const [params] = useSearchParams();
  const base = compact ? '/m/contracts' : '/contracts';
  const id = Number(params.get('id')) || 0;
  if (id) return <ContractView key={id} id={id} base={base} />;
  if (params.has('new') || params.has('lead_id') || params.has('deal_id') || params.has('edit')) return <ContractWizard compact={compact} />;
  return (
    <div className="col gap-lg">
      {!compact && <PageHead label="Генератор договоров" title="Договоры" />}
      <Link className="btn btn-lg" to={`${base}?new=1`}><Plus size={18} />Новый договор</Link>
      <ContractList base={base} />
    </div>
  );
}

export function ContractsPage() { return <Contracts />; }
export function MContracts() { return <Contracts compact />; }
