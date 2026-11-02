"""Building blocks for the user-journey mockups: Telegram chat, Telegram Mini App and the operator's desktop panel.
All data on the screens is invented. Pages are plain HTML, screenshotted by build.py with headless Chrome."""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
IMG = os.path.join(ROOT, "raw", "img")


def photo(name):
    """file:// URL of a listing photo from raw/img (interior photos only, no captions or contacts)."""
    return "file://" + os.path.join(IMG, name + ".jpg")


def fontface(url_cyr, url_lat):
    return (f"@font-face{{font-family:Montserrat;font-weight:100 900;src:url({url_cyr}) format('woff2');"
            "unicode-range:U+0301,U+0400-045F,U+0490-0491,U+04B0-04B1,U+2116}"
            f"@font-face{{font-family:Montserrat;font-weight:100 900;src:url({url_lat}) format('woff2');"
            "unicode-range:U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+0304,U+0308,U+0329,"
            "U+2000-206F,U+20AC,U+2122,U+2191,U+2193,U+2212,U+2215,U+FEFF,U+FFFD}")


FONTS = fontface("file://" + os.path.join(HERE, "fonts", "cyr.woff2"), "file://" + os.path.join(HERE, "fonts", "lat.woff2"))

BRAND = """:root{--blue:#032D6A;--deep:#021F4A;--red:#E3172D;--grey:#F3F5F9;--muted:#4A5A75;--line:#E1E6EF;
--ok:#1E8E55;--okbg:#E3F4EA;--warn:#B7791F;--warnbg:#FFF4DB;--bad:#E3172D;--badbg:#FDE8EA}
*{box-sizing:border-box}
.lbl{font:700 10px Montserrat,sans-serif;letter-spacing:.14em;text-transform:uppercase;color:var(--muted)}
.tag{display:inline-block;font:700 10px Montserrat,sans-serif;letter-spacing:.12em;text-transform:uppercase;padding:3px 7px;border-radius:4px;
 background:var(--grey);color:var(--blue);white-space:nowrap}
.tag.red{background:var(--red);color:#fff}.tag.blue{background:var(--blue);color:#fff}
.tag.ok{background:var(--okbg);color:var(--ok)}.tag.warn{background:var(--warnbg);color:var(--warn)}.tag.bad{background:var(--badbg);color:var(--bad)}
.tag.white{background:#fff}
.btn{display:inline-block;font:700 11px Montserrat,sans-serif;letter-spacing:.14em;text-transform:uppercase;background:var(--blue);color:#fff;
 border-radius:10px;padding:12px 14px;text-align:center}
.btn.ghost{background:#fff;color:var(--blue);box-shadow:inset 0 0 0 1.5px var(--blue)}
.btn.deep{background:var(--deep)}.btn.ok{background:var(--ok)}.btn.off{background:#B9C3D3}
.btn.sm{padding:8px 10px;font-size:10px;letter-spacing:.1em}
.timer{display:inline-flex;align-items:center;gap:4px;font:800 12px Montserrat,sans-serif;padding:3px 8px;border-radius:20px;white-space:nowrap}
.timer.g{background:var(--okbg);color:var(--ok)}.timer.y{background:var(--warnbg);color:var(--warn)}.timer.r{background:var(--red);color:#fff}
.timer.n{background:var(--grey);color:var(--muted)}
"""

# ---------------------------------------------------------------- Telegram chat (phone, 390 px)

