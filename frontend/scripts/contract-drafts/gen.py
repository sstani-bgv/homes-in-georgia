"""Builds frontend/src/features/contracts/templates-david.ts from Google Docs HTML exports of the contract drafts.

  1. Export each draft of DOCS as HTML (Google Docs: File → Download → Web page, or
     https://docs.google.com/document/d/<id>/export?format=html) into one folder as <key>.html (rent_GEO.html ...).
  2. python3 gen.py <that folder> ../../src/features/contracts/templates-david.ts
FIXES (below) are our corrections of slips in the originals, applied after the conversion: keep them when regenerating.
A new draft: add a DOCS row and a DraftKey in templates.ts (see docs/CONTRACTS.md)."""
import json, re, sys
sys.path.insert(0, sys.path[0])
from parse import parse

DOCS = [
    ('rent_GEO', 'DRAFT GEO 2024', '1Wopykf6-csDG0ZlmEk2CpF_EKQ0HO6MhxZgi0ui1MM0'),
    ('rent_RU', 'DRAFT (GEO+RUS) 2024', '10wfKLkSsZV6Dw8jbiX59CC8HFX_WW7RTpYfHRv3cyNU'),
    ('rent_EN', 'DRAFT (GEO+ENG) 2024', '1kg6xq5CGIqxFn0jF-iYH_5Ba52ZuCs3Ueyu3fl__vw8'),
    ('pet_GEO', 'Pet DRAFT GEO 2024', '16z3CxbY7QCuB_PUR2Clfu_4WR8ktGYFi814c3N36Tkg'),
    ('pet_RU', 'Pet DRAFT (GEO+RUS) 2024', '1GrfgitRIt8865ynXqyIllAXROv447Yep1BOLEGaLeRA'),
    ('pet_EN', 'Pet DRAFT (GEO+ENG) 2024', '16R2Y0Y8ru1g_qUDuJMzj78p-t5XZzAhZWWsyOMSykj8'),
    ('comm_GEO', 'DRAFT Commercial (GEO) 2024', '1ANp5k8Xrisom_psOzic438H8PuVS7E1E_7hceuQZzYE'),
    ('comm_RU', 'DRAFT Commercial (GEO+RUS) 2024', '1Ao8C_qjP78wHgSNcOcq75bmqAXSN0xye8kkTkTO6i94'),
    ('sale_GEO', 'DRAFT Sale (GEO) 2024', '1j4lT-RPhwqrg31cUgh5yH56xq6qBAHXnz1BBCU0XD64'),
]


def is_empty(b):
    return isinstance(b, dict) and 'p' in b and not b['p'].replace('*', '').strip()


def tidy(blocks):
    """collapse runs of empty paragraphs, strip them at both ends; compact form."""
    out = []
    for b in blocks:
        if 'table' in b:
            out.append({'t': table(b['table'])})
            continue
        b = dict(b)
        b['p'] = b['p'].strip(' ').replace('** **', ' ')
        if is_empty(b):
            if out and out[-1] == '':
                continue
            out.append('')
            continue
        if set(b) == {'p'}:
            out.append(b['p'])
        else:
            out.append(b)
    while out and out[0] == '':
        out.pop(0)
    while out and out[-1] == '':
        out.pop()
    return out


def cell_blocks(c):
    return c['c'] if isinstance(c, dict) else c


def table(rows):
    rows = [[{'s': c['s'], 'c': tidy(c['c'])} if isinstance(c, dict) else tidy(c) for c in r] for r in rows]
    rows = [r for r in rows if any(cell_blocks(c) for c in r)]
    # drop spacer columns that are empty in every row (only for rows without colspan)
    if rows and all(not isinstance(c, dict) for r in rows for c in r) and len({len(r) for r in rows}) == 1:
        keep = [i for i in range(len(rows[0])) if any(r[i] for r in rows)]
        rows = [[r[i] for i in keep] for r in rows]
    return rows


def split_hf(blocks):
    head = [b for b in blocks if b.get('div')]
    body = [b for b in blocks if not b.get('div')]
    # header = div blocks before the first body block, footer = the rest
    first = blocks.index(body[0]) if body else len(blocks)
    h = [b['p'] for b in blocks[:first] if b.get('div') and b['p'].strip()]
    f = [b['p'] for b in blocks[first:] if b.get('div') and b['p'].strip()]
    for b in head:
        b.pop('div', None)
    return h, tidy(body), f


