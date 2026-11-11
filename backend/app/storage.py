"""Where CRM media files live. The key is property_media.path (properties/<id>/<uuid>.<ext>).

MEDIA_STORAGE=local (default): files under MEDIA_DIR, served by GET /api/media/{key}.
MEDIA_STORAGE=s3: an S3-compatible bucket (Cloudflare R2, Hetzner, MinIO): S3_ENDPOINT, S3_BUCKET, S3_ACCESS_KEY,
S3_SECRET_KEY, S3_REGION; GET /api/media/{key} checks the login and redirects to S3_PUBLIC_URL/<key> or a presigned URL.

Moving existing files (idempotent, never deletes local copies; needs only the S3_* variables, not MEDIA_STORAGE):
    python -m app.storage copy                    # MEDIA_DIR -> bucket, same keys
    python -m app.storage copy --listing-photos   # + the bot's bot/raw/img -> bucket keys bot/raw/img/<file>

Previews: a ~640px WebP of every photo at previews/<key>.webp (made on upload; ?p=1 on the media URLs serves it, else
the original). Listing photos (bot/raw/img, always local) get theirs in MEDIA_DIR/previews/bot/raw/img/ on the first
?p=1 request; remote listing photo URLs (http...) have none. Backfill for existing files (idempotent):
    python -m app.storage previews                # MEDIA_STORAGE files + bot/raw/img
"""
import io
import logging
import mimetypes
import os
import sys
import threading
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

from app.config import REPO_ROOT, settings as env

log = logging.getLogger(__name__)
LISTING_IMG = REPO_ROOT / "bot" / "raw" / "img"  # listings.photos entries are raw/img/<file>, relative to bot/
PRESIGN_TTL = 3600
PREVIEW_PX, PREVIEW_Q = 640, 70
PREVIEW_OF = (".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif")  # what Pillow (+ pillow-heif) reads; videos / gif keep the original


def is_s3() -> bool:
    return env.MEDIA_STORAGE == "s3"


def local_path(key: str) -> Path:
    return Path(env.MEDIA_DIR) / key


def content_type(key: str) -> str:
    return mimetypes.guess_type(key)[0] or "application/octet-stream"


@lru_cache
def _client(endpoint, region, access, secret):
    import boto3
    from botocore.config import Config
    return boto3.client("s3", endpoint_url=endpoint or None, region_name=region or None, aws_access_key_id=access,
                        aws_secret_access_key=secret,
                        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}))


def s3():
    return _client(env.S3_ENDPOINT, env.S3_REGION, env.S3_ACCESS_KEY, env.S3_SECRET_KEY)


def put(key: str, data: bytes) -> None:
    if is_s3():
        s3().put_object(Bucket=env.S3_BUCKET, Key=key, Body=data, ContentType=content_type(key))
    else:
        p = local_path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)


def delete(key: str) -> None:
    keys = [key] + ([preview_key(key)] if not key.startswith("previews/") else [])
    for k in keys:
        if is_s3():
            s3().delete_object(Bucket=env.S3_BUCKET, Key=k)
        else:
            local_path(k).unlink(missing_ok=True)


def preview_key(key: str) -> str:
    return f"previews/{key}.webp"


def make_preview(data: bytes) -> bytes | None:
    """~PREVIEW_PX px WebP of an image, None if Pillow can't read it (the original is served then)."""
    from PIL import Image, ImageOps
    try:
        import pillow_heif  # iPhone originals (HEIC): browsers other than Safari can't show them, the preview can
        pillow_heif.register_heif_opener()
    except ImportError:
        pass
    try:
        with Image.open(io.BytesIO(data)) as im:
            im = ImageOps.exif_transpose(im)
            im.thumbnail((PREVIEW_PX, PREVIEW_PX))
            if im.mode not in ("RGB", "RGBA"):
                im = im.convert("RGB")
            buf = io.BytesIO()
            im.save(buf, "WEBP", quality=PREVIEW_Q, method=4)
            return buf.getvalue()
    except Exception as e:
        log.info("no preview: %s", e)
        return None


def put_preview(key: str, data: bytes) -> bool:
    if not key.lower().endswith(PREVIEW_OF) or not (b := make_preview(data)):
        return False
    put(preview_key(key), b)
    _have.add(preview_key(key))
    return True


def local_preview(src: Path, key: str) -> Path | None:
    """Local file -> its preview under MEDIA_DIR (made now if missing), None when there can't be one."""
    dest = local_path(preview_key(key))
    if dest.is_file():
        return dest
    if not src.name.lower().endswith(PREVIEW_OF) or not (b := make_preview(src.read_bytes())):
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f"{dest.name}.{os.getpid()}.tmp")
    tmp.write_bytes(b)
    os.replace(tmp, dest)  # atomic: two requests for the same photo never serve half a file
    return dest


_have: set[str] = set()  # s3 preview keys known to exist (they only appear, never disappear except with the original)


