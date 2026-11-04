"""Hybrid search (search_mode=hybrid): filters and relaxation, the vector cache, ranking, the mode switch.
No Codex, no real model: llm.ask and the encoders are mocked. Run: python -m unittest discover -s test -v"""
import os
os.environ["ENV_FILE"] = os.devnull  # never read the real .env
import unittest

import numpy as np

import core
import db
import embed
from test_core import Base, item

D = 8


def unit(k, d=D):
    v = np.zeros(d, np.float32)
    v[k % d] = 1
    return v


def fake_img(paths):
    """photo 'raw/img/<n>_x.jpg' -> one-hot axis n: photos of the same listing are identical, others orthogonal."""
    return np.stack([unit(int(os.path.basename(p).split("_")[0])) for p in paths]) if paths else np.zeros((0, 1), np.float32)


def fake_txt(texts):
    return np.stack([unit(len(t)) for t in texts])


class FilterTest(unittest.TestCase):
    pool = [{"id": "a", "price_usd": 900, "bedrooms": 2, "district": "Ваке", "pets": "да"},
            {"id": "b", "price_usd": 1090, "bedrooms": 2, "district": "Ваке", "pets": "нет"},
            {"id": "c", "price_usd": 1200, "bedrooms": 2, "district": "Ваке", "pets": "не указано"},
            {"id": "d", "price_usd": 700, "bedrooms": 1, "district": "Сабуртало", "pets": "не указано"},
            {"id": "e", "price_usd": 800, "bedrooms": None, "district": "Диди Дигоми", "pets": "не указано"},
            {"id": "f", "price_usd": None, "bedrooms": 3, "district": "Ваке", "pets": "не указано"}]

    def ids(self, xs):
        return [i["id"] for i in xs]

    def test_hard_filter(self):
        q = {"max_price_usd": 1000, "bedrooms_min": 2, "bedrooms_max": 2, "districts": ["Ваке"], "pets": None}
        self.assertEqual(self.ids(core.hard_filter(self.pool, q)), ["a", "b"])  # +10% budget; f has 3 bedrooms
        self.assertEqual(self.ids(core.hard_filter(self.pool, {**q, "pets": True})), ["a"])  # explicit "нет" only
        self.assertEqual(self.ids(core.hard_filter(self.pool, {"districts": ["Дигоми"]})), ["e"])  # Диди Дигоми
        self.assertEqual(self.ids(core.hard_filter(self.pool, {"bedrooms_min": 2})), ["a", "b", "c", "e", "f"])  # unknown passes
        dist = {"a": 0.5, "b": 5.0, "d": 1.0}
        self.assertEqual(self.ids(core.hard_filter(self.pool, {}, dist, 2.0)), ["a", "d"])  # 1.5 x radius, no coords = out

    def test_relaxation_order_and_budget_kept(self):
        q = {"max_price_usd": 1000, "bedrooms_min": 2, "bedrooms_max": 2, "districts": ["Ваке"]}
        got, strict, drop = core.candidates(self.pool, q)
        self.assertEqual((strict, drop), (2, ("districts", "bedrooms")))
        self.assertEqual(self.ids(got), ["a", "b", "d", "e", "f"])  # c (1200 > 1100) never comes back
        got, strict, drop = core.candidates(self.pool, {"max_price_usd": 1000, "districts": ["Сабуртало"]})
        self.assertEqual((self.ids(got), drop), (["a", "b", "d", "e", "f"], ("districts",)))
        dist = {i["id"]: 9.0 for i in self.pool}  # everything far: radius is relaxed last
        got, strict, drop = core.candidates(self.pool, {}, dist, 2.0)
        self.assertEqual((strict, drop, len(got)), (0, ("districts", "bedrooms", "radius"), 6))
        got, _, drop = core.candidates(self.pool, {})  # no anchor: radius is not a relaxation step
        self.assertEqual(drop, ())


class VectorTest(Base):
    def make(self, n, **kw):
        os.makedirs(os.path.join(self.tmp.name, "img"), exist_ok=True)
        out = []
        for k in range(n):
            ps = []
            for m in range(2):
                p = os.path.join(self.tmp.name, "img", f"{k}_{m}.jpg")
                open(p, "w").close()
                ps.append(p)
            out.append({"id": f"ss{k}", "photos": ps, "looks": "x" * k, "style": "сканди", **kw})
        return out

    def test_incremental_cache(self):
        calls = []
        enc = lambda ps: (calls.append(len(ps)), fake_img(ps))[1]
        s = embed.build(self.make(3), None, enc, fake_txt)
        self.assertEqual((list(s["ids"]), s["embedded"], calls, len(s["photo"])), (["ss0", "ss1", "ss2"], 3, [6], 6))
        embed.save(s)
        self.assertTrue(embed.path().startswith(self.tmp.name))  # vectors live next to the (test) DB
        old = embed.read()
        items = self.make(4)[1:]  # ss0 gone, ss3 new
        items[0]["looks"] = "changed"  # ss1 profile changed -> re-embedded
        s = embed.build(items, old, enc, fake_txt)
        self.assertEqual((list(s["ids"]), s["embedded"], calls[-1]), (["ss1", "ss2", "ss3"], 2, 4))
        np.testing.assert_array_equal(s["img"][1], old["img"][2])  # ss2 reused as is
        self.assertEqual(list(s["owner"]), [0, 0, 1, 1, 2, 2])
        s2 = embed.build(items, s, enc, fake_txt)
        self.assertEqual(s2["embedded"], 0)
        embed.save(s2)
        v = embed.load()
        self.assertEqual(v["index"], {"ss1": 0, "ss2": 1, "ss3": 2})

    def test_rank_signals(self):
        s = embed.build(self.make(4), None, fake_img, fake_txt)
        s["index"] = {str(x): n for n, x in enumerate(s["ids"])}
        cands = [{"id": f"ss{k}", "impression": 3} for k in range(4)] + [{"id": "novec", "impression": 3}]

        class Emb:  # client photo looks like listing ss2; style text points at ss3's photo axis
            client_images = staticmethod(lambda ps: fake_img(["2_c.jpg"]))
            text = staticmethod(lambda t: unit(3))
        best = lambda **kw: max((r := core.rank(cands, **kw)), key=r.get)
        self.assertEqual(best(q={}, images=["c.jpg"], vec=s, emb=Emb), "ss2")
        self.assertEqual(best(q={"style": "bright"}, vec=s, emb=Emb), "ss3")
        self.assertEqual(best(q={}, liked=["ss1"], vec=s, emb=Emb), "ss1")
        r = core.rank(cands, q={}, disliked=["ss0"], vec=s, emb=Emb)
        self.assertEqual(min(r, key=r.get), "ss0")
        r = core.rank(cands, q={}, vec=None, dist={f"ss{k}": k for k in range(4)})  # no vectors: distance still ranks
        self.assertEqual(max(r, key=r.get), "ss0")
        for c in cands:
            c["price_usd"] = 800
        cands[0]["price_usd"] = None  # unknown price with a budget asked: still a candidate, but ranked last
        r = core.rank(cands, q={"max_price_usd": 900}, vec=None)
        self.assertEqual(min(r, key=r.get), "ss0")


