"""User journey mockups for Homes in Georgia (v4 = v1 screens + small fixes): renders steps to img/*.png and builds one
self-contained index.html with two funnel tabs and the answers form.
Run from the project root: .venv/bin/python docs/user-journey/build.py [--no-shots] [--no-previews] [STEP_ID ...]
Fake data only. Nothing is sent anywhere while building: headless Chrome opens local HTML files.
The page is served by admin.py at /journey; its answers form posts JSON to /journey/answers (same origin)."""
import base64
import html
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from PIL import Image  # noqa: E402

from screens import QUESTIONS, STEPS  # noqa: E402
from ui import BRAND, FONTS, fontface  # noqa: E402

IMG = os.path.join(HERE, "img")
TMP = "/tmp/user-journey-html"
SLUG = {"A": "client", "B": "operator", "C": "agent"}

# ---------------------------------------------------------------- overview: two swimlane diagrams

F1 = (["Бот", "Агент", "Оператор"], [
    (0, "Новые объявления", "ss.ge · myhome · Facebook · каналы. ИИ: собственник, агент, дубль", None),
    (1, "Беру в работу", "объект закреплён, у других заблокирован", None),
    (1, "Звонок", "дозвонился / нет", None),
    (1, "Собственник или агент", "агент → в базу с пометкой", None),
    (1, "Съёмка назначена", "проверка дубля: «добавлено» / «уже есть в базе»", None),
    (1, "Фото и видео", "загрузка в панели, чек-лист комнат", None),
    (2, "Проверка", "одобрить / вернуть агенту, без проверки не публикуем", "до 1 ч ?"),
    (0, "Публикация", "ваш генератор постов: водяной знак, канал, обложки → «Добавлен в базу»", None),
])

F2 = (["Клиент", "Оператор", "Агент"], [
    (0, "Анкета", "/start, можно несколько ответов, фото по желанию", None),
    (0, "Подбор · ❤️", "карточки как в канале → заявка", None),
    (1, "Назначить агента", "общая группа: кто первый нажал, тот ведёт", "5 мин"),
    (2, "Время у собственника", "без контактов клиента", "30 мин"),
    (1, "Время клиенту", "бот отправляет кнопки", "1 ч"),
    (0, "Выбор времени", "одним нажатием", None),
    (2, "Контакты агенту", "бот передаёт сам сразу после выбора времени", None),
    (2, "Просмотр", "напоминание обоим за 1 ч", None),
    ((0, 2), "Итог", "сдано / не подошло / думает", None),
    (1, "Сделка", "комиссия по уровню, #Rented и T0 как сейчас в приложении", None),
    ((0, 2), "T14 · договор", "оценка через 14 дней, конец договора за 30/7/1 дн.", None),
])

OV_CSS = """.ov{display:grid;gap:0;border-radius:16px;overflow:hidden;background:#fff;box-shadow:0 0 0 1.5px var(--line)}
.ov .lane{font:700 10px Montserrat;letter-spacing:.14em;text-transform:uppercase;color:#fff;background:var(--deep);display:flex;align-items:center;
 padding:0 12px;writing-mode:horizontal-tb}
.ov .cell{padding:8px 4px;border-bottom:1px solid var(--line);position:relative;min-height:96px;display:flex;align-items:center}
.ov .cell.alt,.ov .lane.alt{background-color:#F8F9FC}.ov .lane.alt{background:var(--blue)}
.ov .cell::before{content:'';position:absolute;left:0;right:0;top:50%;border-top:1px dashed #D5DBE6}
.ov .box{position:relative;z-index:1;background:#fff;border:1.5px solid var(--blue);border-radius:10px;padding:7px 8px 8px;width:100%;font-size:11px;line-height:1.3;
 color:var(--muted);hyphens:auto;-webkit-hyphens:auto;overflow-wrap:break-word}
.ov .box b{display:block;color:var(--blue);font-weight:800;font-size:12px;margin-bottom:2px}
.ov .box .n{position:absolute;top:-9px;left:7px;background:var(--blue);color:#fff;font:800 10px Montserrat;border-radius:9px;padding:1px 6px}
.ov .box .sla{position:absolute;top:-9px;right:6px;background:var(--red);color:#fff;font:800 10px Montserrat;border-radius:9px;padding:1px 6px}
.ov .box.shared{border-style:dashed}
.ov .hd{background:var(--grey);font:700 10px Montserrat;letter-spacing:.12em;color:var(--muted);text-align:center;padding:8px 0}
"""


