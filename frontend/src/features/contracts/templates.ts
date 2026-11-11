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
  head.push(['cadastral_code', 'Кадастровый код'], ['street_house', 'Адрес'], ['floor', 'Этаж'], ['apartment_number', 'Квартира №'], ['apartment_area', 'Площадь, м²']);
  if (sale) return [...head, ['sale_price', 'Цена продажи, USD'], ['transfer_date', 'Дата передачи']];
  const rent: [keyof CxData | 'one_month', string][] = [['start_rent', 'Начало аренды'], ['end_rent', 'Окончание аренды'], ['rent_duration_months', 'Срок, мес.'],
    ['price_usd', 'Цена в месяц, USD'], ['deposit_usd', 'Депозит, USD'], ['pay_day', 'День оплаты'], ['one_month', 'Первый платёж за период до']];
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
/** Start date + 1 month, clamped to month end (cxOnem). */
export function cxOnem(iso: string): string {
  if (!iso) return '';
  const p = iso.split('-');
  if (p.length !== 3) return '';
  const d = new Date(+p[0], +p[1] - 1, +p[2]);
  const o = d.getDate();
  d.setMonth(d.getMonth() + 1);
  if (d.getDate() !== o) d.setDate(0);
  return `${String(d.getDate()).padStart(2, '0')}.${String(d.getMonth() + 1).padStart(2, '0')}.${d.getFullYear()}`;
}
/** Whole months between two ISO dates (helper for the duration field). */
export function monthsBetween(a: string, b: string): string {
  if (!a || !b) return '';
  const x = new Date(a), y = new Date(b);
  if (Number.isNaN(x.getTime()) || Number.isNaN(y.getTime()) || y <= x) return '';
  let m = (y.getFullYear() - x.getFullYear()) * 12 + (y.getMonth() - x.getMonth());
  if (y.getDate() < x.getDate()) m -= 1;
  return m > 0 ? String(m) : '';
}
/** Start + N months → ISO (for auto end date). */
export function addMonthsIso(iso: string, n: number): string {
  if (!iso || !n) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const day = d.getDate();
  d.setMonth(d.getMonth() + n);
  if (d.getDate() !== day) d.setDate(0);
  return d.toISOString().slice(0, 10);
}


// ---------------------------------------------------------------- David's drafts (data in templates-david.ts)

/** Paragraph ("**bold**" marks bold runs) | aligned paragraph | table (cell = blocks, or {s: colspan, c: blocks}). */
export type DraftBlock = string | { p: string; a?: 'c' | 'r' } | { t: DraftCell[][] };
export type DraftCell = DraftBlock[] | { s: number; c: DraftBlock[] };
export interface DraftDoc { header: string[]; footer: string[]; body: DraftBlock[] }
export type DraftKey = 'rent_GEO' | 'rent_RU' | 'rent_EN' | 'pet_GEO' | 'pet_RU' | 'pet_EN' | 'comm_GEO' | 'comm_RU' | 'sale_GEO';
export const DRAFT_NAMES: Record<DraftKey, string> = {
  rent_GEO: 'DRAFT GEO 2024', rent_RU: 'DRAFT (GEO+RUS) 2024', rent_EN: 'DRAFT (GEO+ENG) 2024',
  pet_GEO: 'Pet DRAFT GEO 2024', pet_RU: 'Pet DRAFT (GEO+RUS) 2024', pet_EN: 'Pet DRAFT (GEO+ENG) 2024',
  comm_GEO: 'DRAFT Commercial (GEO) 2024', comm_RU: 'DRAFT Commercial (GEO+RUS) 2024', sale_GEO: 'DRAFT Sale (GEO) 2024',
};

/** Languages David has a draft for: no Commercial GEO+ENG, Sale is Georgian only. */
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

export function buildContract(drafts: Record<DraftKey, DraftDoc>, type: CxType, lang: CxLang, d: CxData): ContractDoc {
  const key = draftKey(type, lang, type === 'rent' && d.with_pets);
  const v = draftValues(d);
  const f = (s: string) => s.replace(PH, (m, k: string) => (k in v ? v[k] : m));
  const src = drafts[key];
  return {
    lang: key.endsWith('_GEO') ? 'GEO' : lang, key, title: DRAFT_NAMES[key],
    header: src.header.map(f), footer: src.footer.map(f), body: src.body.map((b) => fillBlock(b, f)),
  };
}
