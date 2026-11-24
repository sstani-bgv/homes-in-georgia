// Contract generator (old CRM tab «tab-contract»): deal types, languages, fields and David's contract texts.
// Field set, labels, validation and derived values (one_month, deposit = price, pet deposit = 50 %) are ported
// 1:1 from the old CRM (cxCol / cxNext / cxSubmit / cxOnem / cxSyncDep / cxTogglePets).
// The legal text is David's 2024 Google Docs drafts (owner ddavitashvili@rentintbilisi.ge), copied verbatim into
// templates-david.ts (converted from the docs' HTML export). Drive ids:
//   rent  1Wopykf6-csDG0ZlmEk2CpF_EKQ0HO6MhxZgi0ui1MM0 (GEO), 10wfKLkSsZV6Dw8jbiX59CC8HFX_WW7RTpYfHRv3cyNU (GEO+RUS),
//         1kg6xq5CGIqxFn0jF-iYH_5Ba52ZuCs3Ueyu3fl__vw8 (GEO+ENG)
//   pets  16z3CxbY7QCuB_PUR2Clfu_4WR8ktGYFi814c3N36Tkg (GEO), 1GrfgitRIt8865ynXqyIllAXROv447Yep1BOLEGaLeRA (GEO+RUS),
//         16R2Y0Y8ru1g_qUDuJMzj78p-t5XZzAhZWWsyOMSykj8 (GEO+ENG)
//   comm  1ANp5k8Xrisom_psOzic438H8PuVS7E1E_7hceuQZzYE (GEO), 1Ao8C_qjP78wHgSNcOcq75bmqAXSN0xye8kkTkTO6i94 (GEO+RUS)
//   sale  1j4lT-RPhwqrg31cUgh5yH56xq6qBAHXnz1BBCU0XD64 (GEO)
// The old CRM sent the fields below to a Google Apps Script that filled [[field]] placeholders in those docs;
// buildContract() does the same here. RU / EN mean the bilingual GEO+RUS / GEO+ENG documents.

import { addMonths, isoDate, parseIsoDate, ruDate, wholeMonths } from '../../lib/dates';

export type CxType = 'rent' | 'comm' | 'sale';
export type CxLang = 'GEO' | 'RU' | 'EN';

export interface CxData {
  timestamp: string; // contract date, ISO yyyy-mm-dd
  owner_full_name: string;
  owner_personal_number: string;
  owner_birth_day: string;
  client_full_name: string;
  client_personal_number: string;
  client_birth_day: string;
  cadastral_code: string;
  street_house: string;
  street_house_en?: string; // 11-13: the address in English for the RU / ENG halves (registry.address_en), editable
  floor: string;
  apartment_number: string;
  apartment_area: string;
  start_rent: string;
  end_rent: string;
  rent_duration_months: string;
  price_usd: string;
  deposit_usd: string;
  pay_day: string;
  sale_price: string;
  transfer_date: string;
  with_pets: boolean;
  pet_deposit: string;
  bank_account?: string; // 11-18 (David): the landlord's bank details, printed in clause 2.8 ([[bank_account]])
  extract_url?: string;  // the registry extract's link the agent pasted (OwnerLookup ExtractLink)
  extract_file?: string; // its PDF saved by the server (GET /contracts/{id}/extract)
}

export const EMPTY: CxData = {
  timestamp: '', owner_full_name: '', owner_personal_number: '', owner_birth_day: '', client_full_name: '', client_personal_number: '',
  client_birth_day: '', cadastral_code: '', street_house: '', floor: '', apartment_number: '', apartment_area: '', start_rent: '',
  end_rent: '', rent_duration_months: '', price_usd: '', deposit_usd: '', pay_day: '', sale_price: '', transfer_date: '',
  with_pets: false, pet_deposit: '',
};

export const CX_TYPES: Record<CxType, { title: string; sub: string }> = {
  rent: { title: 'Аренда квартиры', sub: 'Долгосрочная аренда жилья' },
  comm: { title: 'Коммерческая аренда', sub: 'Офис, магазин, склад, ресторан' },
  sale: { title: 'Продажа', sub: 'Договор купли-продажи недвижимости' },
};
export const CX_LANGS: Record<CxLang, { title: string; sub: string }> = {
  GEO: { title: 'ქართული', sub: 'Грузинская версия' },
  RU: { title: 'ქართული + Русский', sub: 'Двуязычный: грузинский и русский' },
  EN: { title: 'ქართული + English', sub: 'Двуязычный: грузинский и английский' },
};

