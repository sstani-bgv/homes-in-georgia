"""Render a recorded fake-Telegram conversation as a Telegram-like chat page (style of proof/render.py) and screenshot it."""
import html
import os
import re
import time

CSS = """*{box-sizing:border-box} body{margin:0;background:#c8dcb0;font:15px -apple-system,system-ui,sans-serif}
.top{position:sticky;top:0;background:#fff;padding:10px 14px;border-bottom:1px solid #ddd;z-index:2;display:flex;gap:12px;align-items:center}
.ava{width:40px;height:40px;border-radius:50%;background:linear-gradient(180deg,#032D6A,#021F4A);color:#fff;font-weight:900;
 display:flex;align-items:center;justify-content:center;font-size:13px}
.top b{display:block} .top small{color:#888}
.note{background:#fff8d6;font-size:12px;color:#665;padding:6px 14px;border-bottom:1px solid #e8dfae}
.chat{padding:10px 16px 30px;display:flex;flex-direction:column;gap:6px}
.msg{max-width:80%;padding:7px 10px 18px;border-radius:14px;position:relative;background:#fff;box-shadow:0 1px 1px rgba(0,0,0,.12);overflow-wrap:anywhere}
.msg.out{align-self:flex-end;background:#e2ffc7} .msg.in{align-self:flex-start}
.msg .who{font-size:12px;font-weight:700;color:#2a7ab8;margin-bottom:2px}
.msg .time{position:absolute;right:9px;bottom:3px;font-size:11px;color:#8a9a8a}
.msg img.single{width:100%;max-width:320px;border-radius:10px;display:block;margin-bottom:6px}
.album{padding:2px;display:grid;gap:2px;grid-template-columns:1fr 1fr;width:62%;align-self:flex-start}
.album.out{align-self:flex-end} .album.n1{grid-template-columns:1fr}
.album img,.album video{width:100%;height:120px;object-fit:cover;border-radius:10px;display:block}
.album .vid{position:relative}.album .vid span{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);width:38px;height:38px;
border-radius:50%;background:rgba(0,0,0,.55);color:#fff;font-size:16px;display:flex;align-items:center;justify-content:center}
.album.n3 img:first-child{grid-column:span 2;height:160px}
.btns{display:flex;flex-direction:column;gap:3px;width:80%;max-width:80%;align-self:flex-start}
.brow{display:flex;gap:3px} .brow span{flex:1;text-align:center;background:rgba(60,90,60,.35);color:#fff;padding:7px;border-radius:10px;font-size:14px}
.kb{align-self:stretch;background:#f1f1f1;border-radius:10px;padding:6px;display:flex;flex-direction:column;gap:4px;margin:4px 0}
.kb span{display:block;text-align:center;background:#fff;border-radius:8px;padding:8px;font-size:14px;box-shadow:0 1px 1px rgba(0,0,0,.1)}
.svc{align-self:center;background:rgba(0,0,0,.28);color:#fff;font-size:12.5px;padding:4px 12px;border-radius:12px;max-width:86%;text-align:center}
.toast{align-self:center;background:rgba(2,31,74,.85);color:#fff;font-size:12.5px;padding:4px 12px;border-radius:8px}
.edited{font-size:11px;color:#8a9a8a;margin-right:4px} a,.link{color:#2a7ab8}
"""


def esc(t):
    return html.escape(t or "").replace("\n", "<br>")


def tg_html(t):
    """Telegram HTML parse_mode subset -> page HTML (b, i, a are kept; the rest of the text is already escaped by the bot)."""
    t = re.sub(r'<a href="([^"]*)">', r'<a href="\1">', t or "")
    return t.replace("\n", "<br>")


def buttons(markup):
    if not markup:
        return ""
    if "inline_keyboard" in markup:
        rows = "".join('<div class="brow">' + "".join(f"<span>{esc(b['text'])}{' ↗' if b.get('web_app') else ''}</span>" for b in r) + "</div>"
                       for r in markup["inline_keyboard"])
        return f'<div class="btns">{rows}</div>' if rows else ""
    if "keyboard" in markup:
        return '<div class="kb">' + "".join(f"<span>{esc(b['text'] if isinstance(b, dict) else b)}</span>"
                                            for r in markup["keyboard"] for b in r) + "</div>"
    return ""


