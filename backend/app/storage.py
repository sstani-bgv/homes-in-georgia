"""Where CRM media files live. The key is property_media.path (properties/<id>/<uuid>.<ext>).

MEDIA_STORAGE=local (default): files under MEDIA_DIR, served by GET /api/media/{key}.
MEDIA_STORAGE=s3: an S3-compatible bucket (Cloudflare R2, Hetzner, MinIO): S3_ENDPOINT, S3_BUCKET, S3_ACCESS_KEY,
S3_SECRET_KEY, S3_REGION; GET /api/media/{key} checks the login and redirects to S3_PUBLIC_URL/<key> or a presigned URL.

Moving existing files (idempotent, never deletes local copies; needs only the S3_* variables, not MEDIA_STORAGE):
    python -m app.storage copy                    # MEDIA_DIR -> bucket, same keys
    python -m app.storage copy --listing-photos   # + the bot's bot/raw/img -> bucket keys bot/raw/img/<file>
"""
import logging
import mimetypes
import sys
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

from app.config import REPO_ROOT, settings as env

log = logging.getLogger(__name__)
LISTING_IMG = REPO_ROOT / "bot" / "raw" / "img"  # listings.photos entries are raw/img/<file>, relative to bot/
PRESIGN_TTL = 3600


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
    if is_s3():
        s3().delete_object(Bucket=env.S3_BUCKET, Key=key)
    else:
        local_path(key).unlink(missing_ok=True)


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


def url(key: str) -> str:
    """s3 only: where the browser gets the file."""
    if env.S3_PUBLIC_URL:
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


if __name__ == "__main__":
    if sys.argv[1:2] != ["copy"]:
        sys.exit(__doc__)
    if not (env.S3_BUCKET and env.S3_ACCESS_KEY):
        sys.exit("set S3_ENDPOINT, S3_BUCKET, S3_ACCESS_KEY, S3_SECRET_KEY first")
    copy(listing_photos="--listing-photos" in sys.argv)
