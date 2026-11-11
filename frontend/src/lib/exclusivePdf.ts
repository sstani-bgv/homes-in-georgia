/* The owner's exclusivity confirmation (David's template, ru / ka / en) and its signed PDF, built in the owner's
 * browser like hig-sign (PlatformDGE/hig-sign assets/pdf.js): A4 pages drawn on a canvas (the browser draws Georgian and
 * Cyrillic itself), packed as JPEG pages into a PDF. No libraries. */

export type Lang = 'ru' | 'ka' | 'en';
export interface ConfirmData { address: string; cadastral_code: string; full_name: string; personal_id: string; phone: string; date: string }

// Georgian first: the owner's page opens in it and the switcher lists it first (Stanislav 2026-11-11)
export const LANG_LABEL: Record<Lang, string> = { ka: 'ქართული', ru: 'Русский', en: 'English' };

/** [title, lines]; a line starting with «• » is a bullet. */
export function confirmText(lang: Lang, d: ConfirmData): [string, string[]] {
  const v = (s: string) => s.trim() || '—';
  if (lang === 'ka') return ['ექსკლუზიურობის დადასტურება', [
    'გამარჯობა!',
    'მე ექსკლუზიურად ვთანამშრომლობ Rent in Tbilisi-სთან ქვემოთ მითითებულ უძრავ ქონებაზე:',
    `მისამართი: ${v(d.address)}`,
    `საკადასტრო კოდი: ${v(d.cadastral_code)}`,
    `მე, ${v(d.full_name)},`,
    `პირადი ნომერი: ${v(d.personal_id)},`,
    `საკონტაქტო ტელეფონი: ${v(d.phone)}, ვადასტურებ, რომ:`,
    '• ვანიჭებ ექსკლუზიურ უფლებას კომპანია Rent in Tbilisi-ს აღნიშნულ ობიექტზე მუშაობისთვის;',
    '• ვაძლევ ნებართვას Rent in Tbilisi-ის Telegram-არხში განთავსებული ფოტო და ვიდეო მასალების გამოყენებაზე;',
    '• ვითხოვ აღნიშნულ ობიექტზე არსებული ყველა აქტიური განცხადების წაშლას სხვა პლატფორმებიდან (SS.ge და სხვა).',
    'ვეთანხმები ზემოთ აღნიშნულ პირობებს.',
    `${v(d.full_name)}, ${d.date}`,
  ]];
  if (lang === 'en') return ['Exclusivity Confirmation', [
    'Hello,',
    'I cooperate exclusively with Rent in Tbilisi regarding the following real estate property:',
    `Address: ${v(d.address)}`,
    `Cadastral code: ${v(d.cadastral_code)}`,
    `I, ${v(d.full_name)},`,
    `personal ID number: ${v(d.personal_id)},`,
    `contact phone number: ${v(d.phone)}, hereby confirm that:`,
    '• I grant exclusive rights to Rent in Tbilisi to work with this property;',
    '• I authorize the use of photo and video materials published in the Rent in Tbilisi Telegram channel;',
    '• I request the removal of all active listings related to this property from other platforms (SS.ge and others).',
    'I agree to the above terms.',
    `${v(d.full_name)}, ${d.date}`,
  ]];
  return ['Подтверждение эксклюзивности', [
    'Здравствуйте!',
    'Я сотрудничаю эксклюзивно с Rent in Tbilisi по следующему объекту недвижимости:',
    `Адрес: ${v(d.address)}`,
    `Кадастровый код: ${v(d.cadastral_code)}`,
    `Я, ${v(d.full_name)},`,
    `личный номер: ${v(d.personal_id)},`,
    `контактный телефон: ${v(d.phone)}, подтверждаю, что:`,
    '• предоставляю эксклюзивное право компании Rent in Tbilisi работать с данным объектом;',
    '• разрешаю использовать фото- и видеоматериалы, размещённые в Telegram-канале Rent in Tbilisi;',
    '• прошу удалить все активные объявления по данному объекту на других платформах (SS.ge и др.).',
    'С условиями согласен(на).',
    `${v(d.full_name)}, ${d.date}`,
  ]];
}

export const tbilisiNow = () => new Intl.DateTimeFormat('ru-RU', { timeZone: 'Asia/Tbilisi', dateStyle: 'short', timeStyle: 'medium' }).format(new Date());

const W = 1240, H = 1754;             // A4 @150 dpi
const PT_W = 595.28, PT_H = 841.89;   // A4 in PDF points
const M = 90, TOP = 170, BOTTOM = H - 190;
const NAVY = '#1D3560', INK = '#1a1f2b', GREY = '#6b7280', LINE = '#c9d1e0';
const FONT = "Montserrat, 'Noto Sans Georgian', Arial, sans-serif";
const font = (size: number, bold?: boolean) => `${bold ? 700 : 400} ${size}px ${FONT}`;