def s3_has(key: str) -> bool:
    if key in _have:
        return True
    try:
        s3().head_object(Bucket=env.S3_BUCKET, Key=key)
    except Exception:
        return False
    _have.add(key)
    return True


def cached(key: str) -> Path | None:
    """s3: a local copy of a small stored file (a preview) under MEDIA_DIR/cache, fetched once and then served from
    the disk (a phone loading 50 cards made 50 redirects to the bucket, each a new, uncacheable presigned link).
    None when the storage has no such file."""
    dest = Path(env.MEDIA_DIR) / "cache" / key
    if dest.is_file():
        return dest
    try:
        data = s3().get_object(Bucket=env.S3_BUCKET, Key=key)["Body"].read()
    except Exception:  # NoSuchKey and the like: the caller falls back to the original
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f"{dest.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, dest)  # atomic: two requests for the same preview never serve half a file
    _have.add(key)
    return dest


def fetch(key: str, tmp: str) -> str | None:
    """A local file path for the key (s3: downloaded into the tmp dir), None if missing."""
    if not is_s3():
        p = Path(key) if Path(key).is_absolute() else local_path(key)
        return str(p) if p.is_file() else None
    dest = Path(tmp) / key.replace("/", "_")
    try:
        s3().download_file(env.S3_BUCKET, key, str(dest))
    except Exception as e:  # botocore ClientError 404 and the like
        log.warning("s3 download of %s failed: %s", key, e)
        return None
    return str(dest)


def url(key: str, presign: bool = False) -> str:
    """s3 only: where the browser gets the file. presign: never the public base URL (private documents)."""
    if env.S3_PUBLIC_URL and not presign:
        return f"{env.S3_PUBLIC_URL.rstrip('/')}/{quote(key)}"
    return s3().generate_presigned_url("get_object", Params={"Bucket": env.S3_BUCKET, "Key": key}, ExpiresIn=PRESIGN_TTL)


def copy(listing_photos: bool = False, out=print) -> tuple[int, int]:
    """Local files -> bucket; a key already there with the same size is skipped. -> (uploaded, skipped)."""
    c = s3()
    roots = [(Path(env.MEDIA_DIR), "")] + ([(LISTING_IMG, "bot/raw/img/")] if listing_photos else [])
    up = skip = 0
    for root, prefix in roots:
        for f in sorted(root.rglob("*")) if root.is_dir() else []:
            if not f.is_file():
                continue
            key = prefix + f.relative_to(root).as_posix()
            try:
                if c.head_object(Bucket=env.S3_BUCKET, Key=key)["ContentLength"] == f.stat().st_size:
                    skip += 1
                    continue
            except c.exceptions.ClientError as e:
                if e.response.get("Error", {}).get("Code") not in ("404", "NoSuchKey", "NotFound"):
                    raise
            c.upload_file(str(f), env.S3_BUCKET, key, ExtraArgs={"ContentType": content_type(key)})
            up += 1
    out(f"storage copy -> s3://{env.S3_BUCKET}: {up} uploaded, {skip} already there")
    return up, skip


def previews(out=print) -> tuple[int, int]:
    """Backfill: a preview for every stored photo that has none + the bot's listing photos. -> (made, skipped)."""
    made = skip = 0
    if is_s3():
        c, keys = s3(), set()
        for page in c.get_paginator("list_objects_v2").paginate(Bucket=env.S3_BUCKET):
            keys |= {o["Key"] for o in page.get("Contents", [])}
        for k in sorted(keys):
            if k.startswith("previews/") or not k.lower().endswith(PREVIEW_OF):
                continue
            if preview_key(k) in keys:
                skip += 1
                continue
            made += put_preview(k, c.get_object(Bucket=env.S3_BUCKET, Key=k)["Body"].read())
    roots = [(LISTING_IMG, "bot/raw/img/")] + ([] if is_s3() else [(Path(env.MEDIA_DIR), "")])
    for root, prefix in roots:
        for f in sorted(root.rglob("*")) if root.is_dir() else []:
            key = prefix + f.relative_to(root).as_posix()
            if not f.is_file() or key.startswith("previews/") or not key.lower().endswith(PREVIEW_OF):
                continue
            if local_path(preview_key(key)).is_file():
                skip += 1
            else:
                made += local_preview(f, key) is not None
    out(f"storage previews: {made} made, {skip} already there")
    return made, skip


if __name__ == "__main__":
    if sys.argv[1:2] == ["previews"]:
        previews()
        sys.exit()
    if sys.argv[1:2] != ["copy"]:
        sys.exit(__doc__)
    if not (env.S3_BUCKET and env.S3_ACCESS_KEY):
        sys.exit("set S3_ENDPOINT, S3_BUCKET, S3_ACCESS_KEY, S3_SECRET_KEY first")
    copy(listing_photos="--listing-photos" in sys.argv)
