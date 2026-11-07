"""A minimal fake Telegram Bot API server for end-to-end tests.

The real bot.py talks to it via TG_API_BASE (python-telegram-bot base_url / base_file_url). The test driver injects
updates (messages, photos, web_app_data, callback queries, inline queries and presses on inline messages) and reads
every outgoing call the bot made. Nothing here touches the real Telegram API."""
import email
import itertools
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

JSON_KEYS = {"reply_markup", "media", "allowed_updates", "commands", "entities", "caption_entities", "link_preview_options", "menu_button",
             "results", "button"}
BOT_USER = {"id": 7000000001, "is_bot": True, "first_name": "Homes in Georgia AI", "username": "homes_in_georgia_test_bot"}


class FakeTelegram:
    def __init__(self, files_dir):
        self.files_dir = files_dir  # uploaded photos (sendMediaGroup/sendPhoto) and files clients "sent"
        os.makedirs(files_dir, exist_ok=True)
        self.lock = threading.Condition()
        self.updates = []           # pending updates for getUpdates
        self.update_id = itertools.count(1)
        self.seq = itertools.count(1)
        self.log = []               # every bot call and every injected update, in order: dicts with "seq"
        self.msg_ids = {}           # chat_id -> last message_id
        self.files = {}             # file_id -> local path (photos clients send)
        self.server = None
        self.polls = 0              # getUpdates calls: a (re)started bot is polling once this grows
        self.usernames = {}         # "@channel" chat ids the bot used -> fake numeric chat id

    # ---------- driver side ----------

    def next_msg_id(self, chat_id):
        with self.lock:
            self.msg_ids[chat_id] = self.msg_ids.get(chat_id, 0) + 1
            return self.msg_ids[chat_id]

    def push(self, kind, update, record):
        with self.lock:
            update["update_id"] = next(self.update_id)
            self.updates.append(update)
            self.log.append({"seq": next(self.seq), "dir": "in", "kind": kind, "t": time.time(), **record})
            self.lock.notify_all()

    @staticmethod
    def user(u):
        return {"id": u["id"], "is_bot": False, "first_name": u["name"], **({"username": u["username"]} if u.get("username") else {}),
                "language_code": u.get("lang", "ru")}

    @staticmethod
    def chat(u, group=None):
        if group:
            return {"id": group["id"], "type": "supergroup", "title": group["title"]}
        return {"id": u["id"], "type": "private", "first_name": u["name"], **({"username": u["username"]} if u.get("username") else {})}

    def message(self, u, group=None, **fields):
        chat = self.chat(u, group)
        mid = self.next_msg_id(chat["id"])
        return mid, {"message_id": mid, "date": int(time.time()), "chat": chat, "from": self.user(u), **fields}

    def send_text(self, u, text, group=None):
        ents = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}] if text.startswith("/") else []
        mid, m = self.message(u, group, text=text, **({"entities": ents} if ents else {}))
        self.push("text", {"message": m}, {"chat": m["chat"]["id"], "from": u["name"], "text": text, "message_id": mid})

    def send_topic_text(self, u, group, topic, text):
        """A member writes in a forum topic of a supergroup (message_thread_id)."""
        mid, m = self.message(u, group, text=text, message_thread_id=topic, is_topic_message=True)
        self.push("text", {"message": m}, {"chat": group["id"], "from": u["name"], "text": text, "message_id": mid})
        return mid

    def channel_post(self, channel, text=None, video=None, media_group_id=None):
        """A post in a channel the bot administers (a channel_post update; no `from`)."""
        mid = self.next_msg_id(channel["id"])
        m = {"message_id": mid, "date": int(time.time()), "sender_chat": {"id": channel["id"], "type": "channel", "title": channel["title"]},
             "chat": {"id": channel["id"], "type": "channel", "title": channel["title"], "username": channel["username"]},
             **({"text": text} if text and not video else {}), **({"caption": text} if text and video else {}),
             **({"video": {"file_id": video, "file_unique_id": "u" + video, "width": 1280, "height": 720, "duration": 10}} if video else {}),
             **({"media_group_id": media_group_id} if media_group_id else {})}
        self.push("channel_post", {"channel_post": m}, {"chat": channel["id"], "text": text, "message_id": mid})
        return mid

    def edit_text(self, u, message_id, text):
        """The user edits a message they sent before (an edited_message update)."""
        m = {"message_id": message_id, "date": int(time.time()), "edit_date": int(time.time()), "chat": self.chat(u),
             "from": self.user(u), "text": text}
        self.push("edit", {"edited_message": m}, {"chat": u["id"], "from": u["name"], "text": text, "message_id": message_id})

    def send_photo(self, u, path, caption=None):
        fid = f"photo{len(self.files) + 1}"
        self.files[fid] = path
        sizes = [{"file_id": fid, "file_unique_id": "u" + fid, "width": 1280, "height": 960, "file_size": os.path.getsize(path)}]
        mid, m = self.message(u, photo=sizes, **({"caption": caption} if caption else {}))
        self.push("photo", {"message": m}, {"chat": u["id"], "from": u["name"], "photo": path, "text": caption, "message_id": mid})

    def send_web_app_data(self, u, data, button_text="🗺 Выбрать место на карте"):
        mid, m = self.message(u, web_app_data={"data": json.dumps(data), "button_text": button_text})
        self.push("web_app_data", {"message": m}, {"chat": u["id"], "from": u["name"], "data": data, "message_id": mid})

    def press(self, u, out, button_text, group=None):
        """Press an inline button (by its text) on an outgoing bot message (a log entry)."""
        data = next(b["callback_data"] for row in self.markup(out) for b in row if b["text"] == button_text)
        chat = out["chat"]
        msg = {"message_id": out["message_id"], "date": int(time.time()), "from": BOT_USER,
               "chat": self.chat(u, group) if group or chat == u["id"] else {"id": chat, "type": "private", "first_name": "?"},
               "text": out.get("text") or "",
               **({"reply_markup": out["reply_markup"]} if out.get("reply_markup") else {})}  # as Telegram: the current keyboard
        cq = {"id": str(next(self.seq) * 1000), "from": self.user(u), "message": msg, "chat_instance": str(chat), "data": data}
        self.push("press", {"callback_query": cq}, {"chat": chat, "from": u["name"], "button": button_text, "ref": out["message_id"],
                                                    "cq": cq["id"]})

    def inline_query(self, u, query):
        """A user types «@bot <query>» (the operator's userbot does it for the slot card): an inline_query update."""
        iq = {"id": str(next(self.seq) * 1000), "from": self.user(u), "query": query, "offset": "", "chat_type": "private"}
        self.push("inline_query", {"inline_query": iq}, {"from": u["name"], "text": query, "iq": iq["id"]})
        return iq["id"]

    def inline_message(self, chat, result, via=BOT_USER):
        """The chosen inline result sent into `chat` by a user account («via @bot»): logged as an outgoing message with
        the result's text/keyboard and a fresh inline_message_id (that's all Telegram gives the bot about it)."""
        imid = f"im{next(self.seq)}"
        content = result.get("input_message_content") or {}
        with self.lock:
            self.log.append({"seq": next(self.seq), "dir": "out", "method": "viaBot", "chat": chat, "t": time.time(), "via": via["username"],
                             "inline_message_id": imid, "text": content.get("message_text", ""),
                             **({"reply_markup": result["reply_markup"]} if result.get("reply_markup") else {})})
        return imid

    def press_inline(self, u, out, button_text):
        """Press a button of an inline message (a viaBot log entry, or the bot's later edit of it): the callback has
        inline_message_id and no message."""
        data = next(b["callback_data"] for row in self.markup(out) for b in row if b["text"] == button_text)
        cq = {"id": str(next(self.seq) * 1000), "from": self.user(u), "inline_message_id": out["inline_message_id"],
              "chat_instance": "dm", "data": data}
        self.push("press", {"callback_query": cq}, {"chat": out.get("chat"), "from": u["name"], "button": button_text, "cq": cq["id"]})

    def userbot_text(self, chat, text):
        """A plain message the operator's account (userbot) sent into `chat`."""
        with self.lock:
            self.log.append({"seq": next(self.seq), "dir": "out", "method": "userbotText", "chat": chat, "text": text, "t": time.time()})

    @staticmethod
    def markup(out):
        rm = out.get("reply_markup") or {}
        return rm.get("inline_keyboard") or []

    def note(self, chat, text):
        """A service line in the rendered transcript (e.g. "bot restarted"); not sent to the bot."""
        with self.lock:
            self.log.append({"seq": next(self.seq), "dir": "in", "kind": "svc", "chat": chat, "text": text, "t": time.time()})

    def calls(self, chat=None, method=None, since=0):
        with self.lock:
            return [x for x in self.log if x["dir"] == "out" and x["seq"] > since
                    and (chat is None or x.get("chat") == chat) and (method is None or x["method"] == method)]

    def wait(self, pred, timeout=300, since=0, what="condition"):
        """Wait until pred(new outgoing calls since `since`) is truthy; returns its value."""
        end = time.time() + timeout
        while time.time() < end:
            v = pred(self.calls(since=since))
            if v:
                return v
            time.sleep(0.3)
        raise AssertionError(f"timeout waiting for {what}")

    def mark(self):
        with self.lock:
            return self.log[-1]["seq"] if self.log else 0

    # ---------- bot side (HTTP) ----------

    def api(self, method, p, files):
        if method == "getMe":
            return BOT_USER
        if method == "getUpdates":
            self.polls += 1
            offset = int(p.get("offset") or 0)
            with self.lock:
                self.updates = [u for u in self.updates if u["update_id"] >= offset]
                if not self.updates:
                    self.lock.wait(min(float(p.get("timeout") or 0), 1.0))
                return list(self.updates)
        if method == "getFile":
            path = self.files[p["file_id"]]
            return {"file_id": p["file_id"], "file_unique_id": "u" + p["file_id"], "file_size": os.path.getsize(path),
                    "file_path": f"photos/{p['file_id']}.jpg"}
        if method in ("deleteWebhook", "setMyCommands", "sendChatAction", "answerCallbackQuery", "answerInlineQuery", "deleteMessage",
                      "close", "logOut"):
            self.record(method, p, files)
            return True
        if str(p.get("chat_id", "")).startswith("@"):  # a public channel by @username: a stable fake numeric id
            uname = p["chat_id"][1:]
            p["chat_id"] = str(self.usernames.setdefault(uname, -1009000 - len(self.usernames)))
        chat = int(p["chat_id"]) if "chat_id" in p else None
        named = {"username": u for u, i in self.usernames.items() if i == chat}
        if method == "sendMediaGroup":
            out = []
            for m in p["media"]:
                mid = self.next_msg_id(chat)
                kind = {"video": {"file_id": f"fidv{mid}", "file_unique_id": f"v{mid}", "width": 640, "height": 360, "duration": 5}} \
                    if m.get("type") == "video" else {"photo": [{"file_id": f"out{mid}", "file_unique_id": f"o{mid}", "width": 640, "height": 480}]}
                out.append({"message_id": mid, "date": int(time.time()), "chat": {"id": chat, "type": "private", **named}, **kind})
            self.record(method, p, files, message_id=out[0]["message_id"], message_ids=[m["message_id"] for m in out])
            return out
        if method in ("sendMessage", "sendPhoto", "sendLocation", "sendDocument", "sendVideo"):
            mid = self.next_msg_id(chat)
            self.record(method, p, files, message_id=mid)
            return {"message_id": mid, "date": int(time.time()), "chat": {"id": chat, "type": "private", **named}, "from": BOT_USER,
                    **({"text": p["text"]} if "text" in p else {})}
        if method.startswith("edit") and p.get("inline_message_id"):  # an inline message: Telegram answers True
            self.record(method, p, files)
            return True
        if method.startswith("edit"):
            self.record(method, p, files, message_id=int(p.get("message_id") or 0))
            return {"message_id": int(p.get("message_id") or 0), "date": int(time.time()), "chat": {"id": chat, "type": "private"},
                    "text": p.get("text") or ""}
        self.record(method, p, files)
        return True

    def record(self, method, p, files, **extra):
        saved = {}
        for name, (fname, data) in files.items():
            ext = os.path.splitext(fname or "")[1].lower()
            ext = ext if ext in (".jpg", ".jpeg", ".png", ".webp", ".mp4", ".mov") else ".jpg"
            if method == "sendVideo" and ext not in (".mp4", ".mov"):
                ext = ".mp4"
            path = os.path.join(self.files_dir, f"{len(self.log)}_{name}{ext}")  # media-group videos renamed to .mp4 below
            with open(path, "wb") as f:
                f.write(data)
            saved[name] = path
        if method == "sendMediaGroup":
            for m in p["media"]:
                m["file"] = saved.get(m["media"].removeprefix("attach://"))
                if m["file"] and m.get("type") == "video" and m["file"].endswith(".jpg"):  # keep the extension: the gallery shows it as a video
                    os.replace(m["file"], m["file"].removesuffix(".jpg") + ".mp4")
                    m["file"] = m["file"].removesuffix(".jpg") + ".mp4"
        if method in ("sendPhoto", "sendVideo", "sendDocument"):  # the uploaded file (or None for a file_id / URL)
            p["file"] = saved.get({"sendPhoto": "photo", "sendVideo": "video", "sendDocument": "document"}[method])
        with self.lock:
            entry = {"seq": next(self.seq), "dir": "out", "method": method, "t": time.time(), **p, **extra}
            if "chat_id" in p:
                entry["chat"] = int(p["chat_id"])
            self.log.append(entry)

    def serve(self, port=0):
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                path = urlparse(self.path).path
                if path.startswith("/file/bot"):  # /file/bot<token>/photos/<file_id>.jpg
                    fid = path.rsplit("/", 1)[-1].removesuffix(".jpg")
                    data = open(fake.files[fid], "rb").read()
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                self.do_POST()

            def do_POST(self):
                url = urlparse(self.path)
                method = url.path.rsplit("/", 1)[-1]
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                ctype = self.headers.get("Content-Type") or ""
                params, files = {k: v[-1] for k, v in parse_qs(url.query).items()}, {}
                if ctype.startswith("multipart/"):
                    msg = email.message_from_bytes(b"Content-Type: " + ctype.encode() + b"\r\n\r\n" + body)
                    for part in msg.get_payload():
                        name = part.get_param("name", header="content-disposition")
                        if part.get_filename() is not None:
                            files[name] = (part.get_filename(), part.get_payload(decode=True))
                        else:
                            params[name] = part.get_payload(decode=True).decode()
                elif body:
                    params.update({k: v[-1] for k, v in parse_qs(body.decode()).items()})
                for k in list(params):
                    if k in JSON_KEYS:
                        params[k] = json.loads(params[k])
                try:
                    res = {"ok": True, "result": fake.api(method, params, files)}
                except Exception as ex:  # surface harness bugs instead of hanging the bot
                    res = {"ok": False, "error_code": 400, "description": f"fake server: {ex!r}"}
                data = json.dumps(res).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", port), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self.server.server_address[1]
