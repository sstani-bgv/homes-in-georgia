"""Media storage (app/storage.py) and property embeddings (app/embeddings.py).
The S3 test runs only with TEST_S3_ENDPOINT/TEST_S3_BUCKET/TEST_S3_ACCESS_KEY/TEST_S3_SECRET_KEY (e.g. a local MinIO)."""
import io
import os
import uuid

import httpx
import pytest
from sqlalchemy import text

from app import embeddings, storage
from app.config import settings
from app.services import properties
from tests.conftest import auth, make_listing, make_user


def _property_with_media(db, files):
    ag = make_user(db, "agent")
    p = properties.take(db, make_listing(db, "ss77").id, ag)
    properties.call_result(db, p.id, ag, True)
    properties.owner_or_agent(db, p.id, ag, True, "Нана", "599 11 22 33")
    return ag, p, properties.upload_media(db, p.id, files, "kitchen", ag)


def test_local_backend(db, client):
    ag, p, (m,) = _property_with_media(db, [("a.jpg", b"\xff\xd8one")])
    assert storage.local_path(m.path).read_bytes() == b"\xff\xd8one"
    assert client.get(f"/api/media/{m.path}", headers=auth(ag)).content == b"\xff\xd8one"
    assert client.get(f"/api/media/{m.path}").status_code == 401
    assert storage.fetch(m.path, "/nonexistent") == str(storage.local_path(m.path))
    properties.delete_media(db, p.id, m.id, ag)
    assert not storage.local_path(m.path).exists() and storage.fetch(m.path, "/x") is None


def _jpeg(w=2000, h=1500):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (200, 30, 30)).save(buf, "JPEG")
    return buf.getvalue()


def test_previews_and_fallback(db, client, tmp_path):
    from PIL import Image
    ag, p, (m, bad) = _property_with_media(db, [("a.jpg", _jpeg()), ("b.jpg", b"\xff\xd8broken")])
    pv = storage.local_path(storage.preview_key(m.path))
    assert Image.open(pv).format == "WEBP" and max(Image.open(pv).size) == storage.PREVIEW_PX
    r = client.get(f"/api/media/{m.path}?p=1", headers=auth(ag))
    assert r.headers["content-type"] == "image/webp" and r.content == pv.read_bytes()
    assert client.get(f"/api/media/{m.path}", headers=auth(ag)).headers["content-type"] == "image/jpeg"
    # no preview possible -> the original
    assert client.get(f"/api/media/{bad.path}?p=1", headers=auth(ag)).content == b"\xff\xd8broken"
    card = client.get(f"/api/properties/{p.id}", headers=auth(ag)).json()
    assert card["media"][0]["preview"] == f"/api/media/{m.path}?p=1"

    # backfill re-makes a missing preview; deleting the photo deletes its preview
    pv.unlink()
    assert storage.previews(out=lambda *_: None)[0] >= 1 and pv.is_file()
    properties.delete_media(db, p.id, m.id, ag)
    assert not pv.exists()

    # listing photos: preview on the first ?p=1 request, the original without it
    f = tmp_path / "ss5_0.jpg"
    f.write_bytes(_jpeg(900, 600))
    make_listing(db, "ss5", photos=f'["{f}"]')
    assert client.get("/api/listing-photos/ss5/0").headers["content-type"] == "image/jpeg"
    r = client.get("/api/listing-photos/ss5/0?p=1")
    assert r.headers["content-type"] == "image/webp" and Image.open(io.BytesIO(r.content)).size == (640, 427)


def test_embed_property_stores_vectors(db):
    class Fake:
        encode_texts = staticmethod(lambda ts: [[1.0] + [0.0] * (embeddings.DIM - 1) for _ in ts])
        encode_images = staticmethod(lambda ps: [[0.0, 3.0] + [0.0] * (embeddings.DIM - 2) for _ in ps])
    _, p, _ = _property_with_media(db, [("a.jpg", b"\xff\xd8one"), ("b.jpg", b"\xff\xd8two")])
    assert embeddings.embed_property(db, p, Fake)
    t, ph = db.execute(text("SELECT text_embedding::text, photo_embedding::text FROM properties WHERE id = :i"),
                       {"i": p.id}).one()
    assert t.startswith("[1,0,") and ph.startswith("[0,1,0")  # photo = normalized mean
    near = db.scalar(text("SELECT id FROM properties ORDER BY photo_embedding <=> CAST(:v AS vector) LIMIT 1"),
                     {"v": embeddings._vec([0.0, 1.0] + [0.0] * (embeddings.DIM - 2))})
    assert near == p.id


S3 = {k: os.environ.get(f"TEST_{k}", "") for k in ("S3_ENDPOINT", "S3_BUCKET", "S3_ACCESS_KEY", "S3_SECRET_KEY")}


@pytest.mark.skipif(not all(S3.values()), reason="TEST_S3_* not set (local MinIO)")
def test_s3_backend(db, client, monkeypatch, tmp_path):
    for k, v in {**S3, "MEDIA_STORAGE": "s3", "S3_REGION": "us-east-1", "S3_PUBLIC_URL": ""}.items():
        monkeypatch.setattr(settings, k, v)
    c = storage.s3()
    try:
        c.head_bucket(Bucket=S3["S3_BUCKET"])
    except c.exceptions.ClientError:
        c.create_bucket(Bucket=S3["S3_BUCKET"])

    # new uploads go to the bucket; the endpoint checks the login and redirects to a presigned URL
    ag, p, (m,) = _property_with_media(db, [("a.jpg", b"\xff\xd8s3")])
    assert not storage.local_path(m.path).exists()
    assert c.get_object(Bucket=S3["S3_BUCKET"], Key=m.path)["ContentType"] == "image/jpeg"
    assert client.get(f"/api/media/{m.path}").status_code == 401
    r = client.get(f"/api/media/{m.path}", headers=auth(ag), follow_redirects=False)
    assert r.status_code == 307 and httpx.get(r.headers["location"]).content == b"\xff\xd8s3"
    assert client.get("/api/media/properties/%2E%2E/x.jpg", headers=auth(ag), follow_redirects=False).status_code == 404
    assert open(storage.fetch(m.path, str(tmp_path)), "rb").read() == b"\xff\xd8s3"
    properties.delete_media(db, p.id, m.id, ag)
    assert storage.fetch(m.path, str(tmp_path)) is None

    # copy of existing local files (+ bot listing photos behind the flag) is idempotent
    run = uuid.uuid4().hex[:8]
    old = storage.local_path(f"properties/old{run}/x.jpg")
    old.parent.mkdir(parents=True)
    old.write_bytes(b"old")
    img = tmp_path / "img"
    img.mkdir()
    (img / f"ss{run}_0.jpg").write_bytes(b"listing")
    monkeypatch.setattr(storage, "LISTING_IMG", img)
    assert storage.copy(out=lambda _: None) == (1, 0)
    assert storage.copy(listing_photos=True, out=lambda _: None) == (1, 1)
    assert storage.copy(listing_photos=True, out=lambda _: None) == (0, 2)
    assert c.get_object(Bucket=S3["S3_BUCKET"], Key=f"bot/raw/img/ss{run}_0.jpg")["Body"].read() == b"listing"
    for k in (f"properties/old{run}/x.jpg", f"bot/raw/img/ss{run}_0.jpg"):
        c.delete_object(Bucket=S3["S3_BUCKET"], Key=k)
