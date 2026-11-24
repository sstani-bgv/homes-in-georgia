// /contracts and /m/contracts — contract generator (port of the old CRM tab «tab-contract»).
// Output: «Сохранить PDF» (the server prints this HTML with headless Chrome → stored contract, features/contracts/
// ContractCard), the printable A4 preview (window.print + the same print CSS) and a .doc file for Word.
import { useEffect, useMemo, useRef, useState } from 'react';
import { cadastralError, cadastralHint, cadastralInput } from '../lib/cadastral';
import { useSearchParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Building2, Copy, Download, ExternalLink, FilePen, FileText, Home, KeyRound, Plus, Printer, RotateCcw, Save, Search, Send, UserCheck } from 'lucide-react';
import { Link } from 'react-router-dom';
import { api } from '../api';
import { itemsOf } from '../api/client';
import { atLeast, type Contract, type Deal, type LeadContract } from '../api/types';
import { ContractActions, ContractList, ContractStatusBadge, ContractView } from '../features/contracts/ContractCard';
import { insideTelegram, tgWebApp, useAuth } from '../auth/AuthContext';
import { errText, useToast } from '../components/toast';
import { Badge, Button, Card, Field, Input, Modal, PageHead, Select, Switch, Tabs, Textarea } from '../components/ui';
import { useSettledId } from '../components/RegistryApps';
import { useDebounced } from '../features/deals/DealForm';
import { OwnerLookup, parseRegistryText, useKnownObjects, type ExtractFields, type KnownObject } from '../features/contracts/OwnerLookup';
import { CONTRACT_STAGES, LEAD_STATUS, todayStr } from '../lib/format';
import { t, lx, LOCALE } from '../lib/i18n';
import {
  CX_LANGS, CX_TYPES, DATE_FIELDS, EMPTY, addMonthsIso, autoPaths, buildContract, clauseEdited, clauseLang, markAuto, pairClause, cxFmt, cxOnem, editClause, langsFor, monthsBetween, requiredFields, reviewLabels,
  type ClauseOp, type ContractDoc, type CxEdits, type CxData, type CxLang, type CxType, type DraftBlock, type DraftCell, type DraftDoc, type DraftKey, ENABLED_TYPES } from '../features/contracts/templates';

const STEPS = [t('Тип'), t('Язык'), t('Данные'), t('Проверка'), t('Документ')];
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
function para(text: string, a?: 'c' | 'r', k?: string): string {
  const dk = k ? ` data-k="${k}"` : '';  // the clause's path (templates.ts editSection): a click opens its editor
  if (!text) return `<div class="cd-gap"${dk}></div>`;
  let open = false;
  const parts = text.split(/ {8,}/).map((x) => {
    const y = (open ? '**' : '') + x;
    open = (y.match(/\*\*/g) || []).length % 2 === 1;
    return y;
  }).filter((x) => x.replace(/\*\*/g, '').trim());
  const body = parts.length > 1 ? `<span class="cd-spread">${parts.map((x) => `<span>${inline(x)}</span>`).join('')}</span>` : inline(parts[0] ?? '');
  return `<p class="cd-p${a ? ` cd-${a}` : ''}"${dk}>${body}</p>`;
}

function blocks(bs: DraftBlock[]): string {
  return bs.map((b) => {
    if (typeof b === 'string') return para(b);
    if ('p' in b) return para(b.p, b.a, b.k);
    // the bilingual drafts are one two-column table (Georgian | Russian/English); others are bordered grids
    const cols = b.t.every((r) => r.length === 2);
    const cell = (c: DraftCell) => (Array.isArray(c) ? `<td>${blocks(c)}</td>` : `<td colspan="${c.s}">${blocks(c.c)}</td>`);
    return `<table class="${cols ? 'cd-cols' : 'cd-grid'}">${b.t.map((r) => `<tr>${r.map(cell).join('')}</tr>`).join('')}</table>`;
  }).join('');
}

/** David's Google Docs, as they print (File → Page setup + the text size of each draft, measured on their PDF export):
 *  margins, font size, the round watermark's size (pt; 0 = none). Pets / commercial follow the rent draft of the language. */
// m: page margins [top, right, bottom, left] in pt — the body's box; the header line sits at 39–57 pt from the top and the
// initials footer starts at 786 pt (both inside the margins), as in the originals. ka: the line height of the Georgian
// text — Google lays FreeSerif lines out taller than Chrome's «normal»; these values give the originals' page breaks
const PAGE: Record<string, { m: [number, number, number, number]; pt: number; wm: number; ka: number }> = {
  GEO: { m: [72, 72, 85, 72], pt: 9.5, wm: 407, ka: 1.36 },
  RU: { m: [62, 36, 60, 36], pt: 9, wm: 471, ka: 1.45 },
  EN: { m: [62, 36, 60, 36], pt: 9, wm: 471, ka: 1.45 },
  sale_GEO: { m: [62, 42.5, 60, 85], pt: 12, wm: 0, ka: 1.36 },
};
const pageOf = (doc: ContractDoc) => PAGE[doc.key] || PAGE[doc.key.split('_')[1]] || PAGE.RU;
/** The originals: Georgia; Georgian letters (Georgia has none) fall back to FreeSerif, as in Google's own PDF. */
const DOC_FONT = `Georgia,'FreeSerif','Noto Serif Georgian','DejaVu Serif',serif`;

