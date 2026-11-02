// Contract generator (old CRM tab «tab-contract»): deal types, languages, fields and legal text.
// Field set, labels, validation and derived values (one_month, deposit = price, pet deposit = 50 %) are ported
// 1:1 from docs/legacy/crm/main.js (cxCol / cxNext / cxBuildSum / cxOnem / cxSyncDep / cxTogglePets).
// The old CRM sent these fields to a Google Docs template on Drive; the template documents themselves were
// never part of the repository, so the wording below is a reconstruction around exactly those fields.
// To switch to the client's verbatim wording, edit only the TEXT object in this file.

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
  RU: { title: 'Русский', sub: 'Русская версия' },
  EN: { title: 'English', sub: 'Английская версия' },
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

// ---------------------------------------------------------------- document model

export interface DocParty { role: string; name: string; idLabel: string; id: string; dobLabel?: string; dob?: string }
export interface ContractDoc {
  lang: CxLang;
  title: string;
  placeLabel: string;
  place: string;
  dateLabel: string;
  date: string;
  preamble: string;
  sections: { h: string; items: string[] }[];
  signTitle: string;
  signLabel: string;
  parties: [DocParty, DocParty];
}

const usd = (v: string, lang: CxLang) => {
  const n = Number(String(v).replace(/[^\d.]/g, ''));
  const s = Number.isFinite(n) && v !== '' ? n.toLocaleString('ru-RU').replace(/ /g, ' ') : v;
  return lang === 'GEO' ? `${s} აშშ დოლარი` : lang === 'RU' ? `${s} долларов США` : `USD ${s}`;
};

/** Georgian case forms: «950 აშშ დოლარს» (dative), «950 აშშ დოლარის» (genitive). */
const geUsd = (v: string, c: 'dat' | 'gen') => usd(v, 'GEO').replace(/დოლარი$/, c === 'dat' ? 'დოლარს' : 'დოლარის');

function premises(d: CxData, lang: CxLang): string {
  const parts: string[] = [d.street_house];
  if (lang === 'GEO') {
    if (d.floor) parts.push(`სართული ${d.floor}`);
    if (d.apartment_number) parts.push(`ბინა №${d.apartment_number}`);
    return `ქ. თბილისი, ${parts.filter(Boolean).join(', ')}`;
  }
  if (lang === 'RU') {
    if (d.floor) parts.push(`этаж ${d.floor}`);
    if (d.apartment_number) parts.push(`кв. ${d.apartment_number}`);
    return `г. Тбилиси, ${parts.filter(Boolean).join(', ')}`;
  }
  if (d.floor) parts.push(`floor ${d.floor}`);
  if (d.apartment_number) parts.push(`apt. ${d.apartment_number}`);
  return `${parts.filter(Boolean).join(', ')}, Tbilisi, Georgia`;
}

type Builder = (d: CxData, comm: boolean) => Omit<ContractDoc, 'lang'>;

// ---------------------------------------------------------------- RU

