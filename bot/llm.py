"""Model calls. LLM_PROVIDER=codex (default): Codex CLI on the local subscription, no API keys.
LLM_PROVIDER=openai: any OpenAI-compatible Chat Completions API (e.g. ProxyAPI) over httpx, for the server.
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


def ask(prompt, schema, images=(), effort="low", timeout=300):
    if os.environ.get("LLM_PROVIDER", "codex").lower() == "openai":
        return ask_openai(prompt, schema, images, timeout)
    return ask_codex(prompt, schema, images, effort, timeout)


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


def ask_openai(prompt, schema, images=(), timeout=300, transport=None, retries=4):
    import httpx
    base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("LLM_MODEL", "gpt-4.1-mini")
    content = [{"type": "text", "text": prompt}] + [
        {"type": "image_url", "image_url": {"url": data_url(p)}} for p in images]
    body = {"model": model, "messages": [{"role": "user", "content": content}],
            "response_format": {"type": "json_schema", "json_schema": {"name": "out", "strict": True, "schema": schema}}}
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
                    print(f"llm {model}: prompt={u.get('prompt_tokens')} completion={u.get('completion_tokens')} "
                          f"images={len(images)} {time.time() - t0:.1f}s", file=sys.stderr, flush=True)
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


def obj(**props):
    """Strict JSON-schema object: every property required, no extras."""
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


if __name__ == "__main__":
    out = ask("What is shown in the image? Answer briefly.", obj(answer={"type": "string"}), images=sys.argv[1:])
    print(out)
    assert isinstance(out["answer"], str) and out["answer"]