class SwitchTest(Base):
    query = {"place": None, "label": None, "radius_km": None, "understood": "2 спальни в Ваке до $1000",
             "max_price_usd": 1000, "bedrooms_min": 2, "bedrooms_max": 2, "districts": ["Ваке"], "pets": None, "style": "bright"}

    def setUp(self):
        super().setUp()
        self.sync([item("ss1", impression=5), item("ss2", price_usd=900), item("ss3", price_usd=5000),
                   item("ss4", bedrooms=3), item("ss5", district="Сабуртало"), item("tg1", "agency", impression=1)])
        self.publish_objects("ss1", "ss2", "ss3", "ss4", "ss5")
        self.cl = core.client("tg", 42, name="Anna", lang_hint="ru")
        self.pick = ["ss3", "ss2", "ss1"]

    def fake_ask(self, prompt, schema, images=(), timeout=None, **kw):
        if "bedrooms_min" in schema["properties"]:
            self.calls.append(prompt)
            return dict(self.query)
        return super().fake_ask(prompt, schema, images, timeout, **kw)

    def test_default_is_llm(self):
        self.assertEqual(db.get_setting("search_mode"), "llm")
        core.add_input(self.cl, text="2 спальни в Ваке до 1000$")
        core.search(self.cl)
        self.assertIn("ss5 |", self.calls[-1])  # whole catalog went to the model
        self.assertNotIn("bedrooms_min", "".join(self.calls))

    def test_hybrid_final_llm(self):
        db.set_setting("search_mode", "hybrid")
        core.add_input(self.cl, text="2 спальни в Ваке до 1000$")
        res = core.search(self.cl)
        self.assertEqual(len(self.calls), 2)  # constraints + final pick
        self.assertIn("Сабуртало", self.calls[0])  # known districts are listed for the model
        final = self.calls[1]
        self.assertIn("ss1 |", final)
        self.assertNotIn("ss3 |", final)  # over budget never reaches the model, even relaxed
        self.assertIn("ослабленными условиями (район, число спален)", final)  # 3 strict < 5: ss5, then ss4 added
        # the model's picks first; topped up only with listings meeting every condition (tg1), not the relaxed ss4/ss5
        self.assertEqual([x["listing"]["id"] for x in res["items"]], ["ss2", "ss1", "tg1"])
        self.assertNotIn("@", res["items"][0]["reason"])
        self.assertEqual(res["total"], 3)  # ss1, ss2, tg1 fit strictly
        core.search(core.client("tg", 42))  # "more": cached constraints, no new extraction call
        self.assertEqual(sum("bedrooms_min" in c for c in self.calls), 1)
        self.assertEqual(core.client("tg", 42)["state"]["shown"][:3], ["ss2", "ss1", "tg1"])

    def test_hybrid_no_final_llm(self):
        db.set_setting("search_mode", "hybrid")
        db.set_setting("final_llm", "off")
        core.add_input(self.cl, text="2 спальни в Ваке до 1000$")
        res = core.search(self.cl)
        self.assertEqual(len(self.calls), 1)  # only the constraints call
        self.assertEqual([x["listing"]["id"] for x in res["items"]][:1], ["ss1"])  # best impression first (no vectors)
        self.assertEqual(set(x["listing"]["id"] for x in res["items"]), {"ss1", "ss2", "tg1", "ss4", "ss5"})
        self.assertEqual(res["understood"], "2 спальни в Ваке до $1000")
        en = core.template_reason({"style": "сканди", "light": "светлая", "renovation": "новый", "furniture": "новая"}, "en")
        self.assertEqual(en, "Scandinavian style, bright, new renovation, new furniture")

    def test_hybrid_photo_only_no_llm_call(self):
        db.set_setting("search_mode", "hybrid")
        db.set_setting("final_llm", "off")
        core.add_input(self.cl, image="/nonexistent.jpg")
        res = core.search(self.cl)
        self.assertEqual((len(self.calls), len(res["items"])), (0, 5))


if __name__ == "__main__":
    unittest.main()