const DOC_CSS = `.contract-doc .cd-p{margin:0;text-align:justify;white-space:pre-line}.contract-doc .cd-c{text-align:center}
.contract-doc .cd-r{text-align:right}.contract-doc .cd-gap{height:1.15em}.contract-doc .cd-spread{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap}
.contract-doc{position:relative;font-family:${DOC_FONT};line-height:normal;background:url(/brand/contract-watermark.png) center 80px/78% auto repeat-y}
.contract-doc .cd-wm{display:none}.contract-doc .cd-head .cd-p{display:inline;font-weight:700;text-decoration:underline;color:#111}
.contract-doc .cd-flag{height:13px;margin-left:6px;vertical-align:-1px}.contract-doc .cd-head{margin-bottom:10px}
.contract-doc .cd-hf{font-size:8.5pt;color:#444}.contract-doc .cd-hf .cd-p{text-align:left}.contract-doc .cd-foot{margin-top:18px}
.contract-doc table{width:100%;border-collapse:collapse;margin:0}.contract-doc .cd-cols{table-layout:fixed;width:calc(100% + 10.8pt);margin-left:-5.4pt}
.contract-doc .cd-cols td{width:50%;vertical-align:top;padding:0 5.4pt}.contract-doc .cd-grid td{border:1pt solid #000;padding:0 5.4pt;vertical-align:top}
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
  const pg = pageOf(doc), [mt, , mb] = pg.m;
  const top = `vertical-align:bottom;padding-bottom:${Math.max(0, mt - 57)}pt`;  // the header line ends at 57 pt
  const bottom = `vertical-align:top;padding-top:${Math.max(0, 786 - (842 - mb))}pt`;  // the footer starts at 786 pt
  const str = (x: string) => `"${x.replace(/\*\*/g, '').replace(/ {8,}/g, '        ').replace(/["\\]/g, '\\$&')}"`;
  const head = doc.header.length ? `@top-left{content:${str(doc.header.join(' '))} url(/brand/ge-flag.svg);font:700 10pt Oswald,'Arial Narrow',Montserrat,Arial,sans-serif;text-decoration:underline;${top}}` : '';
  const foot = doc.footer.length ? `@bottom-center{content:${str(doc.footer.join(' '))};font:9pt ${DOC_FONT};color:#000;white-space:pre;${bottom}}` : '';
  const wm = pg.wm ? `.contract-doc .cd-wm{display:block;position:fixed;left:50%;top:50%;width:${pg.wm}pt;height:${pg.wm}pt;transform:translate(-50%,-50%);z-index:-1}` : '';
  return `@media print{@page{size:A4;margin:${pg.m.map((x) => `${x}pt`).join(' ')};${head}@top-right{content:counter(page);font:9pt Georgia,serif;${top}}${foot}}
.contract-doc{font-size:${pg.pt}pt;background:none}.contract-doc .cd-cols td:first-child,.contract-doc .cd-body>.cd-p{line-height:${pg.ka}}.contract-doc .cd-head,.contract-doc .cd-foot{display:none}${wm}}`;
}

export function ContractDocument({ doc, onClause }: { doc: ContractDoc; onClause?: (k: string) => void }) {
  return (
    <article className="contract-doc" lang="ka">
      <style>{DOC_CSS + pageCss(doc) + (onClause ? EDIT_CSS : '')}</style>
      {/* 11-18 (David): a click on a clause opens its editor; the change is stored with the contract (data._edits) */}
      <div className="cd-body" onClick={onClause && ((e) => {
        const k = (e.target as HTMLElement).closest<HTMLElement>('[data-k]')?.dataset.k;
        if (k) onClause(k);
      })} dangerouslySetInnerHTML={{ __html: docInner(doc) }} />
    </article>
  );
}
const EDIT_CSS = `@media screen{.contract-doc [data-k]{cursor:pointer;border-radius:3px}.contract-doc [data-k]:hover{background:rgba(255,200,0,.18);outline:1px dashed rgba(0,0,0,.25)}}`;

/** The text of the paragraph at path k (as filled in), or null. */
function clauseText(bs: DraftBlock[], k: string): string | null {
  for (const b of bs) {
    if (typeof b === 'string') continue;
    if ('p' in b) { if (b.k === k) return b.p; continue; }
    for (const r of b.t) for (const c of r) {
      const x = clauseText(Array.isArray(c) ? c : c.c, k);
      if (x !== null) return x;
    }
  }
  return null;
}

/** Standalone HTML (opens in Word / LibreOffice as an editable document, like the old «Редактировать DOC»). */
// a phone can't open the .doc (HTML for Word): iOS «This file cannot be previewed» (Irakli 11-23); the PDF is there
const PHONE = /iPhone|iPad|iPod|Android/i.test(navigator.userAgent);

function docHtml(doc: ContractDoc): string {
  const inner = docInner(doc);  // clause edits are in the doc itself (buildContract + data._edits)
  return `<!doctype html><html><head><meta charset="utf-8"><title>${esc(doc.title)}</title><style>body{font-family:${DOC_FONT};font-size:${pageOf(doc).pt}pt;line-height:normal}${DOC_CSS}</style></head><body><div class="contract-doc">${inner}</div></body></html>`;
}

/** The page the server prints to PDF: the edited text + the print CSS of «Печать» (header, page numbers, initials,
 *  watermark). Brand images stay /brand/… links: the server inlines them. */
function pdfHtml(doc: ContractDoc): string {
  const inner = docInner(doc);
  const base = `body{margin:0;background:#fff;color:#000}.contract-doc{font-size:${pageOf(doc).pt}pt}`;
  return `<!doctype html><html lang="ka"><head><meta charset="utf-8"><title>${esc(doc.title)}</title><style>${base}${DOC_CSS}${pageCss(doc)}</style></head><body><article class="contract-doc" lang="ka"><div class="cd-body">${inner}</div></article></body></html>`;
}

/** David's drafts are ~370 KB of text: loaded only when the contract page needs them (own chunk). */
function useDrafts() {
  const [drafts, setDrafts] = useState<Record<DraftKey, DraftDoc> | null>(null);
  useEffect(() => { import('../features/contracts/templates-david').then((m) => setDrafts(m.DRAFTS)); }, []);
  return drafts;
}

// ---------------------------------------------------------------- wizard

type EditSession = { id: number; key: string };
// randomUUID needs a secure context: the mock on a LAN http address gets a random string instead
const newSession = (id: number): EditSession => ({ id, key: globalThis.crypto?.randomUUID?.() ?? `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}` });

/** «Документы на подпись» (pages/SignDocs.tsx): any pasted document signed electronically — staff only. */
function SignDocsLink() {
  const { user } = useAuth();
  return atLeast(user, 'operator') ? <Link className="btn btn-secondary" to="/sign-docs"><FilePen size={16} />{t('Документы на подпись')}</Link> : null;
}