const ruRent: Builder = (d, comm) => {
  const prem = premises(d, 'RU');
  const one = cxOnem(d.start_rent);
  const pets = d.with_pets
    ? [`Арендатору разрешается проживание в Помещении с домашними животными. Дополнительно к основному депозиту Арендатор вносит депозит за животных в размере ${usd(d.pet_deposit || '0', 'RU')}, который возвращается на тех же условиях, что и основной депозит, за вычетом расходов на устранение ущерба, причинённого животными.`]
    : ['Проживание в Помещении с домашними животными без письменного согласия Арендодателя не допускается.'];
  return {
    title: comm ? 'ДОГОВОР АРЕНДЫ НЕЖИЛОГО (КОММЕРЧЕСКОГО) ПОМЕЩЕНИЯ' : 'ДОГОВОР АРЕНДЫ ЖИЛОГО ПОМЕЩЕНИЯ',
    placeLabel: 'Место заключения', place: 'г. Тбилиси', dateLabel: 'Дата', date: cxFmt(d.timestamp),
    preamble: `${d.owner_full_name} (личный номер ${d.owner_personal_number}), именуемый(-ая) в дальнейшем «Арендодатель», с одной стороны, и ${d.client_full_name} (личный номер ${d.client_personal_number}), именуемый(-ая) в дальнейшем «Арендатор», с другой стороны, совместно именуемые «Стороны», заключили настоящий Договор о нижеследующем.`,
    sections: [
      { h: 'Предмет договора', items: [
        `Арендодатель передаёт, а Арендатор принимает во временное возмездное пользование ${comm ? 'нежилое помещение' : 'жилое помещение (квартиру)'}, расположенное по адресу: ${prem} (далее — «Помещение»), кадастровый код ${d.cadastral_code}, общей площадью ${d.apartment_area} м².`,
        comm ? 'Помещение предоставляется для осуществления предпринимательской деятельности Арендатора. Получение разрешений, необходимых для такой деятельности, является обязанностью Арендатора.' : 'Помещение предоставляется исключительно для проживания Арендатора и членов его семьи.',
        'Арендодатель подтверждает, что является собственником Помещения, Помещение не находится под арестом, в залоге или споре, и права третьих лиц не препятствуют его передаче в аренду.',
      ] },
      { h: 'Срок аренды', items: [
        `Срок аренды составляет ${d.rent_duration_months} мес.: с ${cxFmt(d.start_rent)} по ${cxFmt(d.end_rent)} включительно.`,
        'Помещение передаётся Арендатору в день начала срока аренды в состоянии, пригодном для использования по назначению, вместе с имуществом, находящимся в нём.',
      ] },
      { h: 'Арендная плата и порядок расчётов', items: [
        `Ежемесячная арендная плата составляет ${usd(d.price_usd, 'RU')}.`,
        `Арендная плата вносится ежемесячно, авансом, не позднее ${d.pay_day}-го числа каждого месяца.`,
        `Плата за первый месяц аренды (период с ${cxFmt(d.start_rent)} по ${one}) и депозит вносятся в день подписания настоящего Договора.`,
        comm ? 'Коммунальные услуги, эксплуатационные расходы и налоги, связанные с деятельностью Арендатора, оплачиваются Арендатором отдельно.' : 'Коммунальные платежи (электроэнергия, газ, вода, интернет, вывоз мусора и иные услуги по счётчикам) оплачиваются Арендатором отдельно.',
      ] },
      { h: 'Депозит', items: [
        `В обеспечение исполнения обязательств Арендатор передаёт Арендодателю депозит в размере ${usd(d.deposit_usd, 'RU')}.`,
        'Депозит возвращается Арендатору в течение 7 (семи) дней после окончания срока аренды и возврата Помещения, за вычетом задолженности по арендной и коммунальным платежам и расходов на устранение повреждений сверх нормального износа.',
        'Депозит не засчитывается в счёт арендной платы за последний месяц, если Стороны письменно не договорились об ином.',
        ...pets,
      ] },
      { h: 'Права и обязанности Арендодателя', items: [
        'Передать Помещение Арендатору в согласованный срок и в надлежащем состоянии.',
        'Не препятствовать Арендатору в пользовании Помещением. Посещение Помещения возможно по предварительному согласованию с Арендатором.',
        'Производить капитальный ремонт и устранять неисправности, возникшие не по вине Арендатора.',
      ] },
      { h: 'Права и обязанности Арендатора', items: [
        'Своевременно вносить арендную плату и оплачивать коммунальные услуги.',
        'Бережно относиться к Помещению и находящемуся в нём имуществу, соблюдать правила пожарной безопасности и права соседей.',
        'Не сдавать Помещение в субаренду и не передавать права по настоящему Договору третьим лицам без письменного согласия Арендодателя.',
        'Не производить перепланировку и ремонт без письменного согласия Арендодателя.',
        'По окончании срока аренды вернуть Помещение в том состоянии, в котором оно было получено, с учётом нормального износа.',
      ] },
      { h: 'Досрочное расторжение', items: [
        'Каждая из Сторон вправе досрочно расторгнуть Договор, письменно уведомив другую Сторону не менее чем за 30 (тридцать) дней.',
        'При досрочном расторжении по инициативе Арендатора без соблюдения срока уведомления депозит Арендатору не возвращается.',
        'Арендодатель вправе расторгнуть Договор в одностороннем порядке при просрочке арендной платы более 10 (десяти) дней или при существенном нарушении Арендатором условий Договора.',
      ] },
      { h: 'Заключительные положения', items: [
        'Отношения Сторон, не урегулированные настоящим Договором, регулируются законодательством Грузии, в том числе Гражданским кодексом Грузии.',
        'Споры разрешаются путём переговоров, а при недостижении согласия — в суде г. Тбилиси.',
        'Изменения и дополнения к Договору действительны, если они составлены в письменной форме и подписаны обеими Сторонами.',
        'Договор вступает в силу с момента подписания и составлен в двух экземплярах, имеющих равную юридическую силу, по одному для каждой из Сторон.',
      ] },
    ],
    signTitle: 'Подписи сторон', signLabel: 'Подпись',
    parties: [
      { role: 'Арендодатель', name: d.owner_full_name, idLabel: 'Личный номер', id: d.owner_personal_number },
      { role: 'Арендатор', name: d.client_full_name, idLabel: 'Личный номер', id: d.client_personal_number },
    ],
  };
};