def swimlanes(spec):
    lanes, steps = spec
    n = len(steps)
    out = [f'<div class="ov" style="grid-template-columns:96px repeat({n},minmax(0,1fr))"><div class="hd"></div>']
    out += [f'<div class="hd">{"›" if k else ""} {k + 1}</div>' for k in range(n)]
    for li, lane in enumerate(lanes):
        alt = " alt" if li % 2 else ""
        out.append(f'<div class="lane{alt}">{lane}</div>')
        for k, (where, title, note, sla) in enumerate(steps):
            hit = li in where if isinstance(where, tuple) else li == where
            box = ""
            if hit:
                box = (f'<div class="box{" shared" if isinstance(where, tuple) else ""}"><span class="n">{k + 1}</span>'
                       + (f'<span class="sla">⏱ {sla}</span>' if sla else "") + f'<b>{title}</b>{note}</div>')
            out.append(f'<div class="cell{alt}">{box}</div>')
    out.append("</div>")
    return "".join(out)


LEGEND = ('<p class="legend"><span class="sla-l">⏱ 5 мин</span> срок шага, после него напоминание и эскалация · '
          'пунктирная рамка: вопрос задаём и клиенту, и агенту</p>')


def overview_html():
    return ('<h3 class="ovh">Воронка 1 · Объекты: от объявления до публикации</h3>' + swimlanes(F1)
            + '<h3 class="ovh">Воронка 2 · Клиенты: от анкеты до сделки и T14</h3>' + swimlanes(F2) + LEGEND)


# ---------------------------------------------------------------- screenshots

def png_names(s):
    """img file names of a step: A2b_client.png, or A3-1_client.png, A3-2_client.png for before / after."""
    slug = SLUG[s["role"]]
    if len(s["shots"]) == 1:
        return [f'{s["id"]}_{slug}.png']
    return [f'{s["id"]}-{k + 1}_{slug}.png' for k in range(len(s["shots"]))]


def shoot(steps, overview=True):
    from playwright.sync_api import sync_playwright
    os.makedirs(IMG, exist_ok=True)
    os.makedirs(TMP, exist_ok=True)
    with sync_playwright() as pw:
        br = pw.chromium.launch(channel="chrome", headless=True)
        for s in steps:
            w = 1280 if s["kind"] == "desk" else 390
            for png, (page, _) in zip(png_names(s), s["shots"]):
                path = os.path.join(TMP, png.replace(".png", ".html"))
                open(path, "w", encoding="utf-8").write(page)
                pg = br.new_page(viewport={"width": w, "height": 820 if s["kind"] == "desk" else 844}, device_scale_factor=2)
                pg.route("**/*", lambda r: r.continue_() if r.request.url.startswith(("file:", "data:")) else r.abort())
                pg.goto("file://" + path, wait_until="load")
                pg.evaluate("document.fonts.ready")
                pg.wait_for_timeout(150)
                pg.screenshot(path=os.path.join(IMG, png), full_page=True)
                pg.close()
                print(os.path.join(IMG, png))
        if overview:  # the overview diagram as a picture too
            page = (f'<!doctype html><html lang="ru"><head><meta charset="utf-8"><style>{FONTS}{BRAND}{OV_CSS}'
                    'body{margin:0;padding:24px;width:1400px;font:14px Montserrat,sans-serif;color:#021F4A}'
                    '.ovh{font-weight:900;text-transform:uppercase;color:#032D6A;margin:18px 0 12px;font-size:17px}'
                    '.legend{color:#4A5A75;font-size:12px}.sla-l{background:#E3172D;color:#fff;font-weight:800;border-radius:9px;padding:1px 6px;font-size:10px}'
                    f'</style></head><body>{overview_html()}</body></html>')
            path = os.path.join(TMP, "D1.html")
            open(path, "w", encoding="utf-8").write(page)
            pg = br.new_page(viewport={"width": 1448, "height": 800}, device_scale_factor=2)
            pg.goto("file://" + path, wait_until="load")
            pg.evaluate("document.fonts.ready")
            pg.screenshot(path=os.path.join(IMG, "D1_overview.png"), full_page=True)
            pg.close()
        br.close()