/** Required fields per type — same lists as the old cxNext(). */
export function requiredFields(type: CxType): (keyof CxData)[] {
  const base: (keyof CxData)[] = ['timestamp', 'owner_full_name', 'owner_personal_number', 'client_full_name', 'client_personal_number', 'cadastral_code', 'street_house', 'apartment_area'];
  return type === 'sale'
    ? [...base, 'sale_price', 'transfer_date', 'owner_birth_day', 'client_birth_day']
    : [...base, 'start_rent', 'end_rent', 'rent_duration_months', 'price_usd', 'deposit_usd', 'pay_day'];
}

/** Review labels (old CX_LR / CX_LS, in Russian like the rest of the CRM). */
export function reviewLabels(type: CxType): [keyof CxData | 'one_month', string][] {
  const sale = type === 'sale';
  const head: [keyof CxData | 'one_month', string][] = [
    ['timestamp', 'Дата договора'], ['owner_full_name', sale ? 'Продавец' : 'Собственник'], ['owner_personal_number', sale ? 'ID продавца' : 'ID собственника'],
  ];
  if (sale) head.push(['owner_birth_day', 'Дата рождения продавца']);
  head.push(['client_full_name', sale ? 'Покупатель' : 'Арендатор'], ['client_personal_number', sale ? 'ID покупателя' : 'ID арендатора']);
  if (sale) head.push(['client_birth_day', 'Дата рождения покупателя']);
  head.push(['cadastral_code', 'Кадастровый код'], ['street_house', 'Адрес'], ['street_house_en', 'Адрес (ENG)'], ['floor', 'Этаж'], ['apartment_number', 'Квартира №'], ['apartment_area', 'Площадь, м²']);
  if (sale) return [...head, ['sale_price', 'Цена продажи, USD'], ['transfer_date', 'Дата передачи']];
  const rent: [keyof CxData | 'one_month', string][] = [['start_rent', 'Начало аренды'], ['end_rent', 'Окончание аренды'], ['rent_duration_months', 'Срок, мес.'],
    ['price_usd', 'Цена в месяц, USD'], ['deposit_usd', 'Депозит, USD'], ['pay_day', 'День оплаты'], ['one_month', 'Первый платёж за период до'], ['bank_account', 'Банковский счёт собственника']];
  return [...head, ...rent];
}

export const DATE_FIELDS: (keyof CxData)[] = ['timestamp', 'start_rent', 'end_rent', 'transfer_date', 'owner_birth_day', 'client_birth_day'];

// ---------------------------------------------------------------- derived values (ported from main.js)

/** ISO → dd.mm.yyyy (cxFmt). */
export function cxFmt(iso: string): string {
  if (!iso) return '';
  const p = String(iso).slice(0, 10).split('-');
  return p.length === 3 ? `${p[2]}.${p[1]}.${p[0]}` : iso;
}
/** Start date + 1 month, clamped to month end (cxOnem). Calendar math in lib/dates (no timezone shift). */
export function cxOnem(iso: string): string {
  const a = iso ? parseIsoDate(iso) : null;
  return a ? ruDate(addMonths(a, 1)) : '';
}
/** Whole months between two ISO dates (helper for the duration field); the inverse of addMonthsIso. */
export function monthsBetween(a: string, b: string): string {
  const x = a && b ? parseIsoDate(a) : null, y = x ? parseIsoDate(b) : null;
  if (!x || !y || isoDate(y) <= isoDate(x)) return '';
  const m = wholeMonths(x, y);
  return m > 0 ? String(m) : '';
}
/** Start + N months → ISO (for auto end date), clamped to month end. */
export function addMonthsIso(iso: string, n: number): string {
  const a = iso && n ? parseIsoDate(iso) : null;
  return a ? isoDate(addMonths(a, n)) : '';
}


// ---------------------------------------------------------------- David's drafts (data in templates-david.ts)

/** Paragraph ("**bold**" marks bold runs) | aligned paragraph | table (cell = blocks, or {s: colspan, c: blocks}). */
export type DraftBlock = string | { p: string; a?: 'c' | 'r'; k?: string } | { t: DraftCell[][] };
export type DraftCell = DraftBlock[] | { s: number; c: DraftBlock[] };
export interface DraftDoc { header: string[]; footer: string[]; body: DraftBlock[] }
export type DraftKey = 'rent_GEO' | 'rent_RU' | 'rent_EN' | 'pet_GEO' | 'pet_RU' | 'pet_EN' | 'comm_GEO' | 'comm_RU' | 'sale_GEO';
export const DRAFT_NAMES: Record<DraftKey, string> = {
  rent_GEO: 'DRAFT GEO 2024', rent_RU: 'DRAFT (GEO+RUS) 2024', rent_EN: 'DRAFT (GEO+ENG) 2024',
  pet_GEO: 'Pet DRAFT GEO 2024', pet_RU: 'Pet DRAFT (GEO+RUS) 2024', pet_EN: 'Pet DRAFT (GEO+ENG) 2024',
  comm_GEO: 'DRAFT Commercial (GEO) 2024', comm_RU: 'DRAFT Commercial (GEO+RUS) 2024', sale_GEO: 'DRAFT Sale (GEO) 2024',
};

