"""Model calls. LLM_PROVIDER=openai (default, the only path on the server): any OpenAI-compatible
Chat Completions API (OpenRouter, OpenAI, ...) over httpx. LLM_PROVIDER=codex: local dev only, Codex CLI on the
ChatGPT subscription; no codex binary or login on the server, and nothing falls back to it.
Env (openai): OPENAI_BASE_URL (default https://api.openai.com/v1), OPENAI_API_KEY, LLM_MODEL (default gpt-4.1-mini)."""
import base64
import io
import json
import os
import subprocess
import sys
import tempfile
import time

MODEL = os.environ.get("CODEX_MODEL", "gpt-6.1-sol")


def _crm_keys():
    """Keys entered in the CRM (Управление → Ключи, backend app/keys.py) over .env; no backend on the path = .env only."""
    try:
        from app.keys import refresh
        refresh()
    except Exception:  # noqa: BLE001
        pass


def ask(prompt, schema, images=(), effort="low", timeout=300, retries=4, purpose="other"):
    """retries: API only. A client waiting in the chat passes 1: 4 retries back off ~30 s on a rate-limited model.
    purpose: what the call is for (PURPOSES), for Управление → Ключи → Расходы."""
    _crm_keys()
    if os.environ.get("LLM_PROVIDER", "openai").lower() == "codex":
        return ask_codex(prompt, schema, images, effort, timeout)
    return ask_openai(prompt, schema, images, timeout, retries=retries, purpose=purpose)


def ask_codex(prompt, schema, images=(), effort="low", timeout=300):
    # ponytail: one codex process per call (10-60s); switch to an API client if latency matters
    with tempfile.TemporaryDirectory() as d:
        schema_path, out_path = os.path.join(d, "schema.json"), os.path.join(d, "out.json")
        with open(schema_path, "w") as f:
            json.dump(schema, f)
        cmd = ["codex", "exec", "--ephemeral", "--skip-git-repo-check", "--ignore-rules",
               "-s", "read-only", "-C", d, "-m", MODEL, "-c", f'model_reasoning_effort="{effort}"',
               "--output-schema", schema_path, "-o", out_path, "-"]  # "-": prompt via stdin
        for img in images:  # -i is greedy, so images go last
            cmd += ["-i", img]
        r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0 or not os.path.exists(out_path):
            raise RuntimeError(f"codex failed ({r.returncode}): {r.stderr[-800:]}")
        with open(out_path) as f:
            return json.load(f)


def data_url(path, side=640):
    """Photo -> base64 jpeg data URL, downscaled like ingest.small(): image tokens are what cost money."""
    from PIL import Image
    im = Image.open(path).convert("RGB")
    im.thumbnail((side, side))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def ask_openai(prompt, schema, images=(), timeout=300, transport=None, retries=4, purpose="other"):
    import httpx
    base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("LLM_MODEL", "gpt-4.1-mini")
    content = [{"type": "text", "text": prompt}] + [
        {"type": "image_url", "image_url": {"url": data_url(p)}} for p in images]
    body = {"model": model, "messages": [{"role": "user", "content": content}],
            "response_format": {"type": "json_schema", "json_schema": {"name": "out", "strict": True, "schema": schema}}}
    if "openrouter.ai" in base:  # OpenRouter reports what the call cost in usage.cost
        body["usage"] = {"include": True}
    headers = {"Authorization": f"Bearer {os.environ.get('OPENAI_API_KEY', '')}"}
    with httpx.Client(timeout=timeout, transport=transport) as c:
        for attempt in range(retries + 1):
            t0 = time.time()
            try:
                r = c.post(f"{base}/chat/completions", json=body, headers=headers)
            except httpx.TransportError as e:  # timeouts and dropped connections are worth a retry
                err = repr(e)
            else:
                if r.status_code == 200:
                    d = r.json()
                    u = d.get("usage") or {}
                    print(f"llm {model} {purpose}: prompt={u.get('prompt_tokens')} completion={u.get('completion_tokens')} "
                          f"images={len(images)} {time.time() - t0:.1f}s", file=sys.stderr, flush=True)
                    record_usage(model, u.get("prompt_tokens") or 0, u.get("completion_tokens") or 0, u.get("cost"), purpose)
                    msg = d["choices"][0]["message"]
                    if msg.get("refusal"):
                        raise RuntimeError(f"llm refused: {msg['refusal']}")
                    return json.loads(msg["content"])
                if r.status_code != 429 and r.status_code < 500:
                    raise RuntimeError(f"llm failed ({r.status_code}): {r.text[:800]}")
                err = f"{r.status_code}: {r.text[:200]}"
            if attempt == retries:
                raise RuntimeError(f"llm failed after {retries + 1} attempts: {err}")
            print(f"llm retry {attempt + 1}: {err}", file=sys.stderr, flush=True)
            time.sleep(min(2 ** attempt * 2, 30))


# purpose -> who calls it. One client search = 1 search_query (cached per message set) + search_pick, maybe search_near
# and search_translate; avg $ per search = their sum / search_query calls (Расходы)
PURPOSES = ("search_query", "search_near", "search_pick", "search_translate", "index", "index_competitor", "index_facts", "contract_translate", "post_translate", "other")


def _add(row, prompt_tokens, completion_tokens, cost):
    """row: [calls, prompt, completion, cost_usd, unpriced_prompt, unpriced_completion] (old rows: the first 3)."""
    if len(row) < 6:
        row[3:] = [0.0, row[1], row[2]]  # an old row: all its tokens so far are unpriced
    row[0] += 1
    row[1] += prompt_tokens
    row[2] += completion_tokens
    if cost is not None:
        row[3] = round(row[3] + float(cost), 8)
    else:
        row[4] += prompt_tokens
        row[5] += completion_tokens


def record_usage(model, prompt_tokens, completion_tokens, cost=None, purpose="other"):
    """API tokens -> setting llm_usage {"YYYY-MM-DD": {model: row}} and llm_purpose {"YYYY-MM-DD": {purpose: {model: row}}}
    (row: see _add), 62 days (UTC), for Управление → Ключи → Расходы. cost: $ the provider reported (OpenRouter);
    without it the tokens go to the unpriced pair, which the CRM prices by the model's list price. Rows locked: the bot
    and the parser write at once. Never fails the call."""
    try:
        import db
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        day, keep = now.strftime("%Y-%m-%d"), (now - timedelta(days=62)).strftime("%Y-%m-%d")
        with db.tx() as c:
            for key in ("llm_usage", "llm_purpose"):
                c.execute("INSERT INTO settings (key, value) VALUES (?, '{}') ON CONFLICT (key) DO NOTHING", (key,))
                r = c.execute("SELECT value FROM settings WHERE key = ? FOR UPDATE", (key,)).fetchone()
                usage = {k: v for k, v in json.loads(r[0] or "{}").items() if k >= keep}
                rows = usage.setdefault(day, {})
                if key == "llm_purpose":
                    rows = rows.setdefault(purpose, {})
                _add(rows.setdefault(model, [0, 0, 0]), prompt_tokens, completion_tokens, cost)
                c.execute("UPDATE settings SET value = ? WHERE key = ?", (json.dumps(usage), key))
    except Exception as e:  # noqa: BLE001
        print(f"llm usage not recorded: {e}", file=sys.stderr, flush=True)


def obj(**props):
    """Strict JSON-schema object: every property required, no extras."""
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


if __name__ == "__main__":
    out = ask("What is shown in the image? Answer briefly.", obj(answer={"type": "string"}), images=sys.argv[1:])
    print(out)
    assert isinstance(out["answer"], str) and out["answer"]