const ruSale: Builder = (d) => ({
  title: 'ДОГОВОР КУПЛИ-ПРОДАЖИ НЕДВИЖИМОГО ИМУЩЕСТВА',
  placeLabel: 'Место заключения', place: 'г. Тбилиси', dateLabel: 'Дата', date: cxFmt(d.timestamp),
  preamble: `${d.owner_full_name} (личный номер ${d.owner_personal_number}, дата рождения ${cxFmt(d.owner_birth_day)}), именуемый(-ая) в дальнейшем «Продавец», с одной стороны, и ${d.client_full_name} (личный номер ${d.client_personal_number}, дата рождения ${cxFmt(d.client_birth_day)}), именуемый(-ая) в дальнейшем «Покупатель», с другой стороны, совместно именуемые «Стороны», заключили настоящий Договор о нижеследующем.`,
  sections: [
    { h: 'Предмет договора', items: [
      `Продавец продаёт, а Покупатель покупает недвижимое имущество, расположенное по адресу: ${premises(d, 'RU')} (далее — «Объект»), кадастровый код ${d.cadastral_code}, общей площадью ${d.apartment_area} м².`,
      'Право собственности Продавца на Объект зарегистрировано в Национальном агентстве публичного реестра Грузии.',
    ] },
    { h: 'Цена и порядок расчётов', items: [
      `Цена Объекта составляет ${usd(d.sale_price, 'RU')} и является окончательной.`,
      'Порядок и сроки оплаты цены Объекта определяются Сторонами; оплата подтверждается банковским документом или распиской Продавца.',
    ] },
    { h: 'Передача Объекта и переход права собственности', items: [
      `Продавец передаёт Объект Покупателю не позднее ${cxFmt(d.transfer_date)} свободным от проживающих лиц и имущества, если иное не согласовано Сторонами.`,
      'Право собственности на Объект переходит к Покупателю с момента государственной регистрации в Национальном агентстве публичного реестра Грузии.',
      'До передачи Объекта Продавец оплачивает все коммунальные и иные платежи, связанные с Объектом.',
    ] },
    { h: 'Гарантии Продавца', items: [
      'Продавец гарантирует, что Объект не продан, не подарен, не заложен, не находится под арестом или в споре и свободен от прав третьих лиц.',
      'В Объекте нет зарегистрированных или проживающих лиц, сохраняющих право пользования им после перехода права собственности.',
    ] },
    { h: 'Ответственность Сторон', items: [
      'За неисполнение или ненадлежащее исполнение обязательств Стороны несут ответственность в соответствии с законодательством Грузии.',
      'Расходы, связанные с регистрацией перехода права собственности, Стороны несут в порядке, согласованном между ними.',
    ] },
    { h: 'Заключительные положения', items: [
      'Отношения Сторон, не урегулированные настоящим Договором, регулируются Гражданским кодексом Грузии.',
      'Споры разрешаются путём переговоров, а при недостижении согласия — в суде г. Тбилиси.',
      'Договор вступает в силу с момента подписания и составлен в двух экземплярах, имеющих равную юридическую силу, по одному для каждой из Сторон.',
    ] },
  ],
  signTitle: 'Подписи сторон', signLabel: 'Подпись',
  parties: [
    { role: 'Продавец', name: d.owner_full_name, idLabel: 'Личный номер', id: d.owner_personal_number, dobLabel: 'Дата рождения', dob: cxFmt(d.owner_birth_day) },
    { role: 'Покупатель', name: d.client_full_name, idLabel: 'Личный номер', id: d.client_personal_number, dobLabel: 'Дата рождения', dob: cxFmt(d.client_birth_day) },
  ],
});

// ---------------------------------------------------------------- EN

