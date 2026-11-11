// Georgian cadastral code — same rule as backend services/common.py clean_cadastral (keep the two in sync):
// 4–9 dot-separated digit groups, the last may end with a letter (01.10.13.015.029.01.150ა), rarely a /suffix.
const RE = /^\d{1,3}(\.\d{1,6}){3,8}\p{L}?(\/\d{1,4}\p{L}?)?$/u;
export const CADASTRAL_HINT = 'Цифры через точку, например 01.14.12.003.045 или 01.10.13.015.029.01.150ა';

/** A leading N/№/: dropped, spaces/commas/backslashes → dots, no doubled/edge dots, a letter suffix glued to its group. */
export const cadastralNorm = (v: string) =>
  v.trim().replace(/^(N|№|:)\s*/i, '').replace(/[\s,\\]+/g, '.').replace(/\.{2,}/g, '.').replace(/^\.+|\.+$/g, '').replace(/\.(?=\p{L}$)/u, '');

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