TG = """body{margin:0;width:390px;background:#fff;font:15.5px/1.36 -apple-system,'SF Pro Text',system-ui,sans-serif;color:#000}
.sb{height:46px;display:flex;justify-content:space-between;align-items:flex-end;padding:0 28px 6px 34px;font-weight:600;font-size:15px;background:#f6f6f6}
.hd{background:#f6f6f6;display:flex;align-items:center;padding:4px 12px 8px;gap:8px;border-bottom:.5px solid #c8c7cc}
.hd .back{color:#3390ec;font-size:16px;width:78px;white-space:nowrap}
.hd .ttl{flex:1;text-align:center;line-height:1.2}.hd .ttl b{display:block;font-size:16px;font-weight:600}.hd .ttl small{color:#8e8e93;font-size:12.5px}
.hd .ava{width:38px;height:38px;border-radius:50%;background:linear-gradient(180deg,#032D6A,#021F4A);color:#fff;font:900 11px Montserrat;
 display:flex;align-items:center;justify-content:center;margin-left:40px}
.role{background:var(--deep);color:#fff;font:700 10px Montserrat;letter-spacing:.14em;text-transform:uppercase;padding:7px 14px;display:flex;
 justify-content:space-between;align-items:center}
.role .tag{font-size:9px}
.chat{background:#cfe2b3 linear-gradient(165deg,#dbe9c2 0%,#c3dba6 45%,#a9cb8f 100%);padding:10px 8px 12px;display:flex;flex-direction:column;
 justify-content:flex-end;gap:5px}
.date{align-self:center;background:rgba(40,70,30,.32);color:#fff;font-size:12.5px;font-weight:600;padding:3px 10px;border-radius:12px;margin:4px 0}
.grp{display:flex;flex-direction:column;gap:3px;max-width:86%;align-self:flex-start}
.grp.wk{width:86%}.grp.out{align-self:flex-end}
.m{padding:6px 10px 7px 11px;border-radius:17px;position:relative;box-shadow:0 1px 1px rgba(0,0,0,.14);overflow-wrap:anywhere;background:#fff;
 border-bottom-left-radius:6px}
.out .m{background:#e1fec6;border-bottom-left-radius:17px;border-bottom-right-radius:6px}
.m .tm{float:right;font-size:11.5px;color:#a0a7ad;margin:9px -2px -5px 12px;white-space:nowrap}
.out .m .tm{color:#4fae4e}
.m b{font-weight:600}.m .lnk{color:#2f7fc9}
.m .q{border-left:3px solid #3390ec;background:rgba(51,144,236,.08);padding:3px 8px;border-radius:4px;margin:2px 0 6px;font-size:14px}
.m .q b{color:#3390ec}
.m .mute{color:#8b8f94;font-size:14px}
.m .lock{background:#F3F5F9;border-radius:8px;padding:6px 9px;margin-top:6px;font-size:14px;color:#4A5A75}
.m .red{color:#E3172D;font-weight:600}
.m .card{background:#F3F5F9;border-radius:10px;padding:7px 10px;margin:6px 0 2px;font-size:14.5px}
.row{display:flex;gap:3px}
.row span{flex:1;background:rgba(56,92,44,.42);color:#fff;text-align:center;padding:9px 6px;border-radius:9px;font-size:14px;font-weight:500;
 white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.row span.sel{background:rgba(3,45,106,.88)}
.row span.wa::after{content:' ↗';opacity:.85}
.alb{display:grid;grid-template-columns:1fr 1fr;gap:2px;border-radius:15px;overflow:hidden;width:86%;align-self:flex-start;box-shadow:0 1px 1px rgba(0,0,0,.14)}
.alb img{width:100%;height:122px;object-fit:cover;display:block}.alb img:first-child{grid-column:span 2;height:190px}
.alb .v{position:relative}.alb .v img{height:122px}.alb .v i{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);width:38px;height:38px;
 border-radius:50%;background:rgba(0,0,0,.5);color:#fff;font-style:normal;display:flex;align-items:center;justify-content:center;font-size:15px}
.ph{align-self:flex-end;background:#e1fec6;padding:3px;border-radius:15px;box-shadow:0 1px 1px rgba(0,0,0,.14);width:62%}
.ph img{width:100%;height:200px;object-fit:cover;border-radius:12px;display:block}
.ph p{margin:4px 7px 3px;font-size:15px}
.svc{align-self:center;background:rgba(40,70,30,.32);color:#fff;font-size:13px;padding:4px 12px;border-radius:12px;text-align:center;max-width:88%}
.toast{align-self:center;background:rgba(0,0,0,.72);color:#fff;font-size:13.5px;padding:8px 14px;border-radius:10px;margin:4px 0}
.ib{background:#f6f6f6;border-top:.5px solid #c8c7cc;display:flex;align-items:center;gap:8px;padding:7px 9px 28px}
.menu{background:#3390ec;color:#fff;font-weight:600;font-size:14.5px;padding:7px 13px;border-radius:17px;display:flex;align-items:center;gap:6px}
.menu i{font-style:normal;font-size:13px}
.inp{flex:1;background:#fff;border:.5px solid #d1d1d6;border-radius:18px;padding:7px 12px;color:#9a9aa0;font-size:15.5px}
.ic{color:#8e8e93;font-size:20px}
.m .nm{display:block;font-weight:600;font-size:14px;color:#3390ec;margin-bottom:1px}
.m .ok{display:block;margin-top:8px;padding:7px 9px;border-radius:9px;background:#e6f6ea;color:#1f6b33;font-size:14.5px;font-weight:600}
.m .ok small{display:block;font-weight:400;font-size:13px;color:#2f7a45}
.m.hl{box-shadow:0 0 0 3px #ff9500,0 1px 1px rgba(0,0,0,.14)}
.m .pic{margin:-6px -10px 7px -11px}.m .pic img{display:block;width:100%;height:190px;object-fit:cover;border-radius:17px 17px 4px 4px}
.hd .ava.g{background:linear-gradient(180deg,#5ac8fa,#3390ec)}
"""


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def nl(s):
    return s.strip("\n").replace("\n", "<br>")