def transcript(log, chat, since=0, until=None, users=None):
    """-> list of HTML blocks for one chat. Incoming (user) messages on the right, bot on the left; edits applied in place."""
    users = users or {}
    blocks, by_mid = [], {}
    presses = {x["cq"] for x in log if x["dir"] == "in" and x["kind"] == "press" and x.get("chat") == chat}
    for x in log:
        if x["seq"] <= since or (until and x["seq"] > until):
            continue
        if x["dir"] == "out" and x["method"] == "answerCallbackQuery" and x.get("callback_query_id") in presses and x.get("text"):
            blocks.append(f'<div class="toast">💬 {esc(x["text"])}</div>')
        if x.get("chat") != chat:
            continue
        tm = time.strftime("%H:%M", time.localtime(x["t"]))
        if x["dir"] == "in":
            who = f'<div class="who">{esc(x["from"])}</div>' if chat < 0 else ""
            if x["kind"] == "text":
                blocks.append(f'<div class="msg out">{who}{esc(x["text"])}<div class="time">{tm}</div></div>')
            elif x["kind"] == "photo":
                blocks.append(f'<div class="msg out">{who}<img class="single" src="file://{x["photo"]}">{esc(x.get("text"))}'
                              f'<div class="time">{tm}</div></div>')
            elif x["kind"] == "web_app_data":
                blocks.append(f'<div class="svc">🗺 Из Mini App (web_app_data): {esc(str(x["data"]))}</div>')
            elif x["kind"] == "svc":
                blocks.append(f'<div class="svc">{esc(x["text"])}</div>')
            elif x["kind"] == "press":
                blocks.append(f'<div class="svc">👆 {esc(x["from"])} нажал(а) «{esc(x["button"])}»</div>')
            continue
        m = x["method"]
        if m == "setChatMenuButton":
            mb = x.get("menu_button") or {}
            blocks.append(f'<div class="svc">☰ Кнопка меню чата: {esc(mb.get("text") or "по умолчанию")}'
                          + (f' → Mini App {esc(mb["web_app"]["url"])}' if mb.get("web_app") else "") + "</div>")
        elif m == "sendMessage":
            body = tg_html(x["text"]) if x.get("parse_mode") == "HTML" else esc(x["text"])
            blk = {"body": body, "markup": x.get("reply_markup"), "tm": tm, "edited": False}
            by_mid[x["message_id"]] = blk
            blocks.append(blk)
        elif m == "sendMediaGroup":
            imgs = "".join(f'<div class="vid"><video src="file://{p["file"]}#t=1" muted preload="auto"></video><span>▶</span></div>'
                           if p.get("type") == "video" else f'<img src="file://{p["file"]}">' for p in x["media"] if p.get("file"))
            blocks.append(f'<div class="album in n{len(x["media"])}">{imgs}</div>')
        elif m == "sendPhoto":
            blocks.append(f'<div class="msg in"><img class="single" src="file://{x["file"]}"><div class="time">{tm}</div></div>')
        elif m in ("editMessageText", "editMessageReplyMarkup"):
            blk = by_mid.get(x["message_id"])
            if blk:
                if m == "editMessageText":
                    blk["body"] = tg_html(x["text"]) if x.get("parse_mode") == "HTML" else esc(x["text"])
                blk["markup"] = x.get("reply_markup")
                blk["edited"] = True
    out = []
    for b in blocks:
        if isinstance(b, dict):
            ed = '<span class="edited">изменено</span>' if b["edited"] else ""
            out.append(f'<div class="msg in">{b["body"]}<div class="time">{ed}{b["tm"]}</div></div>' + buttons(b["markup"]))
        else:
            out.append(b)
    return out


def page(title, subtitle, note, blocks):
    return (f'<!doctype html><html lang="ru"><head><meta charset="utf-8"><style>{CSS}</style></head><body>'
            f'<div class="top"><div class="ava">HiG</div><div><b>{esc(title)}</b><small>{esc(subtitle)}</small></div></div>'
            + (f'<div class="note">{esc(note)}</div>' if note else "") + f'<div class="chat">{"".join(blocks)}</div></body></html>')


class Shooter:
    """One headless Chrome for all screenshots."""

    def __init__(self, tmp):
        from playwright.sync_api import sync_playwright
        self.tmp = tmp
        os.makedirs(tmp, exist_ok=True)
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.launch(channel="chrome", headless=True)

    def html(self, html_text, png, width=880):
        path = os.path.join(self.tmp, os.path.basename(png) + ".html")
        open(path, "w").write(html_text)
        pg = self.browser.new_page(viewport={"width": width, "height": 900})
        pg.goto("file://" + path)
        pg.wait_for_load_state("load")
        try:  # video thumbnails: wait for the first frames (system Chrome plays H.264)
            pg.wait_for_function("[...document.querySelectorAll('video')].every(v => v.readyState >= 2)", timeout=10000)
        except Exception:
            pass
        pg.screenshot(path=png, full_page=True)
        pg.close()
        return png

    def close(self):
        self.browser.close()
        self.pw.stop()


def chat_png(shooter, log, chat, png, title, subtitle, note=None, since=0, until=None):
    blocks = transcript(log, chat, since, until)
    return shooter.html(page(title, subtitle, note, blocks), png)


def table_png(shooter, png, title, subtitle, rows, note=None):
    """A check list (desc, ok, detail) rendered in the same frame, for data/system scenarios."""
    trs = "".join(f'<tr><td>{"ℹ️" if ok is None else "✅" if ok else "❌"}</td><td>{esc(d)}</td><td style="color:#4A5A75;font-size:12px">{esc(str(det or ""))[:400]}</td></tr>'
                  for d, ok, det in rows)
    body = (f'<div class="msg in" style="max-width:100%;align-self:stretch"><table style="border-collapse:collapse;width:100%">'
            f'<tr><th></th><th align=left>Проверка</th><th align=left>Детали</th></tr>{trs}</table></div>')
    return shooter.html(page(title, subtitle, note, [body]), png)