J = lambda x: json.dumps(x, ensure_ascii=False)


def emit(blocks, ind):
    out = []
    for b in blocks:
        if isinstance(b, dict) and 't' in b:
            out.append(ind + '{ t: [')
            for r in b['t']:
                out.append(ind + '  [')
                for c in r:
                    if isinstance(c, dict):
                        out.append(ind + f"    {{ s: {c['s']}, c: [")
                        out += emit(c['c'], ind + '      ')
                        out.append(ind + '    ] },')
                    elif all(isinstance(x, str) for x in c) and len(J(c)) < 90:
                        out.append(ind + '    ' + J(c) + ',')
                    else:
                        out.append(ind + '    [')
                        out += emit(c, ind + '      ')
                        out.append(ind + '    ],')
                out.append(ind + '  ],')
            out.append(ind + '] },')
        elif isinstance(b, dict):
            out.append(ind + f"{{ p: {J(b['p'])}, a: {J(b['a'])} }},")
        else:
            out.append(ind + J(b) + ',')
    return out


def main(src, dst):
    parts = []
    for key, title, fid in DOCS:
        h, body, f = split_hf(parse(f'{src}/{key}.html'))
        parts.append('\n'.join([f'  // {title} — https://docs.google.com/document/d/{fid}',
                                 f'  {key}: {{', f'    header: {J(h)},', f'    footer: {J(f)},', '    body: [',
                                 *emit(body, '      '), '    ],', '  },']))
    ts = ('// GENERATED from David\'s 2024 Google Docs drafts (owner ddavitashvili@rentintbilisi.ge), text verbatim.\n'
          '// Each draft was exported as HTML and converted: paragraphs are strings (**bold**), centered/right ones\n'
          '// {p, a}, tables {t: rows[cells[blocks]]}, a colspan cell {s, c}. Placeholders [[field]] / {{field}} are\n'
          '// filled by buildContract() in templates.ts. Only empty spacer paragraphs/columns and the logo image were dropped.\n'
          'import type { DraftDoc, DraftKey } from \'./templates\';\n\n'
          'export const DRAFTS: Record<DraftKey, DraftDoc> = {\n' + '\n'.join(parts) + '\n};\n')
    open(dst, 'w', encoding='utf-8').write(fix(ts))


# 2026-11-11, slips in David's 2024 drafts (Stanislav: «поправь ошибки в оригиналах»): (old, new) text replacements,
# then the clauses that ran together in one paragraph are split onto their own lines
FIXES = [
    ('"„1.4. ', '"1.4. '),                                            # a stray opening quote before clause 1.4 (GEO)
    ('ელექტროენერგია: ელექტროენერგია:', 'ელექტროენერგია:'),          # doubled label
    ('{{apartment_number}},,', '{{apartment_number}},'),              # double comma (GEO column of the EN drafts)
    ('[[sale_price]] (სამოცდაათი ათასი)  აშშ დოლარად', '[[sale_price]] აშშ დოლარად'),  # «seventy thousand» was fixed text
    ('**___ **რიცხვისა', '**[[pay_day]] **რიცხვისა'),                  # commercial: the pay day was a blank
    ('**___ **числа', '**[[pay_day]] **числа'),
    ('Guarantee deposits', 'Security Deposit'), ('Guarantee deposit', 'Security Deposit'),  # one term, as clause 2.3
    ('Guarantee Deposit', 'Security Deposit'),
]
MERGED = re.compile(r'([;.]) (4\.1\.8|4\.2\.2|4\.2\.4|5\.2|5\.4)\. ')


def fix(ts):
    for old, new in FIXES:
        ts = ts.replace(old, new)
    ts = MERGED.sub(r'\1\\n\2. ', ts)
    i, j = ts.index('  comm_GEO: {'), ts.index('  comm_RU: {')  # its page header said SALE IN TBILISI
    return ts[:i] + ts[i:j].replace('header: ["SALE IN TBILISI"]', 'header: ["RENT IN TBILISI"]') + ts[j:]


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
