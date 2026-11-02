"""llm.py openai provider against a mocked HTTP transport (no network). Run: python -m unittest discover -s test -v"""
import base64
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

import httpx
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import llm  # noqa: E402

SCHEMA = llm.obj(answer={"type": "string"})


def ok(content, usage=None):
    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(content)}}],
                                     "usage": usage or {"prompt_tokens": 12, "completion_tokens": 3}})


class OpenAIProvider(unittest.TestCase):
    def setUp(self):
        env = {"LLM_PROVIDER": "openai", "OPENAI_BASE_URL": "https://proxy.test/openai/v1/", "OPENAI_API_KEY": "sk-test",
               "LLM_MODEL": "gpt-test"}
        p = mock.patch.dict(os.environ, env)
        p.start()
        self.addCleanup(p.stop)
        s = mock.patch.object(llm.time, "sleep")
        self.sleep = s.start()
        self.addCleanup(s.stop)

    def run_ask(self, handler, **kw):
        transport = httpx.MockTransport(handler)
        real = llm.ask_openai
        with mock.patch.object(llm, "ask_openai", lambda *a, **k: real(*a, transport=transport, **k)):
            return llm.ask("hi", SCHEMA, **kw)

    def test_request_shape_and_result(self):
        seen = []

        def handler(req):
            seen.append(req)
            return ok({"answer": "a flat"})

        d = tempfile.mkdtemp()
        img = os.path.join(d, "big.png")
        Image.new("RGB", (2000, 1000), "red").save(img)
        with mock.patch("sys.stderr", new=io.StringIO()) as err:
            out = self.run_ask(handler, images=[img])
        self.assertEqual(out, {"answer": "a flat"})
        req = seen[0]
        self.assertEqual(str(req.url), "https://proxy.test/openai/v1/chat/completions")
        self.assertEqual(req.headers["authorization"], "Bearer sk-test")
        body = json.loads(req.content)
        self.assertEqual(body["model"], "gpt-test")
        rf = body["response_format"]
        self.assertEqual(rf["type"], "json_schema")
        self.assertTrue(rf["json_schema"]["strict"])
        self.assertEqual(rf["json_schema"]["schema"], SCHEMA)
        text, image = body["messages"][0]["content"]
        self.assertEqual(text, {"type": "text", "text": "hi"})
        url = image["image_url"]["url"]
        self.assertTrue(url.startswith("data:image/jpeg;base64,"))
        im = Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1])))
        self.assertEqual((im.format, max(im.size)), ("JPEG", 640))
        self.assertIn("prompt=12 completion=3", err.getvalue())

    def test_retries_429_and_5xx_then_succeeds(self):
        codes = iter([429, 503])

        def handler(req):
            c = next(codes, 200)
            return ok({"answer": "x"}) if c == 200 else httpx.Response(c, text="busy")

        with mock.patch("sys.stderr", new=io.StringIO()):
            self.assertEqual(self.run_ask(handler), {"answer": "x"})
        self.assertEqual(self.sleep.call_count, 2)

    def test_timeout_is_retried(self):
        calls = []

        def handler(req):
            calls.append(1)
            if len(calls) == 1:
                raise httpx.ReadTimeout("slow", request=req)
            return ok({"answer": "y"})

        with mock.patch("sys.stderr", new=io.StringIO()):
            self.assertEqual(self.run_ask(handler), {"answer": "y"})

    def test_client_error_is_not_retried(self):
        calls = []

        def handler(req):
            calls.append(1)
            return httpx.Response(400, text="bad schema")

        with self.assertRaisesRegex(RuntimeError, "400"):
            self.run_ask(handler)
        self.assertEqual(len(calls), 1)

    def test_gives_up_after_retries(self):
        with mock.patch("sys.stderr", new=io.StringIO()), self.assertRaisesRegex(RuntimeError, "attempts"):
            self.run_ask(lambda req: httpx.Response(500, text="down"))
        self.assertEqual(self.sleep.call_count, 4)

    def test_codex_is_the_default(self):
        with mock.patch.dict(os.environ, {"LLM_PROVIDER": ""}), mock.patch.object(llm, "ask_codex", return_value={"answer": "c"}) as c:
            os.environ.pop("LLM_PROVIDER")
            self.assertEqual(llm.ask("hi", SCHEMA), {"answer": "c"})
        c.assert_called_once()


if __name__ == "__main__":
    unittest.main()