const enRent: Builder = (d, comm) => {
  const one = cxOnem(d.start_rent);
  const pets = d.with_pets
    ? [`The Tenant is allowed to keep pets in the Premises. In addition to the main deposit, the Tenant pays a pet deposit of ${usd(d.pet_deposit || '0', 'EN')}, refundable on the same terms as the main deposit, less the cost of repairing any damage caused by the pets.`]
    : ['Keeping pets in the Premises is not allowed without the Landlord\'s written consent.'];
  return {
    title: comm ? 'COMMERCIAL LEASE AGREEMENT' : 'RESIDENTIAL LEASE AGREEMENT',
    placeLabel: 'Place', place: 'Tbilisi, Georgia', dateLabel: 'Date', date: cxFmt(d.timestamp),
    preamble: `${d.owner_full_name} (personal ID ${d.owner_personal_number}), hereinafter the "Landlord", and ${d.client_full_name} (personal ID ${d.client_personal_number}), hereinafter the "Tenant", jointly the "Parties", have concluded this Agreement as follows.`,
    sections: [
      { h: 'Subject of the Agreement', items: [
        `The Landlord leases to the Tenant for temporary use against payment the ${comm ? 'non-residential premises' : 'residential apartment'} located at ${premises(d, 'EN')} (the "Premises"), cadastral code ${d.cadastral_code}, total area ${d.apartment_area} sq. m.`,
        comm ? 'The Premises are leased for the Tenant\'s business activity. Obtaining any permits required for such activity is the Tenant\'s responsibility.' : 'The Premises are leased solely for residential use by the Tenant and the Tenant\'s family.',
        'The Landlord confirms being the owner of the Premises and that the Premises are free from any seizure, mortgage, dispute or third-party rights preventing the lease.',
      ] },
      { h: 'Lease Term', items: [
        `The lease term is ${d.rent_duration_months} month(s): from ${cxFmt(d.start_rent)} to ${cxFmt(d.end_rent)} inclusive.`,
        'The Premises are handed over on the first day of the term in a condition fit for use, together with the property inside.',
      ] },
      { h: 'Rent and Payments', items: [
        `The monthly rent is ${usd(d.price_usd, 'EN')}.`,
        `The rent is paid monthly in advance, no later than day ${d.pay_day} of each month.`,
        `The rent for the first month (the period from ${cxFmt(d.start_rent)} to ${one}) and the deposit are paid on the day this Agreement is signed.`,
        comm ? 'Utilities, service charges and taxes related to the Tenant\'s business are paid by the Tenant separately.' : 'Utilities (electricity, gas, water, internet, waste collection and other metered services) are paid by the Tenant separately.',
      ] },
      { h: 'Deposit', items: [
        `As security for the Tenant's obligations, the Tenant pays the Landlord a deposit of ${usd(d.deposit_usd, 'EN')}.`,
        'The deposit is returned within 7 (seven) days after the end of the term and the return of the Premises, less any unpaid rent or utilities and the cost of repairing damage beyond normal wear and tear.',
        'The deposit may not be used as rent for the last month unless the Parties agree otherwise in writing.',
        ...pets,
      ] },
      { h: 'Rights and Obligations of the Landlord', items: [
        'To hand over the Premises on time and in proper condition.',
        'Not to interfere with the Tenant\'s use of the Premises. Visits are possible by prior arrangement with the Tenant.',
        'To carry out major repairs and fix defects not caused by the Tenant.',
      ] },
      { h: 'Rights and Obligations of the Tenant', items: [
        'To pay the rent and utilities on time.',
        'To take good care of the Premises and the property inside, and to observe fire safety rules and the rights of neighbours.',
        'Not to sublet the Premises or assign rights under this Agreement without the Landlord\'s written consent.',
        'Not to carry out alterations or renovation without the Landlord\'s written consent.',
        'To return the Premises at the end of the term in the condition received, normal wear and tear excepted.',
      ] },
      { h: 'Early Termination', items: [
        'Either Party may terminate this Agreement early by giving the other Party at least 30 (thirty) days\' written notice.',
        'If the Tenant terminates early without the required notice, the deposit is not refunded.',
        'The Landlord may terminate unilaterally if the rent is more than 10 (ten) days overdue or if the Tenant materially breaches this Agreement.',
      ] },
      { h: 'Final Provisions', items: [
        'Matters not covered by this Agreement are governed by the laws of Georgia, including the Civil Code of Georgia.',
        'Disputes are settled by negotiation and, failing that, by the courts of Tbilisi.',
        'Amendments are valid only if made in writing and signed by both Parties.',
        'This Agreement enters into force upon signature and is made in two copies of equal legal force, one for each Party.',
      ] },
    ],
    signTitle: 'Signatures', signLabel: 'Signature',
    parties: [
      { role: 'Landlord', name: d.owner_full_name, idLabel: 'Personal ID', id: d.owner_personal_number },
      { role: 'Tenant', name: d.client_full_name, idLabel: 'Personal ID', id: d.client_personal_number },
    ],
  };
};