def kb(rows):
    """rows: [[label, ...], ...]; a label starting with '*' is highlighted (chosen), ending with '↗' opens a Mini App."""
    out = []
    for r in rows:
        cells = []
        for b in r:
            cls = []
            if b.startswith("*"):
                b, _ = b[1:], cls.append("sel")
            if b.endswith("↗"):
                b, _ = b[:-1].rstrip(), cls.append("wa")
            cells.append(f'<span class="{" ".join(cls)}">{b}</span>')
        out.append('<div class="row">' + "".join(cells) + "</div>")
    return "".join(out)


def bot(text, tm, btns=None, edited=False, name=None, pic=None, hl=False, extra=""):
    """A bot message. name: sender line (in groups), pic: photo on top, hl: orange frame (the part that changed),
    extra: html block under the text (e.g. the green "✅ Передано оператору" line of an edited message)."""
    ed = "изменено " if edited else ""
    nm = f'<span class="nm">{name}</span>' if name else ""
    img = f'<div class="pic"><img src="{photo(pic)}"></div>' if pic else ""
    body = f'<div class="m{" hl" if hl else ""}">{img}{nm}{nl(text)}{extra}<span class="tm">{ed}{tm}</span></div>'
    return f'<div class="grp{" wk" if btns else ""}">{body}{kb(btns) if btns else ""}</div>'


def me(text, tm):
    return f'<div class="grp out"><div class="m">{nl(text)}<span class="tm">{tm} ✓✓</span></div></div>'


def album(names, video=False):
    imgs = "".join(f'<img src="{photo(n)}">' for n in names[:-1])
    last = names[-1]
    last = f'<div class="v"><img src="{photo(last)}"><i>▶</i></div>' if video else f'<img src="{photo(last)}">'
    return f'<div class="alb">{imgs}{last}</div>'


def my_photo(name, caption, tm):
    return f'<div class="ph"><img src="{photo(name)}"><p>{caption}<span style="float:right;font-size:11.5px;color:#4fae4e;margin-top:6px">{tm} ✓✓</span></p></div>'


def date(s):
    return f'<div class="date">{s}</div>'


def svc(s):
    return f'<div class="svc">{s}</div>'


def toast(s):
    return f'<div class="toast">{s}</div>'