type Item = { k: 'text'; s: string; x: number; y: number; size: number; bold?: boolean; color?: string }
  | { k: 'img'; x: number; y: number; w: number; h: number };

function wrap(ctx: CanvasRenderingContext2D, text: string, width: number, size: number, bold?: boolean): string[] {
  ctx.font = font(size, bold);
  const out: string[] = [];
  let line = '';
  for (const word of text.split(/\s+/).filter(Boolean)) {
    const next = line ? `${line} ${word}` : word;
    if (line && ctx.measureText(next).width > width) { out.push(line); line = word; } else line = next;
  }
  if (line) out.push(line);
  return out;
}

class Flow {
  pages: Item[][] = [[]];
  y = TOP;
  constructor(private ctx: CanvasRenderingContext2D) {}
  get page() { return this.pages[this.pages.length - 1]; }
  need(h: number) { if (this.y + h > BOTTOM) { this.pages.push([]); this.y = TOP; } }
  para(text: string, o: { size?: number; bold?: boolean; color?: string; indent?: number; gap?: number } = {}) {
    const size = o.size || 19, lh = Math.round(size * 1.45), indent = o.indent || 0;
    const bullet = text.startsWith('• ');
    const lines = wrap(this.ctx, bullet ? text.slice(2) : text, W - 2 * M - indent - (bullet ? 30 : 0), size, o.bold);
    lines.forEach((s, i) => {
      this.need(lh);
      if (bullet && i === 0) this.page.push({ k: 'text', s: '•', x: M + indent + 6, y: this.y + lh - 8, size, color: o.color });
      this.page.push({ k: 'text', s, x: M + indent + (bullet ? 30 : 0), y: this.y + lh - 8, size, bold: o.bold, color: o.color });
      this.y += lh;
    });
    this.y += o.gap ?? 6;
  }
}

function fit(ctx: CanvasRenderingContext2D, img: CanvasImageSource & { width: number; height: number }, x: number, y: number, w: number, h: number) {
  if (!img.width || !img.height) return;
  const r = Math.min(w / img.width, h / img.height);
  ctx.drawImage(img, x, y + (h - img.height * r) / 2, img.width * r, img.height * r);
}