const enSale: Builder = (d) => ({
  title: 'REAL ESTATE SALE AND PURCHASE AGREEMENT',
  placeLabel: 'Place', place: 'Tbilisi, Georgia', dateLabel: 'Date', date: cxFmt(d.timestamp),
  preamble: `${d.owner_full_name} (personal ID ${d.owner_personal_number}, date of birth ${cxFmt(d.owner_birth_day)}), hereinafter the "Seller", and ${d.client_full_name} (personal ID ${d.client_personal_number}, date of birth ${cxFmt(d.client_birth_day)}), hereinafter the "Buyer", jointly the "Parties", have concluded this Agreement as follows.`,
  sections: [
    { h: 'Subject of the Agreement', items: [
      `The Seller sells and the Buyer buys the real estate located at ${premises(d, 'EN')} (the "Property"), cadastral code ${d.cadastral_code}, total area ${d.apartment_area} sq. m.`,
      'The Seller\'s ownership of the Property is registered with the National Agency of Public Registry of Georgia.',
    ] },
    { h: 'Price and Payment', items: [
      `The price of the Property is ${usd(d.sale_price, 'EN')} and is final.`,
      'The payment schedule is agreed by the Parties; payment is evidenced by a bank document or the Seller\'s receipt.',
    ] },
    { h: 'Handover and Transfer of Ownership', items: [
      `The Seller hands over the Property to the Buyer no later than ${cxFmt(d.transfer_date)}, vacant of occupants and belongings unless the Parties agree otherwise.`,
      'Ownership passes to the Buyer upon registration with the National Agency of Public Registry of Georgia.',
      'Until handover, the Seller pays all utility and other charges related to the Property.',
    ] },
    { h: 'Seller\'s Warranties', items: [
      'The Seller warrants that the Property has not been sold, donated or mortgaged, is not under seizure or dispute, and is free from third-party rights.',
      'No persons registered or residing in the Property retain a right to use it after the transfer of ownership.',
    ] },
    { h: 'Liability', items: [
      'The Parties are liable for non-performance or improper performance under the laws of Georgia.',
      'Registration costs are borne by the Parties as agreed between them.',
    ] },
    { h: 'Final Provisions', items: [
      'Matters not covered by this Agreement are governed by the Civil Code of Georgia.',
      'Disputes are settled by negotiation and, failing that, by the courts of Tbilisi.',
      'This Agreement enters into force upon signature and is made in two copies of equal legal force, one for each Party.',
    ] },
  ],
  signTitle: 'Signatures', signLabel: 'Signature',
  parties: [
    { role: 'Seller', name: d.owner_full_name, idLabel: 'Personal ID', id: d.owner_personal_number, dobLabel: 'Date of birth', dob: cxFmt(d.owner_birth_day) },
    { role: 'Buyer', name: d.client_full_name, idLabel: 'Personal ID', id: d.client_personal_number, dobLabel: 'Date of birth', dob: cxFmt(d.client_birth_day) },
  ],
});

// ---------------------------------------------------------------- GEO