/** Languages David has a draft for: no Commercial GEO+ENG, Sale is Georgian only. */
/** Types the wizard offers. 2026-11-11: rent only (with or without pets); commercial and sale drafts are kept in
 * templates-david.ts but Irakli finishes them himself — to enable one, see docs/CONTRACTS.md. */
export const ENABLED_TYPES: CxType[] = ['rent'];

export function langsFor(type: CxType): CxLang[] {
  return type === 'sale' ? ['GEO'] : type === 'comm' ? ['GEO', 'RU'] : ['GEO', 'RU', 'EN'];
}

/** type × lang × pets → draft. with_pets only exists for residential rent. */
export function draftKey(type: CxType, lang: CxLang, pets: boolean): DraftKey {
  const l = langsFor(type).includes(lang) ? lang : 'GEO';
  if (type === 'sale') return 'sale_GEO';
  if (type === 'comm') return `comm_${l}` as DraftKey;
  return `${pets ? 'pet' : 'rent'}_${l}` as DraftKey;
}

export interface ContractDoc extends DraftDoc { lang: CxLang; key: DraftKey; title: string }

/** Values exactly as the old CRM sent them to the Apps Script: dates dd.mm.yyyy, everything else as typed. */
export function draftValues(d: CxData): Record<string, string> {
  const v: Record<string, string> = { one_month: cxOnem(d.start_rent) };
  for (const [k, x] of Object.entries(d)) if (typeof x === 'string') v[k] = DATE_FIELDS.includes(k as keyof CxData) ? cxFmt(x) : x;
  // 11-13: GEO prints [[street_house]] (Georgian); the Russian / English halves of the bilingual drafts print
  // [[street_house_en]] — the English line, or the address as typed when there is none (typed in English / Russian)
  v.street_house_en = d.street_house_en?.trim() || d.street_house;
  v.bank_account = d.bank_account?.trim() || '________________________________________';  // empty: the blank line, as before
  return v;
}

const PH = /(?:\[\[|\{\{)\s*([A-Za-z_]+)\s*(?:\]\]|\}\})/g;
/** Placeholder names a text uses ([[field]], and {{field}} in the GEO+ENG drafts). */
export const placeholders = (s: string) => [...s.matchAll(PH)].map((m) => m[1]);

function fillBlock(b: DraftBlock, f: (s: string) => string): DraftBlock {
  if (typeof b === 'string') return f(b);
  if ('t' in b) return { t: b.t.map((r) => r.map((c) => (Array.isArray(c) ? c.map((x) => fillBlock(x, f)) : { s: c.s, c: c.c.map((x) => fillBlock(x, f)) }))) };
  return { ...b, p: f(b.p) };
}

// ---------------------------------------------------------------- clause edits (David 11-18)
// The agent changes, removes or adds paragraphs of one contract without touching the drafts. Stored with the contract
// (data._edits), applied to the draft before the fields are filled, so «Обновить PDF» after a data change keeps them.
// Key `${draftKey}:${path}`: path = the paragraph's index in the body ("12"), or in a column of the bilingual table
// ("8.0.41" = body[8], Georgian column, paragraph 41; "8.1.41" = the Russian / English column). Value: the new text,
// null = removed. `${draftKey}:${path}+` = the paragraphs added after it (in order).
export type CxEdits = Record<string, string | null | string[]>;
export type ClauseOp = { text: string } | { del: true } | { add: string } | { reset: true };

const isCols = (b: DraftBlock): b is { t: DraftCell[][] } => typeof b === 'object' && 't' in b && b.t.length === 1 && b.t[0].length === 2;

function editSection(bs: DraftBlock[], pre: string, e: CxEdits, key: string): DraftBlock[] {
  const out: DraftBlock[] = [];
  bs.forEach((b, i) => {
    const k = pre + i;
    if (typeof b === 'string' || 'p' in b) {
      const x = e[`${key}:${k}`];
      if (x !== null) out.push({ p: typeof x === 'string' ? x : typeof b === 'string' ? b : b.p, a: typeof b === 'string' ? undefined : b.a, k });
      const added = e[`${key}:${k}+`];
      if (Array.isArray(added)) added.forEach((p, n) => out.push({ p, k: `${k}+${n}` }));
    } else if (isCols(b)) {
      out.push({ t: [b.t[0].map((c, ci) => (Array.isArray(c) ? editSection(c, `${k}.${ci}.`, e, key) : c))] });
    } else out.push(b);  // the signature grid: not a clause
  });
  return out;
}

