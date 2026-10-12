"""
Where images live, and the links that show them.

"gcs" (production): the same private "advart" bucket and design-tool/references/ folder the studio's own uploads
use (task management's Design/gcs.py), so a product photo uploaded here is readable there and a generated image
saved there is readable here. Nothing in the bucket is public: every image is shown through a signed link.

"local" (development and tests): files on disk, served back through /api/files/ to signed-in users only.
"""
import datetime
import io
import os
import uuid
from typing import Optional

import pillow_heif
from PIL import Image, ImageOps

import config

pillow_heif.register_heif_opener()  # iPhone photos (HEIC) open like any other image

REFERENCES_FOLDER = "design-tool/references"
_MAX_SIDE = 2048  # the studio's own limit for a reference photo (Design/gcs.py)
_LINK_LIFETIME = datetime.timedelta(hours=12)
_link_cache: dict = {}  # path -> (url, expires): the same link is handed out again so the browser can cache the image
_gcs = None


class NotAnImage(ValueError):
    pass


def prepare_image(data: bytes) -> bytes:
    """Upright (EXIF applied), RGB, at most 2048px on the long side, JPEG: what the studio does to its own
    reference uploads. Raises NotAnImage for anything Pillow cannot read."""
    try:
        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        if max(img.size) > _MAX_SIDE:
            img.thumbnail((_MAX_SIDE, _MAX_SIDE), Image.LANCZOS)
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=85)
        return out.getvalue()
    except Exception as e:
        raise NotAnImage(str(e))


def _bucket():
    global _gcs
    if _gcs is None:
        from google.cloud import storage as gcs
        from google.oauth2 import service_account

        # The service account the studio itself signs in with (task management's Design/gcs.py): key/auth.py
        # holds it as a dict, AUTH. Imported here rather than at the top so tests and STORAGE_BACKEND=local
        # never need the key.
        try:
            from key.auth import AUTH
        except ImportError:
            raise RuntimeError(
                "Image storage is not set up: key/auth.py (the studio's service account, AUTH = {...}) not found. "
                "Copy task-management-system/key/auth.py here, or set STORAGE_BACKEND=local for development."
            )
        credentials = service_account.Credentials.from_service_account_info(AUTH)
        client = gcs.Client(credentials=credentials, project=AUTH["project_id"])
        _gcs = (client.bucket(config.GCS_BUCKET), credentials)
    return _gcs


def _local_file(path: str) -> str:
    root = os.path.abspath(config.LOCAL_STORAGE_DIR)
    full = os.path.abspath(os.path.join(root, path))
    if not full.startswith(root + os.sep):
        raise ValueError("path outside the storage folder")
    return full


def save_image(jpeg_bytes: bytes) -> str:
    """Stores an image already passed through prepare_image. Returns its storage path (never a URL)."""
    path = f"{REFERENCES_FOLDER}/{uuid.uuid4()}.jpg"
    if config.STORAGE_BACKEND == "local":
        full = _local_file(path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as f:
            f.write(jpeg_bytes)
    else:
        bucket, _credentials = _bucket()
        bucket.blob(path).upload_from_string(jpeg_bytes, content_type="image/jpeg")
    return path


def read(path: str) -> bytes:
    if config.STORAGE_BACKEND == "local":
        with open(_local_file(path), "rb") as f:
            return f.read()
    bucket, _credentials = _bucket()
    return bucket.blob(path).download_as_bytes()


def url(path: Optional[str]) -> Optional[str]:
    """A link the browser can load the image from."""
    if not path:
        return None
    if config.STORAGE_BACKEND == "local":
        return f"/api/files/{path}"
    current = datetime.datetime.utcnow()
    cached = _link_cache.get(path)
    if cached and current < cached[1] - datetime.timedelta(hours=1):
        return cached[0]
    bucket, credentials = _bucket()
    link = bucket.blob(path).generate_signed_url(
        expiration=_LINK_LIFETIME, method="GET", version="v4", credentials=credentials,
    )
    _link_cache[path] = (link, current + _LINK_LIFETIME)
    return link