def phone(role, who, blocks, clock="10:04", min_h=844, sub="бот", title="Homes in Georgia", ava="HiG"):
    """A Telegram private chat with the bot. role: КЛИЕНТ | ОПЕРАТОР | АГЕНТ, who: the person on the screen."""
    chat_h = min_h - 46 - 55 - 30 - 64
    return (f'<!doctype html><html lang="ru"><head><meta charset="utf-8"><style>{FONTS}{BRAND}{TG}</style></head><body>'
            f'<div class="sb"><span>{clock}</span><span style="font-size:13px;letter-spacing:1px">▂▄▆█ 5G ▭</span></div>'
            f'<div class="hd"><span class="back">‹ Чаты</span><div class="ttl"><b>{title}</b>'
            f'<small>{sub}</small></div><div class="ava{" g" if ava != "HiG" else ""}">{ava}</div></div>'
            f'<div class="role"><span>{role} · {who}</span><span class="tag red">макет</span></div>'
            f'<div class="chat" style="min-height:{chat_h}px">{"".join(blocks)}</div>'
            '<div class="ib"><span class="menu"><i>☰</i>Панель</span><span class="ic">📎</span><span class="inp">Сообщение</span>'
            '<span class="ic">🎤</span></div></body></html>')


# ---------------------------------------------------------------- Telegram Mini App "Панель" (phone, 390 px)