# ---------------------------------------------------------------- index.html

def jpeg_b64(png, max_w):
    im = Image.open(png).convert("RGB")
    if im.width > max_w:
        im = im.resize((max_w, round(im.height * max_w / im.width)), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85, optimize=True, progressive=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def font_b64(name):
    return "data:font/woff2;base64," + base64.b64encode(open(os.path.join(HERE, "fonts", name), "rb").read()).decode()


def e(s):
    return html.escape(s, quote=True)


# funnel -> (tab title, who, tab line, intro, [(role, section title, where, lead, step ids)])
FUNNELS = {
    1: ("Воронка объектов", "агенты", "Объявление → звонок → съёмка → проверка → публикация в канале",
        "Воронка объектов наполняет канал и базу. Откуда объявления: парсер сам собирает их с ss.ge, myhome.ge, из Facebook и Telegram-каналов. "
        "Агент берёт объект, звонит, снимает, оператор проверяет, и только потом объект публикуется.",
        [("C", "Агент", "Telegram + «Панель» (мини-приложение)",
          "Агент Георгий находит и снимает объекты: от нового объявления до публикации в канале.",
          ["C1", "C2a", "C2b", "C3", "C4", "C5"]),
         ("B", "Оператор", "панель на компьютере",
          "Оператор Нино проверяет объект и публикует его. Без проверки в канал ничего не уходит.", ["B7a", "B7b"])]),
    2: ("Воронка клиентов", "сделки", "Анкета → ❤️ → оператор → агент → просмотр → сделка → T14",
        "Воронка клиентов превращает «нравится» в сделку. Оператор в середине, у каждого шага свой срок.",
        [("A", "Клиент", "Telegram-бот",
          "Клиент Анна ищет квартиру: анкета, подбор, выбор времени просмотра, оценка через 14 дней.",
          ["A1a", "A1b", "A1c", "A2a", "A2b", "A3", "A4a", "A4b", "A5a", "A5b", "A6"]),
         ("B", "Оператор", "Telegram + панель на компьютере",
          "Оператор Нино в середине процесса: берёт заявки в общей группе, назначает агента, следит за сроками и закрывает сделки.",
          ["B1", "B2", "B3", "B4", "B5", "B6"]),
         ("C", "Агент", "Telegram + «Панель» (мини-приложение)",
          "Агент Георгий показывает квартиру и оформляет сделку.", ["C6", "C7", "C8", "C9a", "C9b", "C9c"])]),
}

PAGE_CSS = """body{margin:0;background:#fff;font:400 16px/1.55 Montserrat,system-ui,sans-serif;color:var(--deep)}
.hero{background:linear-gradient(180deg,var(--blue),var(--deep));color:#fff;padding:56px 0 0}
.w{max-width:1200px;margin:0 auto;padding:0 32px}
.hero .brand{font-weight:900;text-transform:uppercase;font-size:15px;letter-spacing:.02em;display:flex;gap:10px;align-items:center}
.hero h1{font-weight:900;text-transform:uppercase;font-size:46px;line-height:1.05;margin:26px 0 16px;max-width:900px}
.hero p{font-size:18px;max-width:780px;opacity:.88;margin:0 0 10px}
.ftabs{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:34px}
.ftabs button{all:unset;cursor:pointer;background:rgba(255,255,255,.08);border-radius:18px 18px 0 0;padding:20px 24px 22px;color:#fff;
 box-shadow:inset 0 0 0 1px rgba(255,255,255,.16);transition:background .15s}
.ftabs button:hover{background:rgba(255,255,255,.16)}
.ftabs button b{display:block;font-weight:900;text-transform:uppercase;font-size:24px;line-height:1.1}
.ftabs button b i{font-style:normal;font-weight:700;opacity:.75}
.ftabs button span{display:block;font-size:13.5px;opacity:.8;margin-top:6px}
.ftabs button.on{background:#fff;color:var(--blue);box-shadow:none}
.ftabs button.on span{opacity:1;color:var(--muted)}
.ftabs button span.num{display:inline-block;font:800 11px Montserrat;letter-spacing:.12em;background:var(--red);color:#fff;border-radius:4px;
 padding:2px 7px;margin:0 0 8px;opacity:1}
.ftabs button.on span.num{color:#fff}
.mini{position:fixed;top:0;left:0;right:0;z-index:30;background:var(--deep);display:none;box-shadow:0 4px 20px rgba(2,31,74,.25)}
.mini.on{display:block}
.mini .w{display:flex;gap:8px;padding-top:8px;padding-bottom:8px;align-items:center}
.mini button{all:unset;cursor:pointer;color:#fff;font:800 12px Montserrat;letter-spacing:.08em;text-transform:uppercase;padding:9px 14px;border-radius:9px;opacity:.75}
.mini button.on{background:#fff;color:var(--blue);opacity:1}
.mini a{margin-left:auto;color:#fff;font:700 12px Montserrat;text-decoration:none;opacity:.8}
.funnel{display:none}.funnel.on{display:block}
section{padding:54px 0 10px}
.roles{display:flex;gap:10px;flex-wrap:wrap;margin:6px 0 4px}
.roles a{background:var(--grey);border-radius:12px;padding:10px 14px;color:var(--blue);text-decoration:none;font-weight:800;font-size:14px}
.roles a i{font-style:normal;font:700 10px Montserrat;letter-spacing:.14em;color:#fff;background:var(--red);border-radius:4px;padding:2px 6px;margin-right:6px}
h2.sec{font-weight:900;text-transform:uppercase;color:var(--blue);font-size:34px;line-height:1.1;margin:8px 0 6px}
h2.role{font-weight:900;text-transform:uppercase;color:var(--blue);font-size:28px;line-height:1.1;margin:50px 0 6px;padding-top:22px;border-top:2px solid var(--blue)}
.lead{color:var(--muted);font-size:17px;max-width:820px;margin:0 0 26px}
.ovh{font-weight:900;text-transform:uppercase;color:var(--blue);margin:26px 0 12px;font-size:17px}
.legend{color:var(--muted);font-size:13px}.sla-l{background:var(--red);color:#fff;font-weight:800;border-radius:9px;padding:1px 6px;font-size:10px}
.st{display:grid;grid-template-columns:380px minmax(0,1fr);gap:40px;align-items:start;padding:30px 0;border-bottom:1px solid var(--line)}
.st.two{grid-template-columns:740px minmax(0,1fr)}
.st.desk{grid-template-columns:1fr}
.st .shot{display:flex;gap:20px;align-items:flex-start}
.st .shot figure{margin:0;flex:1 1 0;min-width:0}
.st .shot img{display:block;width:100%;border-radius:22px;box-shadow:0 10px 40px rgba(2,31,74,.18)}
.st .shot figcaption{font:700 11px Montserrat;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);margin:10px 4px 0}
.st.desk .shot img{border-radius:12px}
.st .txt{position:sticky;top:70px}
.st.desk .txt{position:static;display:grid;grid-template-columns:1fr 1fr 1fr;gap:18px}
.code{display:inline-block;text-transform:none;font:800 12px Montserrat;color:#fff;background:var(--blue);border-radius:6px;padding:3px 8px;margin-right:8px}
.st h4{font-weight:900;color:var(--blue);font-size:22px;line-height:1.2;margin:10px 0 14px;text-transform:uppercase}
.st.desk h4,.st.desk .mark{grid-column:1/-1;margin:0}
.st p{margin:0 0 14px}
.auto{background:var(--grey);border-radius:12px;padding:12px 14px;font-size:14px;color:var(--muted);margin-bottom:12px}
.auto .lbl,.ask .lbl{display:block;margin-bottom:4px}
.mark{background:var(--okbg);color:var(--ok);border-radius:12px;padding:11px 14px;font-weight:800;font-size:14.5px;margin-bottom:12px}
.ask{border-radius:12px;padding:12px 14px;font-size:14px;box-shadow:inset 0 0 0 1.5px var(--red);color:var(--deep);margin-bottom:12px;background:#fff}
.ask .lbl{color:var(--red)}
.ask .qt{font-weight:700;margin-bottom:8px}
.what .lbl{display:block;margin-bottom:4px}
.opts{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px}
.opts label{display:inline-flex;align-items:center;gap:6px;background:var(--grey);border-radius:20px;padding:6px 11px;font-size:13px;cursor:pointer;user-select:none}
.opts input{accent-color:var(--blue);margin:0}
.opts label:has(input:checked){background:var(--blue);color:#fff}
textarea,.nm{box-sizing:border-box;width:100%;font:14px/1.45 Montserrat,system-ui,sans-serif;border:1.5px solid #D5DBE6;border-radius:9px;padding:9px 10px;
 color:var(--deep);background:#fff;resize:vertical}
textarea:focus,.nm:focus{outline:none;border-color:var(--blue)}
.qs{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin:10px 0 18px}
.qs .ask{margin:0;padding:16px 18px}
.qs .go{font-size:12px;color:var(--muted);margin-top:6px}.qs .go a{color:var(--blue)}
.send{display:flex;gap:14px;align-items:center;flex-wrap:wrap;margin:6px 0 40px}
.send button{all:unset;cursor:pointer;font:700 13px Montserrat;letter-spacing:.12em;text-transform:uppercase;background:var(--blue);color:#fff;border-radius:12px;padding:16px 22px}
.send button:disabled{opacity:.5;cursor:default}
.st2{font-weight:700}.st2.ok{color:var(--ok)}.st2.bad{color:var(--red)}
.who{max-width:380px;margin:0 0 14px}
.draft{font-size:12px;color:var(--muted)}
#Q{background:var(--grey);margin-top:50px;padding:46px 0 10px}
#Q .ask{box-shadow:none}
footer{background:var(--deep);color:#fff;padding:30px 0;font-size:13px;opacity:.95}
"""

JS = """
const FUN=['f1','f2'];
function show(id,scroll){FUN.forEach(f=>{document.getElementById(f).classList.toggle('on',f===id);
 document.querySelectorAll('[data-f="'+f+'"]').forEach(b=>b.classList.toggle('on',f===id))});
 if(scroll){const t=document.getElementById(id);window.scrollTo({top:t.getBoundingClientRect().top+scrollY-60})}}
document.querySelectorAll('button[data-f]').forEach(b=>b.onclick=()=>{show(b.dataset.f,true);history.replaceState(null,'','#'+b.dataset.f)});
const tabs=document.querySelector('.ftabs'),mini=document.querySelector('.mini');
window.addEventListener('scroll',()=>mini.classList.toggle('on',tabs.getBoundingClientRect().bottom<0),{passive:true});
function go(id){const el=document.getElementById(id);if(!el)return false;const f=el.closest('.funnel');if(f&&!f.classList.contains('on'))show(f.id,false);
 window.scrollTo({top:el.getBoundingClientRect().top+scrollY-60});return true}
window.addEventListener('hashchange',()=>go(location.hash.slice(1)));
if(location.hash)setTimeout(()=>go(location.hash.slice(1)),0);
document.querySelectorAll('a[href^="#"]').forEach(a=>a.addEventListener('click',ev=>{if(go(a.getAttribute('href').slice(1))){ev.preventDefault();
 history.replaceState(null,'',a.getAttribute('href'))}}));
// ---- answers: a question appears next to its screen and again in "Все вопросы"; both copies stay in sync
const KEY='hig-journey-v3';
let state={};try{state=JSON.parse(localStorage.getItem(KEY)||'{}')}catch(e){state={}}
function save(){try{localStorage.setItem(KEY,JSON.stringify(state));document.querySelectorAll('.draft').forEach(d=>d.textContent='Черновик сохранён в этом браузере')}catch(e){}}
function paint(){document.querySelectorAll('.ask[data-q]').forEach(box=>{const s=state[box.dataset.q]||{};
 box.querySelectorAll('input[type=radio]').forEach(r=>r.checked=(r.value===s.opt));
 const ta=box.querySelector('textarea');if(document.activeElement!==ta)ta.value=s.text||''});
 const nm=document.getElementById('jname');if(document.activeElement!==nm)nm.value=state._name||''}
document.querySelectorAll('.ask[data-q]').forEach(box=>{const q=box.dataset.q;
 box.querySelectorAll('label').forEach(l=>{const r=l.querySelector('input');
  l.addEventListener('click',ev=>{ev.preventDefault();const s=state[q]=state[q]||{};s.opt=(s.opt===r.value)?'':r.value;save();paint()})});
 const ta=box.querySelector('textarea');ta.addEventListener('input',()=>{state[q]=Object.assign(state[q]||{},{text:ta.value});save();paint()})});
document.getElementById('jname').addEventListener('input',ev=>{state._name=ev.target.value;save()});
paint();
document.getElementById('jsend').onclick=async()=>{const btn=document.getElementById('jsend'),out=document.getElementById('jstatus');
 const answers=QUESTIONS.map(([id,question])=>{const s=state[id]||{};return{id,question,answer:[s.opt,(s.text||'').trim()].filter(Boolean).join('. ')}});
 if(!answers.some(a=>a.answer)){out.className='st2 bad';out.textContent='Ответьте хотя бы на один вопрос';return}
 btn.disabled=true;out.className='st2';out.textContent='Отправляю…';
 try{const r=await fetch('/journey/answers',{method:'POST',headers:{'Content-Type':'application/json'},
   body:JSON.stringify({name:(state._name||'').trim(),answers})});
  const j=await r.json().catch(()=>({}));
  if(r.ok&&j.ok){out.className='st2 ok';out.textContent='Спасибо, ответы отправлены ✅'}
  else{out.className='st2 bad';out.textContent=j.error||('Не получилось отправить ('+r.status+'). Ответы сохранены, попробуйте ещё раз.')}}
 catch(e){out.className='st2 bad';out.textContent='Нет связи с сервером. Ответы сохранены в браузере, попробуйте позже.'}
 btn.disabled=false};
"""


def ask_html(qid, where, extra=""):
    title, q, opts = QUESTIONS[qid]
    radios = "".join(f'<label><input type="radio" name="{qid}_{where}" value="{e(o)}">{e(o)}</label>' for o in opts)
    return (f'<div class="ask" data-q="{qid}"><span class="lbl">❓ Подтвердите · {e(title)}</span><div class="qt">{e(q)}</div>'
            f'<div class="opts">{radios}</div><textarea rows="2" placeholder="Ваш ответ или комментарий"></textarea>{extra}</div>')


def step_html(s):
    desk = s["kind"] == "desk"
    figs = []
    for png, (_, cap) in zip(png_names(s), s["shots"]):
        src = jpeg_b64(os.path.join(IMG, png), 1800 if desk else 720)
        figs.append(f'<figure><img loading="lazy" alt="{s["id"]}" src="{src}">' + (f'<figcaption>{e(cap)}</figcaption>' if cap else "") + '</figure>')
    cls = " desk" if desk else (" two" if len(figs) > 1 else "")
    mark = f'<div class="mark">✅ {e(s["mark"])}</div>' if s["mark"] else ""
    asks = "".join(ask_html(q, s["id"]) for q in s["ask"])
    return (f'<div class="st{cls}" id="{s["id"]}"><div class="shot">{"".join(figs)}</div>'
            f'<div class="txt"><h4><span class="code">{s["id"]}</span>{s["title"]}</h4>{mark}'
            f'<div class="what"><span class="lbl">Что происходит</span><p>{s["what"]}</p></div>'
            f'<div class="auto"><span class="lbl">Что автоматизируем</span>{s["auto"]}</div>{asks}</div></div>')


def funnel_html(f):
    title, who, _, intro, sections = FUNNELS[f]
    by_id = {s["id"]: s for s in STEPS}
    out = [f'<section class="funnel{" on" if f == 1 else ""}" id="f{f}"><div class="w"><span class="tag red">Воронка {f}</span>'
           f'<h2 class="sec">{title} ({who})</h2><p class="lead">{intro}</p>'
           '<div class="roles">' + "".join(f'<a href="#f{f}{role}"><i>{role}</i>{name}</a>' for role, name, _, _, _ in sections) + '</div>'
           + ('<h3 class="ovh">Воронка 1 · Объекты: от объявления до публикации</h3>' + swimlanes(F1) if f == 1 else
              '<h3 class="ovh">Воронка 2 · Клиенты: от анкеты до сделки и T14</h3>' + swimlanes(F2)) + LEGEND]
    for role, name, where, lead, ids in sections:
        out.append(f'<h2 class="role" id="f{f}{role}"><span class="tag red">{role}</span> {name}</h2><p class="lead"><b>{where}.</b> {lead}</p>')
        out += [step_html(by_id[i]) for i in ids]
    out.append("</div></section>")
    return "".join(out)


def index():
    placed = [i for f in FUNNELS.values() for sec in f[4] for i in sec[4]]
    assert sorted(placed) == sorted(s["id"] for s in STEPS), "every step sits in exactly one funnel"
    where = {}
    for s in STEPS:
        for q in s["ask"]:
            where.setdefault(q, s["id"])
    assert len(QUESTIONS) <= 8 and set(where) == set(QUESTIONS), "every question sits next to a screen, at most 8"
    fonts = fontface(font_b64("cyr.woff2"), font_b64("lat.woff2"))
    parts = ['<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
             '<title>Homes in Georgia · путь клиента, оператора и агента</title>'
             f'<style>{fonts}{BRAND}{OV_CSS}{PAGE_CSS}</style></head><body>']
    tabs = "".join(f'<button data-f="f{f}" class="{"on" if f == 1 else ""}"><span class="num">ВОРОНКА {f}</span><b>{t} <i>({w})</i></b><span>{l}</span></button>'
                   for f, (t, w, l, _, _) in FUNNELS.items())
    mini = "".join(f'<button data-f="f{f}" class="{"on" if f == 1 else ""}">{t} ({w})</button>' for f, (t, w, _, _, _) in FUNNELS.items())
    parts.append(f'<div class="mini"><div class="w">{mini}<a href="#Q">Все вопросы ↓</a></div></div>')
    parts.append(
        '<div class="hero"><div class="w"><div class="brand">Homes in Georgia <span class="tag red">черновик на проверку</span></div>'
        '<h1>Как будет работать: путь клиента, оператора и агента</h1>'
        '<p>Давид, это макеты экранов: как я понял ваш процесс. Данные выдуманные (клиент Анна, агент Георгий, оператор Нино), '
        'телефоны и адреса ненастоящие.</p>'
        '<p>Выберите воронку ниже. Красным отмечены вопросы, где мне нужен ваш ответ: их можно заполнить прямо здесь, все вопросы собраны и внизу страницы. '
        'Зелёным отмечено то, что у вас уже есть.</p>'
        f'<div class="ftabs">{tabs}</div></div></div>')
    parts += [funnel_html(1), funnel_html(2)]
    qs = "".join(ask_html(q, "all", f'<div class="go">Экран <a href="#{where[q]}">{where[q]}</a></div>') for q in QUESTIONS)
    parts.append('<section id="Q"><div class="w"><span class="tag red">Вопросы</span><h2 class="sec">Все вопросы</h2>'
                 f'<p class="lead">Их {len(QUESTIONS)}. Это те же вопросы, что у экранов: ответ в одном месте сразу виден и в другом. '
                 'Можно выбрать готовый вариант, написать свой или и то и другое. Потом одна кнопка.</p>'
                 '<div class="who"><span class="lbl">Ваше имя (необязательно)</span><input id="jname" class="nm" placeholder="Например, Давид"></div>'
                 f'<div class="qs">{qs}</div>'
                 '<div class="send"><button id="jsend">Отправить ответы Станиславу</button><span id="jstatus" class="st2"></span>'
                 '<span class="draft"></span></div></div></section>')
    q_js = json.dumps([[q, f"{t}. {x}"] for q, (t, x, _) in QUESTIONS.items()], ensure_ascii=False)
    parts.append('<footer><div class="w">Homes in Georgia · макеты на выдуманных данных · 23.10.2026</div></footer>'
                 f'<script>const QUESTIONS={q_js};{JS}</script></body></html>')
    out = os.path.join(HERE, "index.html")
    open(out, "w", encoding="utf-8").write("".join(parts))
    print(out, f"{os.path.getsize(out) / 1e6:.1f} MB")


# ---------------------------------------------------------------- previews of the finished page

def previews():
    from playwright.sync_api import sync_playwright
    url = "file://" + os.path.join(HERE, "index.html")
    with sync_playwright() as pw:
        br = pw.chromium.launch(channel="chrome", headless=True)
        pg = br.new_page(viewport={"width": 1320, "height": 1000}, device_scale_factor=1)
        pg.route("**/*", lambda r: r.continue_() if r.request.url.startswith(("file:", "data:")) else r.abort())
        pg.goto(url, wait_until="load")
        pg.evaluate("document.querySelectorAll('img[loading]').forEach(i=>i.loading='eager')")
        pg.wait_for_timeout(800)

        def clip(js, name):
            pg.evaluate("window.scrollTo(0,0)")
            pg.wait_for_timeout(200)
            y, h = pg.evaluate(js)
            pg.screenshot(path=os.path.join(HERE, name), full_page=True, clip={"x": 0, "y": y, "width": 1320, "height": h})

        rect = "((sel,pad)=>{const r=document.querySelector(sel).getBoundingClientRect();return [r.top+scrollY-pad,r.height+2*pad]})"
        clip("(()=>[0,document.querySelector('#f1 .legend').getBoundingClientRect().bottom+scrollY+20])()", "preview_top.png")
        pg.evaluate("show('f2',false)")
        clip(f"{rect}('#A3',10)", "preview_A3.png")
        clip(f"{rect}('#B1',10)", "preview_B1.png")
        clip("(()=>{const r=document.getElementById('Q').getBoundingClientRect();return [r.top+scrollY,r.height]})()", "preview_form.png")
        br.close()
    print("previews done")


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--no-shots" not in args:
        only = [a for a in args if not a.startswith("--")]
        shoot([s for s in STEPS if not only or s["id"] in only], overview=not only or "D1" in only)
    index()
    if "--no-previews" not in args:
        previews()