const geRent: Builder = (d, comm) => {
  const one = cxOnem(d.start_rent);
  const pets = d.with_pets
    ? [`დამქირავებელს ნება ეძლევა ფართში იცხოვროს შინაურ ცხოველთან ერთად. ძირითადი დეპოზიტის გარდა, დამქირავებელი იხდის შინაური ცხოველის დეპოზიტს ${geUsd(d.pet_deposit || '0', 'gen')} ოდენობით, რომელიც ბრუნდება ძირითადი დეპოზიტის პირობებით, ცხოველის მიერ მიყენებული ზიანის აღმოფხვრის ხარჯების გამოკლებით.`]
    : ['ფართში შინაური ცხოველის ყოფნა გამქირავებლის წერილობითი თანხმობის გარეშე დაუშვებელია.'];
  return {
    title: comm ? 'არასაცხოვრებელი (კომერციული) ფართის ქირავნობის ხელშეკრულება' : 'საცხოვრებელი ფართის ქირავნობის ხელშეკრულება',
    placeLabel: 'ადგილი', place: 'ქ. თბილისი', dateLabel: 'თარიღი', date: cxFmt(d.timestamp),
    preamble: `ერთი მხრივ, ${d.owner_full_name} (პირადი ნომერი ${d.owner_personal_number}), შემდგომში „გამქირავებელი“, და მეორე მხრივ, ${d.client_full_name} (პირადი ნომერი ${d.client_personal_number}), შემდგომში „დამქირავებელი“, ერთად „მხარეები“, ვდებთ წინამდებარე ხელშეკრულებას შემდეგზე:`,
    sections: [
      { h: 'ხელშეკრულების საგანი', items: [
        `გამქირავებელი დროებით სასყიდლიან სარგებლობაში გადასცემს, ხოლო დამქირავებელი იღებს ${comm ? 'არასაცხოვრებელ ფართს' : 'საცხოვრებელ ფართს (ბინას)'}, მდებარეს მისამართზე: ${premises(d, 'GEO')} (შემდგომში „ფართი“), საკადასტრო კოდი ${d.cadastral_code}, საერთო ფართი ${d.apartment_area} კვ.მ.`,
        comm ? 'ფართი გადაეცემა დამქირავებელს სამეწარმეო საქმიანობისთვის. ამ საქმიანობისთვის საჭირო ნებართვების მიღება დამქირავებლის ვალდებულებაა.' : 'ფართი გადაეცემა მხოლოდ დამქირავებლისა და მისი ოჯახის წევრების საცხოვრებლად.',
        'გამქირავებელი ადასტურებს, რომ არის ფართის მესაკუთრე, ფართი არ არის დაყადაღებული, იპოთეკით დატვირთული ან სადავო და მესამე პირთა უფლებები ხელს არ უშლის მის გაქირავებას.',
      ] },
      { h: 'ქირავნობის ვადა', items: [
        `ქირავნობის ვადაა ${d.rent_duration_months} თვე: ${cxFmt(d.start_rent)}-დან ${cxFmt(d.end_rent)}-ის ჩათვლით.`,
        'ფართი დამქირავებელს გადაეცემა ვადის პირველ დღეს, დანიშნულებისამებრ სარგებლობისთვის ვარგის მდგომარეობაში, მასში არსებულ ქონებასთან ერთად.',
      ] },
      { h: 'ქირა და ანგარიშსწორების წესი', items: [
        `ყოველთვიური ქირა შეადგენს ${geUsd(d.price_usd, 'dat')}.`,
        `ქირა იხდება ყოველთვიურად, წინასწარ, არაუგვიანეს ყოველი თვის ${d.pay_day} რიცხვისა.`,
        `პირველი თვის ქირა (პერიოდი ${cxFmt(d.start_rent)}-დან ${one}-მდე) და დეპოზიტი იხდება ხელშეკრულების ხელმოწერის დღეს.`,
        comm ? 'კომუნალური მომსახურების, ექსპლუატაციის ხარჯები და დამქირავებლის საქმიანობასთან დაკავშირებული გადასახადები დამქირავებელს ცალკე ეკისრება.' : 'კომუნალური გადასახადები (ელექტროენერგია, გაზი, წყალი, ინტერნეტი, დასუფთავება და მრიცხველით აღრიცხული სხვა მომსახურება) დამქირავებელს ცალკე ეკისრება.',
      ] },
      { h: 'დეპოზიტი', items: [
        `ვალდებულებების შესრულების უზრუნველსაყოფად დამქირავებელი გამქირავებელს გადასცემს დეპოზიტს ${geUsd(d.deposit_usd, 'gen')} ოდენობით.`,
        'დეპოზიტი დამქირავებელს უბრუნდება ქირავნობის ვადის დასრულებიდან და ფართის დაბრუნებიდან 7 (შვიდი) დღის განმავლობაში, გადაუხდელი ქირის, კომუნალური გადასახადებისა და ბუნებრივ ცვეთაზე მეტი დაზიანების აღმოფხვრის ხარჯების გამოკლებით.',
        'დეპოზიტი არ ჩაითვლება ბოლო თვის ქირის ანგარიშში, თუ მხარეები წერილობით სხვა რამეზე არ შეთანხმდნენ.',
        ...pets,
      ] },
      { h: 'გამქირავებლის უფლებები და მოვალეობები', items: [
        'დამქირავებელს დროულად და სათანადო მდგომარეობაში გადასცეს ფართი.',
        'ხელი არ შეუშალოს დამქირავებელს ფართით სარგებლობაში. ფართის მონახულება შესაძლებელია დამქირავებელთან წინასწარი შეთანხმებით.',
        'ჩაატაროს კაპიტალური რემონტი და აღმოფხვრას დამქირავებლის ბრალეულობის გარეშე წარმოშობილი დაზიანებები.',
      ] },
      { h: 'დამქირავებლის უფლებები და მოვალეობები', items: [
        'დროულად გადაიხადოს ქირა და კომუნალური გადასახადები.',
        'გაუფრთხილდეს ფართს და მასში არსებულ ქონებას, დაიცვას სახანძრო უსაფრთხოების წესები და მეზობლების უფლებები.',
        'გამქირავებლის წერილობითი თანხმობის გარეშე არ გადასცეს ფართი ქვექირავნობით და არ დაუთმოს ამ ხელშეკრულებით მინიჭებული უფლებები მესამე პირს.',
        'გამქირავებლის წერილობითი თანხმობის გარეშე არ განახორციელოს გადაგეგმარება ან რემონტი.',
        'ვადის დასრულებისას დააბრუნოს ფართი მიღებისდროინდელ მდგომარეობაში, ბუნებრივი ცვეთის გათვალისწინებით.',
      ] },
      { h: 'ხელშეკრულების ვადამდე შეწყვეტა', items: [
        'თითოეულ მხარეს შეუძლია ვადამდე შეწყვიტოს ხელშეკრულება მეორე მხარის წერილობითი გაფრთხილებით არანაკლებ 30 (ოცდაათი) დღით ადრე.',
        'თუ დამქირავებელი ხელშეკრულებას ვადამდე წყვეტს გაფრთხილების ვადის დაუცველად, დეპოზიტი არ უბრუნდება.',
        'გამქირავებელს უფლება აქვს ცალმხრივად შეწყვიტოს ხელშეკრულება, თუ ქირის გადახდა 10 (ათი) დღეზე მეტით დაგვიანდა ან დამქირავებელმა არსებითად დაარღვია ხელშეკრულების პირობები.',
      ] },
      { h: 'დასკვნითი დებულებები', items: [
        'ამ ხელშეკრულებით მოუწესრიგებელი ურთიერთობები რეგულირდება საქართველოს კანონმდებლობით, მათ შორის საქართველოს სამოქალაქო კოდექსით.',
        'დავები წყდება მოლაპარაკებით, ხოლო შეთანხმების მიუღწევლობის შემთხვევაში — თბილისის სასამართლოში.',
        'ხელშეკრულებაში ცვლილებები ძალაშია, თუ შედგენილია წერილობით და ხელმოწერილია ორივე მხარის მიერ.',
        'ხელშეკრულება ძალაში შედის ხელმოწერისთანავე და შედგენილია ორ თანაბარი იურიდიული ძალის მქონე ეგზემპლარად, თითო ეგზემპლარი თითოეული მხარისთვის.',
      ] },
    ],
    signTitle: 'მხარეთა ხელმოწერები', signLabel: 'ხელმოწერა',
    parties: [
      { role: 'გამქირავებელი', name: d.owner_full_name, idLabel: 'პირადი ნომერი', id: d.owner_personal_number },
      { role: 'დამქირავებელი', name: d.client_full_name, idLabel: 'პირადი ნომერი', id: d.client_personal_number },
    ],
  };
};

