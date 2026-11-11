"""Google Docs HTML export -> compact block list.
Block: {"p": str, "a": "c"|"r"|None, "div": bool}   (bold spans as **...**)
       {"table": [[ [para,...] per cell ] per row ]}  para = {"p":..,"a":..}
"""
import json, re, sys
from html.parser import HTMLParser

ROMAN = ['', 'i', 'ii', 'iii', 'iv', 'v', 'vi', 'vii', 'viii', 'ix', 'x', 'xi', 'xii']


def fmt_num(n, kind):
    if kind == 'lower-latin':
        return chr(96 + n)
    if kind == 'lower-roman':
        return ROMAN[n]
    return str(n)


class P(HTMLParser):
    def __init__(self, css):
        super().__init__(convert_charrefs=True)
        self.css = css
        self.out = []          # top-level blocks
        self.stack = []        # table contexts: {"rows": [], "row": None, "cell": None}
        self.para = None       # list of (text, bold)
        self.palign = None
        self.bold = []
        self.ol = []           # [kind, next_number]
        self.div = 0
        self.in_style = False
        self.img = 0

    def sink(self):
        if self.stack and self.stack[-1]['cell'] is not None:
            return self.stack[-1]['cell']
        return self.out

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        st = a.get('style', '') or ''
        if tag == 'style':
            self.in_style = True
        elif tag == 'div':
            self.div += 1
        elif tag == 'table':
            self.stack.append({'rows': [], 'row': None, 'cell': None})
        elif tag == 'tr':
            self.stack[-1]['row'] = []
        elif tag in ('td', 'th'):
            self.stack[-1]['cell'] = []
            self.stack[-1]['span'] = int(a.get('colspan') or 1)
        elif tag == 'ol':
            cls = (a.get('class') or '').split()[0]
            m = re.search(r'\.' + re.escape(cls) + r' > li:before\{content:"" counter\([^,]+,([a-z-]+)\) "\. "\}', self.css)
            self.ol.append([m.group(1) if m else 'decimal', int(a.get('start') or 1)])
        elif tag in ('p', 'li', 'h1', 'h2', 'h3', 'h4'):
            self.para = []
            m = re.search(r'text-align:(\w+)', st)
            al = m.group(1) if m else 'left'
            self.palign = {'center': 'c', 'right': 'r'}.get(al)
            if tag == 'li':
                kind, n = self.ol[-1]
                self.ol[-1][1] += 1
                self.para.append((fmt_num(n, kind) + '. ', None))
        elif tag == 'span':
            m = re.search(r'font-weight:(\d+)', st)
            self.bold.append(None if not m else int(m.group(1)) >= 700)
        elif tag == 'br':
            if self.para is not None:
                self.para.append(('\n', None))
        elif tag == 'img':
            self.img += 1

    def handle_endtag(self, tag):
        if tag == 'style':
            self.in_style = False
        elif tag == 'div':
            self.div -= 1
        elif tag == 'span':
            if self.bold:
                self.bold.pop()
        elif tag == 'ol':
            self.ol.pop()
        elif tag in ('p', 'li', 'h1', 'h2', 'h3', 'h4'):
            if self.para is None:
                return
            text = to_marked(self.para)
            blk = {'p': text}
            if self.palign:
                blk['a'] = self.palign
            if self.div:
                blk['div'] = True
            self.sink().append(blk)
            self.para = None
        elif tag in ('td', 'th'):
            t = self.stack[-1]
            t['row'].append({'c': t['cell'], 's': t['span']} if t['span'] > 1 else t['cell'])
            t['cell'] = None
        elif tag == 'tr':
            t = self.stack[-1]
            t['rows'].append(t['row'])
            t['row'] = None
        elif tag == 'table':
            t = self.stack.pop()
            self.sink().append({'table': t['rows']})

    def handle_data(self, data):
        if self.in_style or self.para is None:
            return
        b = next((x for x in reversed(self.bold) if x is not None), False)
        self.para.append((data, b))


PH = re.compile(r'(?:\[\[|\{\{)\s*([A-Za-z_]+)\s*(?:\]\]|\}\})')


def to_marked(runs):
    # char-level bold array; list numbers (bold None) take the bold of the next char
    chars, bolds = [], []
    for t, b in runs:
        for ch in t:
            chars.append(ch)
            bolds.append(b)
    s = ''.join(chars).replace('\xa0', ' ')
    # placeholders: whole [[...]] span gets the bold state of the field name
    for m in PH.finditer(s):
        nb = bolds[m.start(1)]
        for i in range(m.start(), m.end()):
            bolds[i] = nb
    # list-number prefix bold = first real char bold
    nxt = False
    for i in range(len(bolds) - 1, -1, -1):
        if bolds[i] is None:
            bolds[i] = nxt
        else:
            nxt = bolds[i]
    # whitespace-only bold toggles are dropped (keeps markup tidy)
    out, cur = [], False
    for ch, b in zip(s, bolds):
        if ch in ' \n' and b != cur:
            b = cur
        if b != cur:
            out.append('**')
            cur = b
        out.append(ch)
    if cur:
        out.append('**')
    r = ''.join(out)
    # trim and normalise "** **" artifacts
    r = r.replace('****', '')
    return r.rstrip(' ') if not r.strip() == '' else ''


def parse(path):
    h = open(path, encoding='utf-8').read()
    css = ''.join(re.findall(r'<style[^>]*>(.*?)</style>', h, re.S))
    p = P(css)
    p.feed(h)
    return p.out


if __name__ == '__main__':
    print(json.dumps(parse(sys.argv[1]), ensure_ascii=False, indent=1))