/** One change of a paragraph (k = its path from the rendered doc, «12» or an added «12+0»). Pure: a new CxEdits. */
export function editClause(e: CxEdits, key: DraftKey, k: string, op: ClauseOp): CxEdits {
  const n = { ...e };
  const [base, added] = k.split('+');
  const listKey = `${key}:${base}+`;
  const list = Array.isArray(n[listKey]) ? [...(n[listKey] as string[])] : [];
  const at = added === undefined ? -1 : Number(added);  // -1: the template's own paragraph
  if ('add' in op) list.splice(at + 1, 0, op.add);
  else if (at >= 0) {
    if ('text' in op) list[at] = op.text;
    else list.splice(at, 1);  // del / reset of an added paragraph: gone
  } else if ('text' in op) n[`${key}:${base}`] = op.text;
  else if ('del' in op) n[`${key}:${base}`] = null;
  else delete n[`${key}:${base}`];
  if (list.length) n[listKey] = list; else delete n[listKey];
  return n;
}

// The bilingual columns don't share paragraph indices (rent_RU: 94 Georgian, 102 Russian paragraphs), so the pair of a
// paragraph is found by its clause number: the nearest numbered paragraph above it («1.4.», «2.1»), then the same count
// of non-empty paragraphs down in the other column. No match (an unnumbered preamble that doesn't line up) = null.
const CLAUSE_NO = /^[\s*„"«(]*(\d+(?:\.\d+)+|\d+(?=\.))/;
type Para = { p: string; k: string };

/** The other column's paragraph of k (with k's «+n» of an added one), or null: not in the bilingual table / no pair. */
export function pairClause(doc: ContractDoc, k: string): string | null {
  const m = /^(\d+)\.([01])\.(\d+)(\+\d+)?$/.exec(k);
  const tbl = m && doc.body[Number(m[1])];
  if (!m || !tbl || !isCols(tbl)) return null;
  const col = (ci: number) => ((tbl.t[0][ci] as DraftBlock[]).filter((b) => typeof b === 'object' && 'p' in b && b.k && !b.k.includes('+')) as Para[]);
  const own = col(Number(m[2])), other = col(1 - Number(m[2]));
  const at = own.findIndex((b) => b.k === `${m[1]}.${m[2]}.${m[3]}`);
  if (at < 0 || !own[at].p.trim()) return null;
  let a = at;
  while (a >= 0 && !CLAUSE_NO.test(own[a].p)) a--;
  let steps = own.slice(a + 1, at + 1).filter((b) => b.p.trim()).length;
  let j = a < 0 ? -1 : other.findIndex((b) => CLAUSE_NO.exec(b.p)?.[1] === CLAUSE_NO.exec(own[a].p)![1]);
  if (a >= 0 && j < 0) return null;
  while (steps > 0) {
    j++;
    if (j >= other.length || CLAUSE_NO.test(other[j].p)) return null;  // ran into the next clause: the columns differ here
    if (other[j].p.trim()) steps--;
  }
  return other[j].k + (m[4] ?? '');
}

/** The autotranslated paths (`${draftKey}:auto`, shown «автоперевод» in the clause editor). */
export const autoPaths = (e: CxEdits, key: DraftKey): string[] => (Array.isArray(e[`${key}:auto`]) ? e[`${key}:auto`] as string[] : []);
export function markAuto(e: CxEdits, key: DraftKey, paths: string[], on: boolean): CxEdits {
  const s = new Set(autoPaths(e, key));
  paths.forEach((p) => (on ? s.add(p) : s.delete(p)));
  const n = { ...e };
  if (s.size) n[`${key}:auto`] = [...s]; else delete n[`${key}:auto`];
  return n;
}

/** The paragraph's language in the document: the bilingual table's columns are Georgian | Russian / English. */
export function clauseLang(doc: ContractDoc, k: string): CxLang {
  const col = /^\d+\.(\d)\./.exec(k)?.[1];
  return col === '1' ? doc.lang : 'GEO';
}

/** Whether the paragraph is changed / added / the template's own (for «Вернуть как в шаблоне»). */
export function clauseEdited(e: CxEdits, key: DraftKey, k: string): boolean {
  return k.includes('+') || `${key}:${k}` in e;
}

export function buildContract(drafts: Record<DraftKey, DraftDoc>, type: CxType, lang: CxLang, d: CxData, edits: CxEdits = {}): ContractDoc {
  const key = draftKey(type, lang, type === 'rent' && d.with_pets);
  const v = draftValues(d);
  const f = (s: string) => s.replace(PH, (m, k: string) => (k in v ? v[k] : m));
  const src = drafts[key];
  return {
    lang: key.endsWith('_GEO') ? 'GEO' : lang, key, title: DRAFT_NAMES[key],
    header: src.header.map(f), footer: src.footer.map(f), body: editSection(src.body, '', edits, key).map((b) => fillBlock(b, f)),
  };
}