const geSale: Builder = (d) => ({
  title: 'უძრავი ქონების ნასყიდობის ხელშეკრულება',
  placeLabel: 'ადგილი', place: 'ქ. თბილისი', dateLabel: 'თარიღი', date: cxFmt(d.timestamp),
  preamble: `ერთი მხრივ, ${d.owner_full_name} (პირადი ნომერი ${d.owner_personal_number}, დაბადების თარიღი ${cxFmt(d.owner_birth_day)}), შემდგომში „გამყიდველი“, და მეორე მხრივ, ${d.client_full_name} (პირადი ნომერი ${d.client_personal_number}, დაბადების თარიღი ${cxFmt(d.client_birth_day)}), შემდგომში „მყიდველი“, ერთად „მხარეები“, ვდებთ წინამდებარე ხელშეკრულებას შემდეგზე:`,
  sections: [
    { h: 'ხელშეკრულების საგანი', items: [
      `გამყიდველი ყიდის, ხოლო მყიდველი ყიდულობს უძრავ ქონებას, მდებარეს მისამართზე: ${premises(d, 'GEO')} (შემდგომში „ობიექტი“), საკადასტრო კოდი ${d.cadastral_code}, საერთო ფართი ${d.apartment_area} კვ.მ.`,
      'გამყიდველის საკუთრების უფლება ობიექტზე რეგისტრირებულია საჯარო რეესტრის ეროვნულ სააგენტოში.',
    ] },
    { h: 'ფასი და ანგარიშსწორების წესი', items: [
      `ობიექტის ფასი შეადგენს ${geUsd(d.sale_price, 'dat')} და არის საბოლოო.`,
      'გადახდის წესი და ვადები განისაზღვრება მხარეთა შეთანხმებით; გადახდა დასტურდება საბანკო დოკუმენტით ან გამყიდველის ხელწერილით.',
    ] },
    { h: 'ობიექტის გადაცემა და საკუთრების უფლების გადასვლა', items: [
      `გამყიდველი მყიდველს გადასცემს ობიექტს არაუგვიანეს ${cxFmt(d.transfer_date)}-ისა, მცხოვრებთა და ნივთებისგან თავისუფალს, თუ მხარეები სხვა რამეზე არ შეთანხმდნენ.`,
      'საკუთრების უფლება მყიდველზე გადადის საჯარო რეესტრის ეროვნულ სააგენტოში რეგისტრაციის მომენტიდან.',
      'ობიექტის გადაცემამდე ობიექტთან დაკავშირებულ კომუნალურ და სხვა გადასახადებს იხდის გამყიდველი.',
    ] },
    { h: 'გამყიდველის გარანტიები', items: [
      'გამყიდველი იძლევა გარანტიას, რომ ობიექტი არ არის გაყიდული, ნაჩუქარი, იპოთეკით დატვირთული, დაყადაღებული ან სადავო და თავისუფალია მესამე პირთა უფლებებისგან.',
      'ობიექტში არ არიან რეგისტრირებული ან მცხოვრები პირები, რომლებიც საკუთრების გადასვლის შემდეგ ინარჩუნებენ სარგებლობის უფლებას.',
    ] },
    { h: 'მხარეთა პასუხისმგებლობა', items: [
      'ვალდებულებების შეუსრულებლობის ან არაჯეროვანი შესრულებისთვის მხარეები პასუხს აგებენ საქართველოს კანონმდებლობით დადგენილი წესით.',
      'საკუთრების უფლების რეგისტრაციის ხარჯებს მხარეები გაიღებენ ურთიერთშეთანხმებით.',
    ] },
    { h: 'დასკვნითი დებულებები', items: [
      'ამ ხელშეკრულებით მოუწესრიგებელი ურთიერთობები რეგულირდება საქართველოს სამოქალაქო კოდექსით.',
      'დავები წყდება მოლაპარაკებით, ხოლო შეთანხმების მიუღწევლობის შემთხვევაში — თბილისის სასამართლოში.',
      'ხელშეკრულება ძალაში შედის ხელმოწერისთანავე და შედგენილია ორ თანაბარი იურიდიული ძალის მქონე ეგზემპლარად, თითო ეგზემპლარი თითოეული მხარისთვის.',
    ] },
  ],
  signTitle: 'მხარეთა ხელმოწერები', signLabel: 'ხელმოწერა',
  parties: [
    { role: 'გამყიდველი', name: d.owner_full_name, idLabel: 'პირადი ნომერი', id: d.owner_personal_number, dobLabel: 'დაბადების თარიღი', dob: cxFmt(d.owner_birth_day) },
    { role: 'მყიდველი', name: d.client_full_name, idLabel: 'პირადი ნომერი', id: d.client_personal_number, dobLabel: 'დაბადების თარიღი', dob: cxFmt(d.client_birth_day) },
  ],
});

const TEXT: Record<CxLang, { rent: Builder; sale: Builder }> = {
  RU: { rent: ruRent, sale: ruSale },
  EN: { rent: enRent, sale: enSale },
  GEO: { rent: geRent, sale: geSale },
};

export function buildContract(type: CxType, lang: CxLang, d: CxData): ContractDoc {
  const t = TEXT[lang];
  const doc = type === 'sale' ? t.sale(d, false) : t.rent(d, type === 'comm');
  return { lang, ...doc };
}