export function ContractWizard({ compact }: { compact?: boolean }) {
  const toast = useToast();
  const [params, setParams] = useSearchParams();
  const qc = useQueryClient();
  const dealId = Number(params.get('deal_id')) || 0;
  const leadId = Number(params.get('lead_id')) || 0;
  const propId = Number(params.get('property_id')) || 0;  // the object card's «Оформить договор аренды» / the picker (11-15)
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
  const [edits, setEdits] = useState<CxEdits>({});  // clause edits (templates.ts editClause), autosaved as data._edits
  const [clause, setClause] = useState<{ k: string; text: string; add?: boolean } | null>(null);  // the clause editor

  // ---- the contract lives in the deal: the form autosaves into it (contracts.autosave) and reopens from it
  const [contract, setContract] = useState<Contract | null>(null);  // the server's copy: status, PDF, versions
  const saved = contract?.has_pdf ? contract : null;
  // the editing session: one per form (reset / another lead or object / another contract in the URL). Its id is read
  // when a queued save actually runs, and answers of an older session never reach the form; its key makes the first
  // autosaves of a standalone draft idempotent (contracts.autosave data._key) while the id is not known yet
  const sess = useRef(newSession(editId));
  const restored = useRef(0);
  const touched = useRef(false);  // only the agent's input (or a lead prefill) saves, not opening the form
  const chain = useRef<Promise<unknown>>(Promise.resolve());  // autosaves one after another: one draft, no races
  const [autoState, setAutoState] = useState<{ at?: string; err?: string }>({});
  // the deal's open draft: «Оформить договор» again continues it instead of starting over
  const dealDraft = useQuery({ queryKey: ['contracts', 'draft-of', leadId, dealId, propId],
    queryFn: () => api.contracts.list({ status: 'draft', ...(leadId ? { lead_id: leadId } : dealId ? { deal_id: dealId } : { property_id: propId }) }),
    enabled: !editId && !!(leadId || dealId || propId) });
  const existing = itemsOf(dealDraft.data)[0];
  useEffect(() => {
    if (existing && !editId) setParams((sp) => { sp.set('edit', String(existing.id)); return sp; }, { replace: true });
  }, [existing?.id]);  // eslint-disable-line react-hooks/exhaustive-deps
  // the URL switched to another contract (or away from it) without the pickers / reset: a new session, so a save still
  // in flight lands in the old contract and the next one does not
  const linkKey = `${leadId}:${dealId}:${propId}`;
  const prevLink = useRef(linkKey);
  useEffect(() => {
    const moved = prevLink.current !== linkKey;
    prevLink.current = linkKey;
    if (!moved && editId === sess.current.id) return;  // our own ?edit= after the first save
    sess.current = newSession(editId); restored.current = 0; touched.current = false; setContract(null);
  }, [editId, linkKey]);  // eslint-disable-line react-hooks/exhaustive-deps
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
    setEdits(x._edits && typeof x._edits === 'object' ? x._edits as CxEdits : {});
    setDepTouched(!!x._dep); setEndTouched(!!x._end);
    setStep(c.has_pdf ? 3 : Math.min(5, Number(x._step) || (c.kind ? 2 : 1)));
    sess.current.id = c.id;
    setContract(c);
    touched.current = false;
    setPrefilled({ key: `contract:${c.id}`, text: c.has_pdf
      ? t('Договор #{id} · {status}. Поменяйте данные и нажмите «Обновить PDF»: договор перегенерируется, прошлая версия PDF сохранится.', { id: c.id, status: t(c.status_label) })
      : t('Черновик договора #{id} восстановлен — продолжайте с того же места.', { id: c.id }) });
  }
  const current = contract ?? stored.data;
  const locked = !!current && current.status !== 'draft';  // signed / closed: not edited
  const linkLead = leadId || contract?.lead_id || 0;
  const linkDeal = dealId || contract?.deal_id || 0;
  const linkProp = propId || contract?.property_id || 0;

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
    toast(t(key.startsWith('lead') ? 'Данные подставлены из заявки #{id}' : key.startsWith('object') ? 'Данные подставлены из объекта #{id}' : 'Данные подставлены из сделки #{id}', { id: key.split(':')[1] }));
  }
  function applyDeal(x: Deal, p?: Parameters<typeof fromDeal>[1]) {
    const r = fromDeal(x, p);
    apply(r.type, r.data, `deal:${x.id}`, `${t('Данные подставлены из сделки #{id}', { id: x.id })}${x.address ? ` · ${x.address}` : ''}.`);
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
      `${t('Данные подставлены из заявки #{id}', { id: x.lead_id })}${x.data.street_house ? ` · ${x.data.street_house}` : ''}${x.district ? `, ${x.district}` : ''}.`
      + who(t('Клиент'), x.client) + who(t('Собственник'), x.owner));
  }
  // ---- prefill from /contracts?property_id= (the object card, or the object picker below)
  const propC = useQuery({ queryKey: ['property-contract', propId], queryFn: () => api.properties.contract(propId), enabled: !!propId && !leadId && !dealId && fresh, retry: false });
  useEffect(() => {
    const x = propC.data;
    if (!x || prefilled?.key === `object:${x.property_id}`) return;
    apply(x.type, x.data as Partial<CxData>, `object:${x.property_id}`, `${t('Данные подставлены из объекта #{id}', { id: x.property_id })}${x.data.street_house ? ` · ${x.data.street_house}` : ''}.`
      + (x.owner.name || x.owner.phone ? ` ${t('Собственник')}: ${[x.owner.name, x.owner.phone].filter(Boolean).join(' · ')}.` : ''));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [propC.data]);
  const [objQ, setObjQ] = useState('');
  const objDq = useDebounced(objQ.trim());
  const objFound = useQuery({ queryKey: ['deal-search', objDq], queryFn: () => api.properties.dealSearch({ q: objDq }), enabled: !!objDq });
  function pickObject(id: number) {
    sess.current = newSession(0); restored.current = 0; touched.current = false; setContract(null); setObjQ('');
    setParams({ property_id: String(id) }, { replace: true });
  }
  const pickable = useQuery({ queryKey: ['leads', 'list', 'contract'], queryFn: () => api.leads.list({ status: CONTRACT_STAGES.join(',') }) });
  function pickLead(id: number) {
    sess.current = newSession(0); restored.current = 0; touched.current = false; setContract(null);
    setParams({ lead_id: String(id) }, { replace: true });
  }

  // ---- autosave: debounced, from a partly filled form; the wizard's step / sync flags go along (_step, _dep, _end)
  const payloadData = () => ({ ...d, _step: step, _dep: depTouched, _end: endTouched, ...(Object.keys(edits).length ? { _edits: edits } : {}) });
  useEffect(() => {
    if (!touched.current || locked) return;  // a standalone contract (no lead / deal) autosaves too (11-13)
    const t = setTimeout(() => {
      const s = sess.current;
      const body = { kind: type, lang, lead_id: linkLead || null, deal_id: linkDeal || null, property_id: linkProp || null, data: payloadData() };
      chain.current = chain.current.then(() => api.contracts.autosave(  // the id as it is now, after the saves before
        s.id ? { ...body, id: s.id } : { ...body, id: null, data: { ...body.data, _key: s.key } }).then((c) => {
        const first = !s.id;
        s.id = c.id;
        if (s !== sess.current) return;  // the form went on to another contract meanwhile
        restored.current = c.id;
        setContract(c);
        setAutoState({ at: new Date().toLocaleTimeString(LOCALE, { hour: '2-digit', minute: '2-digit' }) });
        if (first) {
          setParams((sp) => { sp.set('edit', String(c.id)); return sp; }, { replace: true });
          qc.invalidateQueries({ queryKey: ['contracts', 'list'] });
        }
      }, (e) => { if (s === sess.current) setAutoState({ err: errText(e) }); }));
    }, AUTOSAVE_MS);
    return () => clearTimeout(t);
  }, [type, lang, d, depTouched, endTouched, step, edits]);  // eslint-disable-line react-hooks/exhaustive-deps

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
  const ownerPid = useSettledId(String(d.owner_personal_number ?? ''));  // → the owner's objects: CRM records, then NAPR
  const known = useKnownObjects(ownerPid);
  // the owner's ID typed and the CRM already has his object: the cadastral code (and the empty address fields) fill in
  const applyKnown = (o: KnownObject) => setD((x) => ({
    ...x, cadastral_code: o.cadastral,
    street_house: x.street_house || o.address || '', floor: x.floor || o.floor || '',
    apartment_number: x.apartment_number || o.apartment_number || '', apartment_area: x.apartment_area || o.area || '',
  }));
  // «Стереть» (Irakli 11-13): a wrong object substituted — exactly the fields «Подставить» fills go back to empty
  const clearKnown = () => setD((x) => ({ ...x, cadastral_code: '', street_house: '', street_house_en: '', floor: '', apartment_number: '', apartment_area: '' }));
  // a registry extract / the pasted NAPR page: the code always, the other fields where still empty; says what it filled
  const applyExtract = (f: ExtractFields) => {
    const LABEL: Record<string, string> = { cadastral_code: t('код'), street_house: t('адрес'), street_house_en: t('адрес (ENG)'), floor: t('этаж'), apartment_number: t('квартира'),
      apartment_area: t('площадь'), owner_full_name: t('собственник'), owner_personal_number: t('личный номер') };
    // only the empty fields: what the agent typed stays (a different code is pointed out, not replaced)
    const put: Partial<CxData> = {};
    for (const k of Object.keys(LABEL) as (keyof ExtractFields & keyof CxData)[]) {
      const v = f[k];
      if (v && !String(d[k] ?? '').trim()) (put as Record<string, string>)[k] = v;
    }
    setD((x) => ({ ...x, ...put }));
    const names = Object.keys(put).map((k) => LABEL[k]);
    const typed = String(d.cadastral_code ?? '').trim();
    if (f.cadastral_code && typed && typed !== f.cadastral_code) toast(t('В выписке другой кадастровый код: {code} — проверьте', { code: f.cadastral_code }), true);
    else toast(names.length ? t('Из реестра: {list}', { list: names.join(', ') }) : t('Поля уже заполнены — из реестра ничего не изменено'));
    return names;
  };
  const [autoFor, setAutoFor] = useState('');
  useEffect(() => {
    const first = known.data?.[0];
    if (!first || autoFor === ownerPid || String(d.cadastral_code ?? '').trim()) return;
    setAutoFor(ownerPid);
    applyKnown(first);
    toast(t('Кадастровый код из базы CRM: {code} ({source})', { code: first.cadastral, source: first.source }));
  }, [known.data, ownerPid]);  // eslint-disable-line react-hooks/exhaustive-deps
  const drafts = useDrafts();
  const doc = useMemo(() => (drafts && type && lang ? buildContract(drafts, type, lang, d, edits) : null), [drafts, type, lang, d, edits]);
  function changeClause(op: ClauseOp) {
    if (!doc || !clause) return;
    if ('text' in op && op.text === clauseText(doc.body, clause.k)) { setClause(null); return; }  // unchanged: the fields stay live
    touched.current = true;
    const { key } = doc, k = clause.k;
    setEdits((e) => markAuto(editClause(e, key, k, op), key, [k], false));  // a manual edit: no longer «автоперевод»
    setClause(null);
    void mirrorClause(doc, k, op);
  }
  /** David 11-23: the agent writes Russian; the other column of a bilingual contract follows by autotranslation (and an
   *  English column gets English). The edit above is already kept; a failed translation only warns. */
  async function mirrorClause(doc: ContractDoc, k: string, op: ClauseOp) {
    const { key } = doc, pair = pairClause(doc, k);
    const [base, added] = k.split('+');
    const own = 'add' in op ? `${base}+${added === undefined ? 0 : Number(added) + 1}` : k;  // where the new text sits now
    if (!/^\d+\.[01]\./.test(k)) return;  // not the bilingual table: one language, nothing to do
    if (!pair) {
      if (!('del' in op || 'reset' in op)) toast(t('Второй столбец не изменён: не нашёл в нём этот пункт, поправьте его вручную'), true);
      return;
    }
    if ('del' in op || 'reset' in op) { setEdits((e) => markAuto(editClause(e, key, pair, op), key, [pair], false)); return; }
    const text = 'add' in op ? op.add : op.text;
    if (!text.trim()) return;
    const ownLang = clauseLang(doc, k), pairLang = clauseLang(doc, pair);
    const to = [...new Set([pairLang, ...(ownLang === 'RU' ? [] : [ownLang])])];  // the agent may type Russian in any column
    try {
      const tr = await api.contracts.translate(text, to);
      if (!tr[pairLang]) throw new Error(t('пустой ответ'));
      setEdits((e) => {
        let n = e;
        const list = n[`${key}:${pair.split('+')[0]}+`];
        const pairAdded = pair.split('+')[1];
        if ('add' in op) n = editClause(n, key, pair, { add: tr[pairLang] });
        else if (pairAdded !== undefined && !(Array.isArray(list) && list.length > Number(pairAdded))) n = editClause(n, key, pair.split('+')[0], { add: tr[pairLang] });
        else n = editClause(n, key, pair, { text: tr[pairLang] });
        const pairNow = 'add' in op ? `${pair.split('+')[0]}+${pairAdded === undefined ? 0 : Number(pairAdded) + 1}` : pair;
        const auto = [pairNow];
        if (tr[ownLang] && tr[ownLang] !== text) { n = editClause(n, key, own, { text: tr[ownLang] }); auto.push(own); }
        return markAuto(n, key, auto, true);
      });
      toast(t('Автоперевод: второй столбец обновлён, проверьте'));
    } catch (e) {
      toast(t('Автоперевод не удался, второй столбец не изменён: {err}', { err: errText(e) }), true);
    }
  }
  // David has no Commercial GEO+ENG and only a Georgian sale draft: drop a language the chosen type doesn't have
  useEffect(() => { if (type && lang && !langsFor(type).includes(lang)) setLang(''); }, [type, lang]);

  function next() {
    setErr('');
    if (step === 1) {
      if (!type) { setErr(t('Выберите тип договора')); return; }
      setStep(2); return;
    }
    if (step === 2) { if (!lang) { setErr(t('Выберите язык договора')); return; } setStep(3); return; }
    if (step === 3) {
      // «Далее» walks the sub-tabs first, validation only from the last one (11-13: it jumped straight to step 4)
      if (tab !== 'money') { goTab(tab === 'parties' ? 'property' : 'money'); return; }
      const miss = requiredFields(type as CxType).filter((k) => !String(d[k] ?? '').trim());
      if (miss.length) {
        setMissing(new Set(miss));
        setErr(t('Заполните все обязательные поля'));
        const parties: (keyof CxData)[] = ['timestamp', 'owner_full_name', 'owner_personal_number', 'owner_birth_day', 'client_full_name', 'client_personal_number', 'client_birth_day'];
        const property: (keyof CxData)[] = ['cadastral_code', 'street_house', 'apartment_area'];
        goTab(miss.some((k) => parties.includes(k)) ? 'parties' : miss.some((k) => property.includes(k)) ? 'property' : 'money');
        return;
      }
      if (cadastralError(d.cadastral_code)) { setErr(t('Проверьте кадастровый код')); goTab('property'); return; }
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
    sess.current = newSession(0); restored.current = 0; touched.current = false; setContract(null); setAutoState({});
    setType(''); setLang(''); setD({ ...EMPTY, timestamp: todayStr() }); setEdits({}); setDepTouched(false); setPetTouched(false);
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
        lead_id: linkLead || leadC.data?.lead_id || null, deal_id: linkDeal || leadC.data?.deal_id || null, property_id: linkProp || null,
        data: payloadData() };  // the wizard's CxData: the deal page prefills from the signed contract (contracts.data)
      const s = sess.current;
      await chain.current;  // a pending autosave lands first
      return { s, c: await (s.id ? api.contracts.update(s.id, b) : api.contracts.create(b)) };
    },
    onSuccess: ({ s, c }) => {
      const first = !s.id;
      s.id = c.id;
      if (s !== sess.current) { qc.invalidateQueries({ queryKey: ['contracts'] }); return; }  // another form by now
      restored.current = c.id;
      setContract(c);
      if (first) setParams((sp) => { sp.set('edit', String(c.id)); return sp; }, { replace: true });
      qc.invalidateQueries({ queryKey: ['contracts'] });
      toast(c.versions?.length ? t('PDF обновлён: договор #{id}, версия {n}', { id: c.id, n: c.versions.length + 1 }) : t('PDF сохранён: договор #{id}', { id: c.id }));
    },
    onError: (e) => toast(errText(e), true),
  });
  const sendTg = useMutation({
    mutationFn: () => api.contracts.send(`${fileBase}.doc`, docHtml(doc!)),
    onSuccess: () => toast(t('Договор отправлен вам в Telegram')),
    onError: (e) => toast(errText(e), true),
  });

  // ---- field helper
  // red only while still empty: the term fills the pay day and the dates itself, they must not stay red (11-13)
  const bad = (k: keyof CxData) => missing.has(k) && !String(d[k] ?? '').trim();
  // 11-19 (Irakli): the object's numbers open the digit keyboard and drop letters as typed; floor may be «-1», a flat «12/1»
  const digits: Partial<Record<keyof CxData, [mode: 'numeric' | 'decimal', clean: (v: string) => string]>> = {
    floor: ['numeric', (v) => v.replace(/(?!^-)\D/g, '')],
    apartment_number: ['numeric', (v) => v.replace(/[^\d/-]/g, '')],
    apartment_area: ['decimal', (v) => v.replace(/,/g, '.').replace(/[^\d.]/g, '').replace(/(\..*)\./g, '$1')],
  };
  const F = ({ k, label, type: t = 'text', placeholder }: { k: keyof CxData; label: string; type?: string; placeholder?: string }) => (
    <Field label={`${label}${req.has(k) ? ' *' : ''}`} hint={k === 'cadastral_code' ? cadastralHint(String(d[k] ?? '')) : undefined}>
      <Input type={t} value={String(d[k] ?? '')} placeholder={placeholder} inputMode={digits[k]?.[0]}
        aria-invalid={bad(k) || undefined} style={bad(k) ? { borderColor: 'var(--accent)' } : undefined}
        onChange={(e) => set(k, k === 'cadastral_code' ? cadastralInput(e) : digits[k] ? digits[k][1](e.target.value) : e.target.value)}
        onPaste={k === 'cadastral_code' ? (e) => {
          // the whole NAPR page pasted into the code field: the code is found in it (backend registry.code_from_text)
          const t = e.clipboardData.getData('text');
          if (t.length < 40) return;
          e.preventDefault();
          parseRegistryText(t).then(
            (r) => (Object.keys(r.fields).length ? applyExtract(r.fields) : toast(lx('В тексте не нашлось данных реестра', 'ტექსტში რეესტრის მონაცემები ვერ მოიძებნა', 'No registry data found in the text'), true)),
            (err) => toast(errText(err), true));
        } : undefined} />
    </Field>
  );

  if (locked && current) return (
    <div className="alert alert-info">
      {t('Договор #{id} · {status}: подписанный или закрытый договор не меняется.', { id: current.id, status: t(current.status_label) })}{' '}
      <Link to={`?id=${current.id}`}>{t('Открыть договор')}</Link>
    </div>
  );

  return (
    <div className="col gap-lg contract-wizard">
      <div className="no-print">
        {!compact && (
          <PageHead label={t('Генератор договоров')} title={t('Договоры')} actions={<div className="row row-wrap gap-sm">
            {linkProp > 0 && <Link className="btn btn-secondary" to={`/objects/${linkProp}`}><Home size={16} />{t('К объекту')}</Link>}
            <SignDocsLink />
            <Button variant="secondary" onClick={reset}><RotateCcw size={16} />{t('Новый договор')}</Button></div>} />
        )}
        {(contract || autoState.err) && (
          <div className="row row-wrap gap-sm" style={{ fontSize: 12, marginBottom: 6 }} aria-live="polite">
            {contract && <ContractStatusBadge c={contract} />}
            {contract && <span className="muted">{t('Договор #{id}', { id: contract.id })}{linkLead ? ` · ${t('заявка #{id}', { id: linkLead })}` : ''}{linkDeal ? ` · ${t('сделка #{id}', { id: linkDeal })}` : ''}</span>}
            {autoState.err ? <span style={{ color: 'var(--accent)' }}>{t('Черновик не сохранён: {err}', { err: autoState.err })}</span>
              : autoState.at && <span className="faint">{t('Сохранено в {at}', { at: autoState.at })}</span>}
          </div>
        )}
        <div className="stepper" style={{ marginBottom: 6 }}>
          {STEPS.map((s, i) => (
            <div key={s} className={`step ${i + 1 < step ? 'done' : i + 1 === step ? 'current' : ''}`}>
              <span className="dot">{i + 1}</span><span className="step-label">{s}</span>
            </div>
          ))}
        </div>
        {prefilled && <div className="alert alert-info mt">{prefilled.text} {t('Проверьте и дополните поля (личные номера сторон вводятся вручную).')}</div>}
        {deal.error && <div className="alert alert-warn mt">{t('Не удалось загрузить сделку #{id}: {err}', { id: dealId, err: errText(deal.error) })}</div>}
        {leadC.error && <div className="alert alert-warn mt">{t('Не удалось загрузить заявку #{id}: {err}', { id: leadId, err: errText(leadC.error) })}</div>}
        {propC.error && <div className="alert alert-warn mt">{t('Не удалось загрузить объект #{id}: {err}', { id: propId, err: errText(propC.error) })}</div>}
        {prefilled && step < 4 && !prefilled.key.startsWith('contract') && <Button size="sm" variant="ghost" className="no-print" onClick={() => setPrefilled(null)}>{t('Подставить из другой заявки или квартиры')}</Button>}
        {step < 4 && !prefilled && (
          <Card label={t('Сделка · необязательно')} title={t('Заявка или квартира договора')} className="mt">
            {itemsOf(pickable.data).length === 0 ? (
              <div className="muted" style={{ fontSize: 13 }}>{pickable.isLoading ? t('Загрузка…') : t('Нет заявок после просмотра: договор можно оформить и без заявки, привязать позже из карточки договора.')}</div>
            ) : (
              <Select aria-label={t('Заявка')} value={leadId || ''} onChange={(e) => e.target.value && pickLead(Number(e.target.value))} placeholder={t('Без заявки (можно привязать позже) — или выберите заявку')}
                options={itemsOf(pickable.data).map((l) => ({ value: l.id, label: `#${l.id} · ${l.listing?.address || l.listing?.title || t('объект')} · ${l.client?.name || t('клиент')} · ${LEAD_STATUS[l.status]}${l.agent?.name ? ` · ${l.agent.name}` : ''}` }))} />
            )}
            {/* «Новый договор → выбрать квартиру» (Irakli 11-15): the deal form's object search, the same prefill as the card */}
            <div className="input-icon mt">
              <Search size={16} />
              <input className="input" aria-label={t('Квартира')} placeholder={t('Или квартира: адрес, номер объекта, телефон собственника, кадастр')} value={objQ} onChange={(e) => setObjQ(e.target.value)} />
            </div>
            {!!objDq && objFound.data && (objFound.data.items.length === 0 ? <div className="muted" style={{ fontSize: 13 }}>{t('В базе не найдено')}</div> : objFound.data.items.map((o) => (
              <button key={o.id} type="button" className="list-row" disabled={!!o.blocked} style={{ textAlign: 'left', width: '100%', background: 'none', cursor: o.blocked ? 'not-allowed' : 'pointer' }} onClick={() => pickObject(o.id)}>
                <div className="grow">
                  <div style={{ fontWeight: 700 }}>#{o.id} · {o.address || t('Без адреса')}</div>
                  <div className="muted" style={{ fontSize: 12 }}>{[o.district, o.agent_tag, o.blocked].filter(Boolean).join(' · ')}</div>
                </div>
              </button>
            )))}
          </Card>
        )}
      </div>

      {step === 1 && (
        <Card label={t('Шаг 1')} title={t('Тип договора')} className="no-print">
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
        <Card label={t('Шаг 2')} title={t('Язык договора')} className="no-print">
          <div className="grid g3">
            {(Object.keys(CX_LANGS) as CxLang[]).map((l) => {
              const on = lang === l;
              const off = !!type && !langsFor(type).includes(l);
              return (
                <button key={l} type="button" className={`choice ${on ? 'on' : ''}`} disabled={off} style={off ? { opacity: 0.45, cursor: 'not-allowed' } : undefined}
                  onClick={() => { touched.current = true; setLang(l); setErr(''); }} aria-pressed={on}>
                  <span className="grow"><b lang={l === 'GEO' ? 'ka' : undefined}>{CX_LANGS[l].title}</b><small>{off ? t('Нет такого шаблона для этого типа договора') : CX_LANGS[l].sub}</small></span><Badge tone={on ? 'solid' : 'blue'}>{l}</Badge>
                </button>
              );
            })}
          </div>
        </Card>
      )}

      {step === 3 && type && (
        <Card label={`${t('Шаг 3')} · ${CX_TYPES[type].title} · ${lang}`} title={t('Данные договора')} className="no-print cx-data">
          <Tabs value={tab} onChange={setTab} tabs={[{ value: 'parties', label: t('Стороны') }, { value: 'property', label: t('Объект') }, { value: 'money', label: sale ? t('Цена') : t('Финансы') }]} />
          {tab === 'parties' && (
            <div className="form-grid">
              <div className="span2">{F({ k: 'timestamp', label: t('Дата договора'), type: 'date' })}</div>
              {F({ k: 'owner_full_name', label: sale ? t('Продавец (ФИО)') : t('Собственник (ФИО)'), placeholder: t('Полное имя') })}
              <div>{F({ k: 'owner_personal_number', label: sale ? t('Личный номер продавца') : t('Личный номер собственника'), placeholder: 'Personal ID' })}
                <CecCheck pid={String(d.owner_personal_number ?? '')} fullName={String(d.owner_full_name ?? '')} /></div>
              {sale && <div className="span2">{F({ k: 'owner_birth_day', label: t('Дата рождения продавца'), type: 'date' })}</div>}
              {F({ k: 'client_full_name', label: sale ? t('Покупатель (ФИО)') : t('Арендатор (ФИО)'), placeholder: t('Полное имя') })}
              <div>{F({ k: 'client_personal_number', label: sale ? t('Личный номер покупателя') : t('Личный номер арендатора'), placeholder: 'Personal ID' })}
                <CecCheck pid={String(d.client_personal_number ?? '')} fullName={String(d.client_full_name ?? '')} /></div>
              {sale && <div className="span2">{F({ k: 'client_birth_day', label: t('Дата рождения покупателя'), type: 'date' })}</div>}
            </div>
          )}
          {tab === 'property' && (
            <div className="col gap-lg">
              <div className="form-grid">
                <div className="span2">{F({ k: 'cadastral_code', label: t('Кадастровый код'), placeholder: '01.10.13.015.029.01.150' })}</div>
                <div className="span2">
                  <OwnerLookup personalId={ownerPid} current={String(d.cadastral_code ?? '')} onKnown={applyKnown} onClear={clearKnown} onFields={applyExtract}
                    extractUrl={d.extract_url} onExtract={(url, file) => setD((x) => ({ ...x, extract_url: url, extract_file: file }))}
                    onCode={(c) => { if (c !== d.cadastral_code) { set('cadastral_code', c); toast(t('Из реестра: {list}', { list: c })); } }} />
                </div>
                <div className="span2">{F({ k: 'street_house', label: t('Адрес'), placeholder: t('Улица, номер дома') })}</div>
                {/* 11-13: the RU / ENG contracts print this one; filled from the extract's Georgian, editable */}
                <div className="span2">{F({ k: 'street_house_en', label: t('Адрес (ENG)'), placeholder: t('Tabidze St, 43 — для русской и английской версии') })}</div>
                {F({ k: 'floor', label: t('Этаж'), placeholder: '2' })}
                {F({ k: 'apartment_number', label: t('Квартира №'), placeholder: t('Номер') })}
                <div className="span2">{F({ k: 'apartment_area', label: t('Площадь, м²'), placeholder: t('45 или 45.05') })}</div>
              </div>
            </div>
          )}
          {tab === 'money' && (sale ? (
            <div className="form-grid">
              {F({ k: 'sale_price', label: t('Цена продажи, USD'), type: 'number', placeholder: t('Итоговая цена') })}
              {F({ k: 'transfer_date', label: t('Дата передачи'), type: 'date' })}
            </div>
          ) : (
            <div className="col gap-lg">
              <div className="form-grid">
                {F({ k: 'rent_duration_months', label: t('Срок, месяцев'), type: 'number', placeholder: t('Количество месяцев') })}
                {F({ k: 'pay_day', label: t('День оплаты (1–31)'), type: 'number', placeholder: t('Число месяца') })}
                {F({ k: 'price_usd', label: t('Цена в месяц, USD'), type: 'number', placeholder: t('Ежемесячная плата') })}
                {F({ k: 'deposit_usd', label: t('Депозит, USD'), type: 'number', placeholder: t('Равен цене, можно изменить') })}
              </div>
              {type === 'rent' && <div className="outcome-card col">
                <Switch checked={d.with_pets} onChange={(v) => set('with_pets', v)} label={t('С животными')} />
                <span className="muted" style={{ fontSize: 12 }}>{t('Договор по шаблону Pet DRAFT (с депозитом за животных)')}</span>
                {d.with_pets && (
                  <Field label={t('Депозит за животных, USD')} hint={t('По умолчанию 50% основного депозита')}>
                    <Input type="number" value={d.pet_deposit} onChange={(e) => { setPetTouched(true); set('pet_deposit', e.target.value); }} />
                  </Field>
                )}
              </div>}
              <div className="form-grid">
                {F({ k: 'start_rent', label: t('Начало аренды'), type: 'date' })}
                {F({ k: 'end_rent', label: t('Окончание аренды'), type: 'date' })}
                {/* 11-18 (David): printed in clause 2.8 of every language; empty = the blank line */}
                <div className="span2">{F({ k: 'bank_account', label: t('Банковский счёт собственника'), placeholder: t('Банк, IBAN, получатель — для пункта 2.8') })}</div>
              </div>
              <div className="muted" style={{ fontSize: 13 }}>
                {d.start_rent ? `${t('Первый платёж за период {from} – {to}.', { from: cxFmt(d.start_rent), to: cxOnem(d.start_rent) })} ` : ''}
                {endTouched ? t('Окончание введено вручную.') : t('Окончание = начало + срок.')}
              </div>
            </div>
          ))}
        </Card>
      )}

      {step === 4 && type && (
        <Card label={t('Шаг 4')} title={t('Проверка данных')} className="no-print" actions={<Button size="sm" variant="ghost" onClick={() => setStep(3)}>{t('Изменить')}</Button>}>
          <dl className="kv">
            <dt>{t('Тип')}</dt><dd>{CX_TYPES[type].title}</dd>
            <dt>{t('Язык')}</dt><dd>{lang && CX_LANGS[lang].title}</dd>
            {reviewLabels(type).map(([k, label]) => {
              let v = k === 'one_month' ? cxOnem(d.start_rent) : String(d[k] ?? '');
              if (k !== 'one_month' && DATE_FIELDS.includes(k)) v = cxFmt(v);
              return <div key={k} style={{ display: 'contents' }}><dt>{label}</dt><dd>{v || '—'}</dd></div>;
            })}
            {type === 'rent' && <><dt>{t('С животными')}</dt><dd>{d.with_pets ? t('да, депозит {n} USD', { n: d.pet_deposit || 0 }) : t('нет')}</dd></>}
          </dl>
        </Card>
      )}

      {step === 5 && !doc && <div className="muted no-print">{t('Загрузка шаблона…')}</div>}
      {step === 5 && doc && (
        <>
          <div className="row row-wrap no-print">
            <Button loading={save.isPending} onClick={() => save.mutate()}><Save size={16} />{saved ? t('Обновить PDF') : t('Сохранить PDF')}</Button>
            {!tg && <Button variant="secondary" onClick={print}><Printer size={16} />{t('Печать')}</Button>}
            {tg ? <Button variant="secondary" loading={sendTg.isPending} onClick={() => sendTg.mutate()}><Send size={16} />{t('.doc в Telegram')}</Button>
              : !PHONE && <Button variant="secondary" onClick={download}><Download size={16} />{t('Скачать .doc')}</Button>}
            <Button variant="ghost" onClick={() => setStep(3)}>{t('Изменить данные')}</Button>
          </div>
          {saved && (
            <Card className="no-print" label={t('Договор #{id}', { id: saved.id })} title={<span className="row gap-sm">{t('PDF сохранён')} <ContractStatusBadge c={saved} /></span>}
              actions={<Link className="btn btn-sm btn-ghost" to={`?id=${saved.id}`}>{t('Открыть договор')}</Link>}>
              {saved.pdf_stale && <div className="alert alert-warn" style={{ marginBottom: 8 }}>{t('Данные изменены после генерации: нажмите «Обновить PDF», иначе в PDF старый текст.')}</div>}
              <ContractActions c={saved} onChange={setContract} />
            </Card>
          )}
          <div className="muted no-print" style={{ fontSize: 12 }}>{saved ? t('Нажмите на пункт договора, чтобы изменить, удалить его или добавить новый после него. Правки сохраняются в договоре и уйдут в PDF (нажмите «Обновить PDF»), в печать и в .doc.') : t('Нажмите на пункт договора, чтобы изменить, удалить его или добавить новый после него. Правки сохраняются в договоре и уйдут в PDF, в печать и в .doc.')}</div>
          <div className="contract-paper-wrap"><ContractDocument doc={doc} onClause={(k) => setClause({ k, text: clauseText(doc.body, k) ?? '' })} /></div>
          <Modal open={!!clause} onClose={() => setClause(null)} title={clause?.add ? t('Новый пункт') : t('Пункт договора')}>
            {clause && <div className="col gap-sm no-print">
              <span className="faint" style={{ fontSize: 12 }}>{CX_LANGS[clauseLang(doc, clause.k)].title.split(' + ').pop()} · {t('**текст** между звёздочками печатается жирным')}{autoPaths(edits, doc.key).includes(clause.k) && <> · <b>{t('автоперевод')}</b></>}</span>
              {/^\d+\.[01]\./.test(clause.k) && <span className="faint" style={{ fontSize: 12 }}>{t('Можно писать по-русски: второй столбец переведётся автоматически')}</span>}
              <Textarea rows={8} value={clause.text} autoFocus onChange={(e) => setClause({ ...clause, text: e.target.value })} />
              <div className="row row-wrap gap-sm">
                <Button onClick={() => changeClause(clause.add ? { add: clause.text } : { text: clause.text })}><Save size={16} />{t('Сохранить')}</Button>
                {!clause.add && <>
                  <Button variant="secondary" onClick={() => setClause({ ...clause, text: '', add: true })}><Plus size={16} />{t('Добавить пункт после')}</Button>
                  <Button variant="ghost" onClick={() => changeClause({ del: true })}>{t('Удалить пункт')}</Button>
                  {clauseEdited(edits, doc.key, clause.k) && !clause.k.includes('+') && <Button variant="ghost" onClick={() => changeClause({ reset: true })}><RotateCcw size={16} />{t('Вернуть текст шаблона')}</Button>}
                </>}
              </div>
            </div>}
          </Modal>
        </>
      )}

      {err && <div className="alert alert-warn no-print" role="alert">{err}</div>}

      {step < 5 && (
        <div className="row no-print" style={{ justifyContent: 'flex-end' }}>
          {step > 1 && <Button variant="secondary" onClick={back}>{t('Назад')}</Button>}
          <Button onClick={next}>{step === 4 ? <><FileText size={16} />{t('Создать договор')}</> : t('Далее')}</Button>
        </div>
      )}
      {step === 5 && compact && <Button variant="secondary" className="no-print" onClick={reset}><RotateCcw size={16} />{t('Новый договор')}</Button>}
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
  const copy = (v: string, what: string) => navigator.clipboard?.writeText(v).then(() => toast(t(`${what} скопирован${what === 'Фамилия' ? 'а' : ''}`)), () => toast(t('Не удалось скопировать'), true));
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
      <Button variant="ghost" size="sm" onClick={() => copy(v, what)}><Copy size={14} />{t('Копировать')}</Button>
    </div>
  );
  return (
    <>
      <Button variant="ghost" size="sm" disabled={!id} onClick={go} style={{ marginTop: 4 }}><UserCheck size={14} />{t('Проверить в ЦИК')}</Button>
      <Modal open={open} onClose={() => setOpen(false)} title={t('Проверка в ЦИК')}>
        <div className="col gap-sm">
          <span>{t('Личный номер скопирован — вставьте его на сайте ЦИК, затем фамилию и телефон, и введите код из SMS.')}</span>
          {row(t('Личный номер'), id, 'Номер')}
          {surname && row(t('Фамилия'), surname, 'Фамилия')}
          {phone ? row(t('Телефон'), phone, 'Телефон') : <span className="faint">{t('В профиле нет вашего телефона — укажите номер, на который придёт код.')}</span>}
          <a href={CEC_URL} target="_blank" rel="noreferrer" className="row gap-sm"><ExternalLink size={14} />{t('Открыть voters.cec.gov.ge')}</a>
          <span className="faint" style={{ fontSize: 12 }}>{t('Проверка необязательна. Фамилию на сайте вводят по-грузински.')}</span>
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
  if (params.has('new') || params.has('lead_id') || params.has('deal_id') || params.has('property_id') || params.has('edit')) return <ContractWizard compact={compact} />;
  return (
    <div className="col gap-lg">
      {!compact && <PageHead label={t('Генератор договоров')} title={t('Договоры')} actions={<SignDocsLink />} />}
      <Link className="btn btn-lg" to={`${base}?new=1`}><Plus size={18} />{t('Новый договор')}</Link>
      <ContractList base={base} />
    </div>
  );
}

export function ContractsPage() { return <Contracts />; }
export function MContracts() { return <Contracts compact />; }
