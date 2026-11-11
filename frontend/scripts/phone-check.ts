// node scripts/phone-check.ts — self-check for formatPhone (src/lib/format.ts)
import assert from 'node:assert/strict';
import { formatPhone as f } from '../src/lib/format.ts';

const cases: [string, string][] = [
  ['599206716', '+995 599 206 716'],
  ['+995599206716', '+995 599 206 716'],
  ['995599206716', '+995 599 206 716'],
  ['0599-20-67-16', '+995 599 206 716'],
  ['00995 599 206 716', '+995 599 206 716'],
  ['+995 (32) 212-34-56', '+995 322 123 456'],
  ['5', '+995 5'], ['5992', '+995 599 2'], ['59920', '+995 599 20'], ['5992067', '+995 599 206 7'],
  ['+', '+'], ['+9', '+9'], ['+99', '+99'], ['+995', '+995 '], ['+995 ', '+995 '], ['+995 5', '+995 5'],
  ['+79161234567', '+7 916 123 45 67'],
  ['+380671234567', '+380 671 23 45 67'],
  ['+12125551234', '+1 212 555 12 34'],
  ['79161234567', '+7 916 123 45 67'], // >9 digits w/o + can't be Georgian: grouped, nothing dropped
  ['599206716, 577112233', '599206716, 577112233'],
  ['Нино 599206716', 'Нино 599206716'],
  ['', ''],
  // typed after the «+995 » a phone field starts with: a trunk 0 or the code again is not duplicated
  ['+995 0', '+995 '], ['+995 0599206716', '+995 599 206 716'], ['+995 995', '+995 '], ['+995 995599206716', '+995 599 206 716'],
  ['+995 +995599206716', '+995 599 206 716'], ['+995 99', '+995 99'],
];
for (const [inp, out] of cases) assert.equal(f(inp), out, inp);
for (const [inp] of cases) { const once = f(inp); assert.equal(f(once), once, `idempotent: ${inp}`); }
assert.equal(f(null), '');
console.log(`formatPhone: ${cases.length} cases ok`);