APP = """body{margin:0;width:390px;background:#fff;font:400 14px/1.45 Montserrat,'Noto Sans Georgian',system-ui,sans-serif;color:var(--deep)}
.sb{height:46px;display:flex;justify-content:space-between;align-items:flex-end;padding:0 28px 6px 34px;font:600 15px -apple-system,system-ui;background:#fff}
.tg{display:flex;align-items:center;padding:6px 14px 10px;font:15px -apple-system,system-ui;border-bottom:.5px solid #e2e2e6}
.tg .x{color:#3390ec;width:80px}.tg .t{flex:1;text-align:center;line-height:1.15}.tg .t b{display:block;font-weight:600}.tg .t small{color:#8e8e93;font-size:12px}
.tg .d{width:80px;text-align:right;color:#3390ec;font-size:20px;letter-spacing:1px}
.mh{background:linear-gradient(180deg,var(--blue),var(--deep));color:#fff;padding:14px 16px 0}
.mh .brand{display:flex;justify-content:space-between;align-items:center;font-weight:900;font-size:15px;text-transform:uppercase;letter-spacing:.01em}
.tabs{display:flex;gap:2px;margin-top:12px}
.tabs span{font:700 10px Montserrat;letter-spacing:.12em;text-transform:uppercase;padding:10px 7px;border-radius:9px 9px 0 0;opacity:.7;white-space:nowrap}
.tabs span.on{background:#fff;color:var(--blue);opacity:1}
.tabs span b{background:var(--red);color:#fff;border-radius:8px;padding:0 5px;margin-left:4px;font-size:9px}
.wrap{padding:14px 14px 30px;display:flex;flex-direction:column;gap:12px}
.chips{display:flex;gap:6px;flex-wrap:wrap}
.chip{font:700 11px Montserrat;padding:7px 11px;border-radius:20px;background:var(--grey);color:var(--blue);white-space:nowrap}
.chip.on{background:var(--blue);color:#fff}.chip.dis{color:#B5BECC;text-decoration:line-through}
.card{background:var(--grey);border-radius:14px;padding:12px}
.card.white{background:#fff;box-shadow:0 0 0 1.5px var(--line)}
.card.locked{opacity:.8}
.item{display:grid;grid-template-columns:92px minmax(0,1fr);gap:11px}
.item img{width:92px;height:92px;object-fit:cover;border-radius:10px;display:block}
.item .t{font-weight:800;color:var(--blue);font-size:14.5px;line-height:1.25}
.item .s{color:var(--muted);font-size:12px;margin:2px 0 6px}
.badges{display:flex;gap:4px;flex-wrap:wrap}.badges .tag{white-space:normal;line-height:1.3}
.grid2 .btn,.acts .btn{letter-spacing:.06em}
.btn.lockd{background:#E1E6EF;color:var(--muted)}
.acts{display:flex;gap:8px;margin-top:10px}.acts .btn{flex:1}
h3{margin:0;font-weight:900;font-size:17px;color:var(--blue);text-transform:uppercase;line-height:1.15}
.kv{display:grid;grid-template-columns:104px minmax(0,1fr);gap:5px 10px;font-size:13px}.kv span:nth-child(odd){color:var(--muted)}
.step{display:flex;gap:10px;align-items:flex-start}
.step .n{flex:0 0 24px;height:24px;border-radius:50%;background:var(--blue);color:#fff;font:800 12px Montserrat;display:flex;align-items:center;justify-content:center}
.step.done .n{background:var(--ok)}.step.todo .n{background:#C9D1DE}
.step .b{flex:1}.step .b .h{font-weight:800;font-size:13.5px}.step .b .h small{font-weight:500;color:var(--muted)}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.grid3{display:grid;grid-template-columns:repeat(3,1fr);gap:6px}
.grid4{display:grid;grid-template-columns:repeat(4,1fr);gap:6px}
.slot{font:700 13px Montserrat;padding:10px 0;border-radius:10px;background:var(--grey);color:var(--blue);text-align:center}
.slot.on{background:var(--blue);color:#fff}.slot.dis{color:#B5BECC}
.banner{border-radius:12px;padding:11px 12px;font-weight:700;font-size:13.5px;display:flex;gap:9px;align-items:flex-start}
.banner.ok{background:var(--okbg);color:var(--ok)}.banner.warn{background:var(--warnbg);color:var(--warn)}.banner.blue{background:var(--blue);color:#fff}
.banner small{display:block;font-weight:500;font-size:12px;opacity:.9;margin-top:2px}
.field{display:flex;flex-direction:column;gap:4px}
.inp{border:1.5px solid #D5DBE6;border-radius:9px;padding:9px 10px;font-size:14px;background:#fff}
.seg{display:flex;background:var(--grey);border-radius:10px;padding:3px}.seg span{flex:1;text-align:center;font:700 12px Montserrat;padding:8px 0;border-radius:8px;color:var(--muted)}
.seg span.on{background:var(--blue);color:#fff}
.calc{background:var(--deep);color:#fff;border-radius:14px;padding:14px}
.calc .r{display:flex;justify-content:space-between;font-size:13.5px;padding:3px 0}.calc .r b{font-weight:800}
.calc .big{font-weight:900;font-size:24px}
.thumbs{display:grid;grid-template-columns:repeat(3,1fr);gap:4px}
.thumbs div{position:relative}.thumbs img{width:100%;height:84px;object-fit:cover;border-radius:8px;display:block}
.thumbs i{position:absolute;right:4px;top:4px;background:var(--ok);color:#fff;border-radius:50%;width:18px;height:18px;font-style:normal;font-size:11px;
 display:flex;align-items:center;justify-content:center}
.thumbs .vid i{background:rgba(0,0,0,.55);left:50%;top:50%;right:auto;transform:translate(-50%,-50%);width:30px;height:30px}
.check{display:grid;grid-template-columns:1fr 1fr;gap:4px 10px;font-size:12.5px}
.hint{font-size:12px;color:var(--muted)}
.sheet{background:#fff;border-radius:18px 18px 0 0;box-shadow:0 -6px 24px rgba(2,31,74,.18);padding:16px 14px 26px;margin:0 -14px -30px}
hr{border:0;border-top:1px solid var(--line);margin:4px 0}
"""


def miniapp(who, level, tab, body, clock="10:12", tabs=("Объявления", "Мои объекты", "Заявки", "Сделки"), badge=None):
    tabs_html = "".join(f'<span class="{"on" if t == tab else ""}">{t}{f"<b>{badge[1]}</b>" if badge and badge[0] == t else ""}</span>'
                        for t in tabs)
    return (f'<!doctype html><html lang="ru"><head><meta charset="utf-8"><style>{FONTS}{BRAND}{APP}</style></head><body>'
            f'<div class="sb"><span>{clock}</span><span style="font-size:13px;letter-spacing:1px">▂▄▆█ 5G ▭</span></div>'
            '<div class="tg"><span class="x">Закрыть</span><div class="t"><b>Homes in Georgia</b><small>мини-приложение</small></div>'
            '<span class="d">⋯</span></div>'
            f'<div class="mh"><div class="brand"><span>Панель агента</span><span class="tag red">{who} · {level}</span></div>'
            f'<div class="tabs">{tabs_html}</div></div><div class="wrap">{body}</div></body></html>')


