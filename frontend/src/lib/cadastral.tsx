// Georgian cadastral code — same rule as backend services/common.py clean_cadastral (keep the two in sync):
// 4–9 dot-separated digit groups, the last may end with a letter (01.10.13.015.029.01.150ა), rarely a /suffix.
const RE = /^\d{1,3}(\.\d{1,6}){3,8}\p{L}?(\/\d{1,4}\p{L}?)?$/u;
export const CADASTRAL_HINT = 'Цифры через точку, например 01.14.12.003.045 или 01.10.13.015.029.01.150ა';

/** A leading N/№/: dropped, spaces/commas/backslashes → dots, no doubled/edge dots, a letter suffix glued to its group. */
export const cadastralNorm = (v: string) =>
  v.trim().replace(/^(N|№|:)\s*/i, '').replace(/[\s,\\]+/g, '.').replace(/\.{2,}/g, '.').replace(/^\.+|\.+$/g, '').replace(/\.(?=\p{L}$)/u, '');

// The real codes (≈1200 objects of the old CRM): 2.2.2.3.3 (a plot), +.2.3 (most flats), +.2.2.3 (19 digits).
const GROUPS = [2, 2, 2, 3, 3, 2, 3, 3, 3];
const GROUPS_LONG = [2, 2, 2, 3, 3, 2, 2, 3, 3];

/** As-you-type: digits get their dots (011412003045 → 01.14.12.003.045); dots the person typed stay (a shorter group
 * is his call); spaces/commas are dropped; a trailing letter (ა, A) and a /suffix are kept; anything else → as is. */
export function cadastralFormat(v: string): string {
  const m = /^\s*(?:N|№)?\s*([\d.,\s]*?)(\p{L}?)(\/\S*)?\s*$/u.exec(v);
  if (!m || !m[1]) return v;
  const base = m[1].replace(/[\s,]+/g, '').replace(/\.{2,}/g, '.').replace(/^\./, '');
  const tokens = base.split('.');
  const pattern = base.replace(/\D/g, '').length > 17 ? GROUPS_LONG : GROUPS;
  const out: string[] = [];
  tokens.forEach((tk, ti) => {
    let t = tk;
    if (!t && ti === tokens.length - 1) return;  // a dot just typed: kept below
    while (t.length > (pattern[out.length] ?? 9)) { out.push(t.slice(0, pattern[out.length] ?? 9)); t = t.slice(pattern[out.length - 1] ?? 9); }
    out.push(t);
  });
  return out.join('.') + (base.endsWith('.') && !m[2] && !m[3] ? '.' : '') + m[2] + (m[3] || '');
}

/** onChange helper (like phoneInput): formats while typing at the end; deleting or editing inside stays as typed,
 * so backspace over a dot just removes it. */
export function cadastralInput(e: { target: HTMLInputElement; nativeEvent: Event }): string {
  const el = e.target, raw = el.value;
  if ((e.nativeEvent as InputEvent).inputType?.startsWith('delete') || (el.selectionStart ?? raw.length) < raw.length) return raw;
  return cadastralFormat(raw);
}

/** null when empty or valid; the hint otherwise. `known` (the object's saved code) passes as is: legacy codes stay. */
export function cadastralError(v: string | null | undefined, known?: string | null): string | null {
  const s = (v || '').trim();
  if (!s || s === (known || '').trim() || RE.test(cadastralNorm(s))) return null;
  return `Неверный кадастровый код. ${CADASTRAL_HINT}`;
}

/** A Field hint: the error in red, else `fallback`. */
export const cadastralHint = (v: string | null | undefined, known?: string | null, fallback?: string) => {
  const e = cadastralError(v, known);
  return e ? <span style={{ color: 'var(--accent)' }}>{e}</span> : fallback;
};

// ponytail: tiny self-check, runs in dev only
if (import.meta.env?.DEV) {
  const cases: [string, string][] = [['011412003045', '01.14.12.003.045'], ['0110130150290 1150', '01.10.13.015.029.01.150'],
    ['01101301502901150ა', '01.10.13.015.029.01.150ა'], ['0110130150290101150', '01.10.13.015.029.01.01.150'],
    ['01,10,13,015,029', '01.10.13.015.029'], ['01.13.06.008', '01.13.06.008'], ['01.14.', '01.14.'], ['0', '0'],
    ['01.10.10.02.20', '01.10.10.02.20'], ['01.14.12.003.045/2', '01.14.12.003.045/2'], ['N 011412003045A', '01.14.12.003.045A'],
    ['abc', 'abc'], ['01.14.12.00304', '01.14.12.003.04']];
  for (const [a, b] of cases) if (cadastralFormat(a) !== b) console.error('cadastralFormat', a, '→', cadastralFormat(a), '≠', b);
}