function bytes(dataUrl: string) {
  const bin = atob(dataUrl.split(',')[1]);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

/** hig-sign makePdf: one JPEG image per A4 page. */
function makePdf(jpegs: Uint8Array[], title: string) {
  const enc = new TextEncoder(), parts: Uint8Array[] = [], offsets: number[] = [];
  let len = 0;
  const add = (x: string | Uint8Array) => { const b = typeof x === 'string' ? enc.encode(x) : x; parts.push(b); len += b.length; };
  const obj = (n: number, body: () => void) => { offsets[n] = len; add(`${n} 0 obj\n`); body(); add('\nendobj\n'); };
  const str = (s: string) => '<FEFF' + [...s].map((c) => c.charCodeAt(0).toString(16).padStart(4, '0')).join('').toUpperCase() + '>';
  const kids = jpegs.map((_, i) => `${4 + i * 3} 0 R`);
  add('%PDF-1.4\n%\xE2\xE3\xCF\xD3\n');
  obj(1, () => add('<< /Type /Catalog /Pages 2 0 R >>'));
  obj(2, () => add(`<< /Type /Pages /Count ${jpegs.length} /Kids [${kids.join(' ')}] >>`));
  obj(3, () => add(`<< /Title ${str(title)} /Producer ${str('Homes in Georgia CRM (hig-sign)')} >>`));
  jpegs.forEach((jpg, i) => {
    const p = 4 + i * 3, cs = `q ${PT_W} 0 0 ${PT_H} 0 0 cm /Im${i} Do Q`;
    obj(p, () => add(`<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${PT_W} ${PT_H}] /Resources << /XObject << /Im${i} ${p + 1} 0 R >> >> /Contents ${p + 2} 0 R >>`));
    obj(p + 1, () => { add(`<< /Type /XObject /Subtype /Image /Width ${W} /Height ${H} /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length ${jpg.length} >>\nstream\n`); add(jpg); add('\nendstream'); });
    obj(p + 2, () => add(`<< /Length ${cs.length} >>\nstream\n${cs}\nendstream`));
  });
  const n = 4 + jpegs.length * 3, xref = len;
  let x = `xref\n0 ${n}\n0000000000 65535 f \n`;
  for (let i = 1; i < n; i++) x += `${String(offsets[i]).padStart(10, '0')} 00000 n \n`;
  add(`${x}trailer\n<< /Size ${n} /Root 1 0 R /Info 3 0 R >>\nstartxref\n${xref}\n%%EOF\n`);
  const out = new Uint8Array(len);
  let o = 0;
  parts.forEach((b) => { out.set(b, o); o += b.length; });
  return out;
}

/** The signed confirmation in all three languages + the signing record → PDF bytes. */
/** extra: more signing-record rows (the uploaded owner documents: file names only, never their content). */
export async function buildExclusivePdf(objectId: number, d: ConfirmData, signature: HTMLCanvasElement, device: string, extra: [string, string][] = []): Promise<Uint8Array> {
  await Promise.race([
    Promise.all(['400', '700'].flatMap((w) => [document.fonts.load(`${w} 19px Montserrat`, 'Договор'), document.fonts.load(`${w} 19px "Noto Sans Georgian"`, 'ხელშეკრულება')])),
    new Promise((r) => setTimeout(r, 4000)),
  ]).catch(() => undefined);
  const canvas = document.createElement('canvas');
  canvas.width = W; canvas.height = H;
  const ctx = canvas.getContext('2d')!;
  const f = new Flow(ctx);
  (['ka', 'ru', 'en'] as Lang[]).forEach((lang) => {
    const [title, lines] = confirmText(lang, d);
    f.need(140);
    f.para(title, { size: 25, bold: true, color: NAVY, gap: 10 });
    lines.forEach((s) => f.para(s, { gap: s.startsWith('• ') ? 2 : 6 }));
    f.y += 26;
  });
  f.need(200);
  f.para('ხელმოწერა / Подпись / Signature', { size: 16, color: GREY, gap: 4 });
  f.page.push({ k: 'img', x: M, y: f.y, w: 460, h: 150 });
  f.y += 160;
  f.para(`${d.full_name} · ${d.date} (Tbilisi)`, { bold: true });
  f.y += 30;
  f.need(320);
  f.para('ხელმოწერის ოქმი / Протокол подписания / Signing record', { size: 21, bold: true, color: NAVY, gap: 10 });
  ([
    ['ობიექტი / Объект CRM', `#${objectId}`], ['სახელი და გვარი / ФИО', d.full_name], ['პირადი ნომერი / Личный номер', d.personal_id],
    ['ტელეფონი / Телефон', d.phone], ['მისამართი / Адрес', d.address || '—'], ['საკადასტრო კოდი / Кадастровый код', d.cadastral_code || '—'],
    ...extra, ['დრო / Время (Тбилиси)', d.date], ['მოწყობილობა / Устройство', device],
    ['', 'Собственник открыл ссылку от агента, заполнил данные, отметил согласие и расписался на экране. IP-адрес, время сервера и SHA-256 этого PDF записаны в CRM Homes in Georgia.'],
  ] as [string, string][]).forEach(([k, v]) => f.para(k ? `${k}: ${v}` : v, { size: 16, color: k ? INK : GREY, gap: 4 }));

  const pages = f.pages.filter((p) => p.length);
  const jpegs = pages.map((items, i) => {
    ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, W, H);
    ctx.textBaseline = 'alphabetic';
    ctx.font = font(22, true); ctx.fillStyle = NAVY; ctx.fillText('Homes in Georgia · Rent in Tbilisi', M, 90);
    ctx.textAlign = 'right'; ctx.font = font(16); ctx.fillStyle = GREY; ctx.fillText(`Объект #${objectId} · hge.ge`, W - M, 90); ctx.textAlign = 'left';
    ctx.fillStyle = NAVY; ctx.fillRect(M, 120, W - 2 * M, 3);
    items.forEach((it) => {
      if (it.k === 'img') fit(ctx, signature, it.x, it.y, it.w, it.h);
      else { ctx.font = font(it.size, it.bold); ctx.fillStyle = it.color || INK; ctx.fillText(it.s, it.x, it.y); }
    });
    const fy = H - 150;
    ctx.fillStyle = LINE; ctx.fillRect(M, fy, W - 2 * M, 1.5);
    ctx.font = font(15); ctx.fillStyle = GREY; ctx.fillText(`${d.full_name} · ${d.date}`, M, fy + 34);
    fit(ctx, signature, M + 560, fy + 8, 200, 64);
    ctx.textAlign = 'right'; ctx.fillText(`${i + 1} / ${pages.length}`, W - M, fy + 34); ctx.textAlign = 'left';
    return bytes(canvas.toDataURL('image/jpeg', 0.8));
  });
  return makePdf(jpegs, `Exclusivity confirmation · object #${objectId} · ${d.full_name}`);
}