# ---------------------------------------------------------------- operator's desktop panel (1280 px)

DESK = """body{margin:0;width:1280px;background:#fff;font:400 14px/1.45 Montserrat,'Noto Sans Georgian',system-ui,sans-serif;color:var(--deep)}
.chrome{background:#E8EBF0;padding:9px 14px;display:flex;align-items:center;gap:10px}
.chrome i{width:12px;height:12px;border-radius:50%;display:inline-block}
.chrome .url{flex:1;background:#fff;border-radius:7px;padding:5px 12px;font:13px -apple-system,system-ui;color:#5b6474;margin-left:14px}
header{background:linear-gradient(180deg,var(--blue),var(--deep));color:#fff;padding:16px 28px 0}
.top{display:flex;align-items:center;gap:16px}
.brand{font-weight:900;font-size:21px;text-transform:uppercase;letter-spacing:.01em}
.brand span{font-weight:700;font-size:11px;letter-spacing:.14em;opacity:.65;margin-left:10px}
.me{margin-left:auto;display:flex;gap:10px;align-items:center;font-weight:700;font-size:13px}
.me .av{width:32px;height:32px;border-radius:50%;background:#fff;color:var(--blue);display:flex;align-items:center;justify-content:center;font-weight:900}
nav{display:flex;gap:2px;margin-top:14px}
nav a{color:#fff;opacity:.72;font-weight:700;font-size:12px;letter-spacing:.14em;text-transform:uppercase;padding:11px 14px;border-radius:10px 10px 0 0}
nav a.on{background:#fff;color:var(--blue);opacity:1}
nav a b{background:var(--red);color:#fff;border-radius:8px;padding:1px 6px;margin-left:6px;font-size:10px;letter-spacing:0}
main{padding:22px 28px 34px}
h1{font-weight:900;text-transform:uppercase;color:var(--blue);font-size:26px;line-height:1.1;margin:0}
h2{font-weight:800;color:var(--blue);font-size:17px;margin:0 0 10px}
.sub{color:var(--muted);margin:4px 0 0}
.bar{display:flex;align-items:flex-end;gap:16px;margin-bottom:18px}
.bar .r{margin-left:auto;display:flex;gap:8px;align-items:center}
.card{background:var(--grey);border-radius:14px;padding:16px}
.card.white{background:#fff;box-shadow:0 0 0 1.5px var(--line)}
.kan{display:grid;grid-template-columns:repeat(7,1fr);gap:10px;align-items:start}
.col{background:var(--grey);border-radius:12px;padding:10px 8px;min-height:430px}
.col .h{display:flex;justify-content:space-between;align-items:center;margin:0 2px 10px}
.col .h .lbl{font-size:9.5px;letter-spacing:.1em}
.col .h b{font:800 12px Montserrat;color:var(--blue);background:#fff;border-radius:10px;padding:1px 8px}
.lc{background:#fff;border-radius:10px;padding:9px 9px 8px;margin-bottom:8px;box-shadow:0 1px 2px rgba(2,31,74,.08);font-size:12px;line-height:1.35;
 border-left:4px solid transparent}
.lc.g{border-left-color:var(--ok)}.lc.y{border-left-color:#E8A317}.lc.r{border-left-color:var(--red)}
.lc .t{display:flex;justify-content:space-between;align-items:center;gap:4px;margin-bottom:3px}
.lc .t b{font-weight:800;color:var(--blue);font-size:12px;line-height:1.25}
.lc .o{font-weight:700}.lc .p{color:var(--muted);font-size:11.5px}
.lc .a{margin-top:5px;display:flex;justify-content:space-between;align-items:center;color:var(--muted);font-size:11px}
.lc.hl{box-shadow:0 0 0 2.5px var(--blue)}
.lc .timer{font-size:11px;padding:2px 7px}
.legend{display:flex;gap:14px;align-items:center;font-size:12px;color:var(--muted)}
.legend i{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:5px;vertical-align:-1px}
.overlay{position:absolute;inset:0;background:rgba(2,31,74,.45);display:flex;align-items:flex-start;justify-content:center;padding-top:110px}
.modal{background:#fff;border-radius:18px;width:760px;padding:22px 24px;box-shadow:0 20px 60px rgba(2,31,74,.35)}
table{border-collapse:collapse;width:100%}
th{text-align:left;padding:8px;border-bottom:2px solid var(--blue);font:700 10px Montserrat;letter-spacing:.14em;text-transform:uppercase;color:var(--muted)}
td{padding:11px 8px;border-bottom:1px solid var(--line);vertical-align:middle;font-size:13px}
tr.rec td{background:#F0F5FF}
.kv{display:grid;grid-template-columns:150px minmax(0,1fr);gap:6px 12px;font-size:13.5px}.kv span:nth-child(odd){color:var(--muted)}
.tl{display:flex;flex-direction:column;gap:0}
.tl .e{display:grid;grid-template-columns:70px 18px minmax(0,1fr);gap:8px;font-size:13px;padding-bottom:12px;position:relative}
.tl .e .d{color:var(--muted);font-weight:700;font-size:12px}
.tl .e .dot{width:12px;height:12px;border-radius:50%;background:var(--ok);margin-top:4px}
.tl .e .dot.now{background:var(--blue);box-shadow:0 0 0 4px #D6E2F5}.tl .e .dot.todo{background:#C9D1DE}
.btns{display:flex;gap:10px;flex-wrap:wrap}
.tgpost{background:#fff;border-radius:14px;box-shadow:0 1px 2px rgba(0,0,0,.15);padding:9px 12px;font:14px/1.42 -apple-system,system-ui;color:#000}
.tgpost .who{font-weight:600;color:#2f7fc9;font-size:13.5px;margin-bottom:2px}
.tgwrap{background:linear-gradient(165deg,#dbe9c2 0%,#c3dba6 45%,#a9cb8f 100%);border-radius:14px;padding:12px}
.tgtop{font:600 12px -apple-system,system-ui;color:#4A5A75;margin-bottom:8px}
.photos{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}
.photos div{position:relative}.photos img{width:100%;height:118px;object-fit:cover;border-radius:10px;display:block}
.photos .vid i{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);width:36px;height:36px;border-radius:50%;background:rgba(0,0,0,.55);
 color:#fff;font-style:normal;display:flex;align-items:center;justify-content:center}
.wm{position:relative}.wm::after{content:'HOMES IN GEORGIA';position:absolute;right:8px;bottom:6px;font:900 10px Montserrat;letter-spacing:.08em;color:rgba(255,255,255,.85);
 text-shadow:0 1px 2px rgba(0,0,0,.45)}
.chk{display:flex;flex-direction:column;gap:7px;font-size:13px}
.inp{border:1.5px solid #D5DBE6;border-radius:9px;padding:9px 10px;font-size:13.5px;background:#fff;color:var(--muted)}
"""


def desk(tab, body, url="crm.homesingeorgia.ge/leads", who="Нино", role="оператор", overlay=""):
    tabs = [("Заявки", "4"), ("Объекты", None), ("Проверка", "3"), ("Сделки", None), ("Агенты", None), ("Статистика", None)]
    nav = "".join(f'<a class="{"on" if t == tab else ""}">{t}{f"<b>{n}</b>" if n else ""}</a>' for t, n in tabs)
    return (f'<!doctype html><html lang="ru"><head><meta charset="utf-8"><style>{FONTS}{BRAND}{DESK}</style></head><body style="position:relative">'
            f'<div class="chrome"><i style="background:#FF5F57"></i><i style="background:#FEBC2E"></i><i style="background:#28C840"></i>'
            f'<span class="url">🔒 {url}</span></div>'
            f'<header><div class="top"><div class="brand">Homes in Georgia<span>CRM · макет</span></div>'
            f'<div class="me"><span>{who} · {role}</span><span class="av">{who[0]}</span></div></div><nav>{nav}</nav></header>'
            f'<main>{body}</main>{overlay}</body></html>')
