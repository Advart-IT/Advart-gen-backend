"""
Products. A product is a studio Brand Submissions item (brand_items), so this file reads and writes the studio's
own tables and must keep to the studio's rules for them (task management, Design/brand_submissions.py):

    Pending   = no generated image saved yet, or sent back (is_sent_back)
    Generated = at least one generated image, not sent back, not marked done   -> the customer reviews it
    Done      = is_marked_generated

These are the same three states the studio's own Pending / Generated / Done tabs show (list_items_by_brand
there), so both apps always agree on where a product is. From the portal a customer can only create a product
(Pending), approve a Generated one ("Mark as done") or send a Generated one back with notes.
"""
import re
import zipfile
from tempfile import SpooledTemporaryFile
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

import storage
from database import get_db
from models import (
    BrandItem, BrandItemGeneratedImage, BrandItemImage, BrandItemType, DesignAsset, PortalReviewNote,
)
from routers.uploads import take_uploads
from security import Caller, allowed

router = APIRouter()
PRODUCTS = allowed("products")  # an approved brand, and Products access there

# brand_items.created_by / brand_item_types.created_by hold a studio employee id. 0 = created by a customer in
# the portal (no studio employee has id 0, so the studio simply shows no creator name).
CREATED_IN_PORTAL = 0
MAX_PRODUCT_IMAGES = 20
MAX_DESCRIPTION = 2000
REVIEW_TAGS = ("Adjust lighting", "Different pose", "Fix fabric detail", "Colour accuracy", "Change crop", "Swap background")


def _iso(dt):
    return dt.isoformat() + "Z" if dt else None  # stored as naive UTC


def status_of(item: BrandItem, has_generated: bool) -> str:
    if item.is_marked_generated:
        return "done"
    if has_generated and not item.is_sent_back:
        return "generated"
    return "pending"


def shot_label(shot_type: str) -> str:
    return (shot_type or "hero").replace("_", " ").title()


def _brand_items(db: Session, caller: Caller):
    """Every query for products starts here: only this brand's live items."""
    return (
        db.query(BrandItem, BrandItemType.name)
        .join(BrandItemType, BrandItem.item_type_id == BrandItemType.id)
        .filter(
            BrandItemType.brand_name == caller.brand_key,
            BrandItemType.is_deleted.is_(False),
            BrandItem.is_deleted.is_(False),
        )
    )


def _own_item(db: Session, caller: Caller, product_id: int):
    row = _brand_items(db, caller).filter(BrandItem.id == product_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="That product no longer exists.")
    return row


def _generated(db: Session, item_ids: List[int]) -> dict:
    """item id -> its generated images as (row, storage path), hero first then by shot type (the studio's order)."""
    if not item_ids:
        return {}
    rows = db.query(BrandItemGeneratedImage).filter(BrandItemGeneratedImage.item_id.in_(item_ids)).all()
    asset_ids = {r.asset_id for r in rows if r.asset_id}
    paths = dict(db.query(DesignAsset.id, DesignAsset.storage_url).filter(DesignAsset.id.in_(asset_ids)).all()) if asset_ids else {}
    out: dict = {}
    for row in sorted(rows, key=lambda r: (r.shot_type != "hero", r.shot_type or "", r.id)):
        path = paths.get(row.asset_id) if row.asset_id else row.gcs_path
        if path:
            out.setdefault(row.item_id, []).append((row, path))
    return out


def _summary(item: BrandItem, type_name: str, originals: list, generated: list, last_note_at) -> dict:
    status = status_of(item, bool(generated))
    stamps = [item.created_at, last_note_at] + [row.generated_at for row, _path in generated]
    thumb = generated[0][1] if generated and status != "pending" else (originals[0].gcs_path if originals else None)
    return {
        "id": item.id, "name": item.name, "item_type": type_name, "status": status,
        "sent_back": bool(item.is_sent_back),
        "original_count": len(originals), "generated_count": len(generated),
        "uploaded_at": _iso(item.created_at), "updated_at": _iso(max(s for s in stamps if s) if any(stamps) else None),
        "thumb_url": storage.url(thumb),
    }


@router.get("/item-types")
def list_item_types(caller: Caller = Depends(PRODUCTS), db: Session = Depends(get_db)):
    rows = db.query(BrandItemType.name).filter(
        BrandItemType.brand_name == caller.brand_key, BrandItemType.is_deleted.is_(False),
    ).order_by(BrandItemType.name).all()
    return [name for (name,) in rows]


@router.get("/products")
def list_products(caller: Caller = Depends(PRODUCTS), db: Session = Depends(get_db)):
    rows = _brand_items(db, caller).order_by(BrandItem.id.desc()).all()
    item_ids = [item.id for item, _type in rows]
    originals: dict = {}
    last_note: dict = {}
    if item_ids:
        for image in db.query(BrandItemImage).filter(BrandItemImage.item_id.in_(item_ids)).order_by(BrandItemImage.id).all():
            originals.setdefault(image.item_id, []).append(image)
        last_note = dict(
            db.query(PortalReviewNote.item_id, func.max(PortalReviewNote.created_at))
            .filter(PortalReviewNote.item_id.in_(item_ids)).group_by(PortalReviewNote.item_id).all()
        )
    generated = _generated(db, item_ids)
    return [
        _summary(item, type_name, originals.get(item.id, []), generated.get(item.id, []), last_note.get(item.id))
        for item, type_name in rows
    ]


class ProductCreate(BaseModel):
    name: str
    item_type: str
    upload_ids: List[int]
    description: str = ""


@router.post("/products")
def create_product(payload: ProductCreate, caller: Caller = Depends(PRODUCTS), db: Session = Depends(get_db)):
    name, type_name = payload.name.strip(), " ".join(payload.item_type.split())
    if not name:
        raise HTTPException(status_code=400, detail="Enter an item name.")
    if not type_name:
        raise HTTPException(status_code=400, detail="Choose an item type.")
    if not payload.upload_ids:
        raise HTTPException(status_code=400, detail="Upload at least one product image.")
    if len(payload.upload_ids) > MAX_PRODUCT_IMAGES:
        raise HTTPException(status_code=400, detail=f"Add at most {MAX_PRODUCT_IMAGES} images per product.")
    description = payload.description.strip()
    if len(description) > MAX_DESCRIPTION:
        raise HTTPException(status_code=400, detail=f"Keep the description under {MAX_DESCRIPTION} characters.")
    uploads = take_uploads(db, caller, payload.upload_ids)

    # Item types are each brand's own list in the studio: reuse the brand's type of that name, or start it.
    item_type = db.query(BrandItemType).filter(
        BrandItemType.brand_name == caller.brand_key, BrandItemType.is_deleted.is_(False),
        func.lower(BrandItemType.name) == type_name.lower(),
    ).first()
    if not item_type:
        item_type = BrandItemType(brand_name=caller.brand_key, name=type_name[:255], created_by=CREATED_IN_PORTAL)
        db.add(item_type)
        db.flush()
    item = BrandItem(item_type_id=item_type.id, name=name[:255], description=description or None, available_ages=[],
                     created_by=CREATED_IN_PORTAL)
    db.add(item)
    db.flush()
    for upload in uploads:
        db.add(BrandItemImage(item_id=item.id, gcs_path=upload.storage_path))
    db.commit()
    return get_product(item.id, caller, db)


@router.get("/products/{product_id}")
def get_product(product_id: int, caller: Caller = Depends(PRODUCTS), db: Session = Depends(get_db)):
    item, type_name = _own_item(db, caller, product_id)
    originals = db.query(BrandItemImage).filter(BrandItemImage.item_id == item.id).order_by(BrandItemImage.id).all()
    generated = _generated(db, [item.id]).get(item.id, [])
    notes = db.query(PortalReviewNote).filter(
        PortalReviewNote.item_id == item.id, PortalReviewNote.brand_id == caller.brand.id,
    ).order_by(PortalReviewNote.id.desc()).all()
    out = _summary(item, type_name, originals, generated, notes[0].created_at if notes else None)
    out["description"] = item.description or ""
    out["originals"] = [{"id": image.id, "url": storage.url(image.gcs_path)} for image in originals]
    # A product that is Pending again (sent back) keeps its earlier images in the studio, but the customer is
    # shown "being reworked" rather than the images they just rejected.
    out["generated"] = [] if out["status"] == "pending" else [
        {"id": row.id, "url": storage.url(path), "shot_label": shot_label(row.shot_type)} for row, path in generated
    ]
    out["notes"] = [{"id": n.id, "text": n.text or "", "tags": n.tags or [], "created_at": _iso(n.created_at)} for n in notes]
    return out


def _require_in_review(item: BrandItem, generated: list) -> None:
    if status_of(item, bool(generated)) != "generated":
        raise HTTPException(status_code=409, detail="This product isn't waiting for your review.")


@router.post("/products/{product_id}/mark-done")
def mark_done(product_id: int, caller: Caller = Depends(PRODUCTS), db: Session = Depends(get_db)):
    item, _type = _own_item(db, caller, product_id)
    _require_in_review(item, _generated(db, [item.id]).get(item.id, []))
    item.is_marked_generated = True
    item.is_sent_back = False
    db.commit()
    return get_product(product_id, caller, db)


class SendBack(BaseModel):
    tags: List[str] = []
    text: str = ""


@router.post("/products/{product_id}/send-back")
def send_back(product_id: int, payload: SendBack, caller: Caller = Depends(PRODUCTS), db: Session = Depends(get_db)):
    item, _type = _own_item(db, caller, product_id)
    _require_in_review(item, _generated(db, [item.id]).get(item.id, []))
    tags = [tag for tag in REVIEW_TAGS if tag in payload.tags]
    text = payload.text.strip()
    if not tags and not text:
        raise HTTPException(status_code=400, detail="Add a note or pick at least one tag.")
    db.add(PortalReviewNote(item_id=item.id, brand_id=caller.brand.id, user_id=caller.user.id, text=text, tags=tags))
    # What the studio's own "Send back" does (send_item_back): the note it shows its team, and back to Pending.
    item.send_back_note = (f"{', '.join(tags)}: {text}" if tags and text else text or ", ".join(tags))
    item.is_marked_generated = False
    item.is_sent_back = True
    db.commit()
    return get_product(product_id, caller, db)


# ---- Downloads -------------------------------------------------------------------------------------------------

def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9 _.-]+", "", name).strip() or "image"


def _extension(path: str) -> str:
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else "png"
    return ext if ext in ("png", "jpg", "jpeg", "webp") else "png"


def _downloadable(db: Session, caller: Caller, product_id: int):
    item, _type = _own_item(db, caller, product_id)
    generated = _generated(db, [item.id]).get(item.id, [])
    if status_of(item, bool(generated)) == "pending":
        generated = []
    return item, generated


@router.get("/products/{product_id}/images/{image_id}/download")
def download_image(product_id: int, image_id: int, caller: Caller = Depends(PRODUCTS), db: Session = Depends(get_db)):
    item, generated = _downloadable(db, caller, product_id)
    match = next(((row, path) for row, path in generated if row.id == image_id), None)
    if not match:
        raise HTTPException(status_code=404, detail="That image no longer exists.")
    row, path = match
    ext = _extension(path)
    filename = f"{_safe(item.name)} - {_safe(shot_label(row.shot_type))}.{ext}"
    return Response(
        content=storage.read(path), media_type="image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/products/{product_id}/download")
def download_all(product_id: int, caller: Caller = Depends(PRODUCTS), db: Session = Depends(get_db)):
    item, generated = _downloadable(db, caller, product_id)
    if not generated:
        raise HTTPException(status_code=404, detail="There are no images to download yet.")
    buffer = SpooledTemporaryFile(max_size=64 * 1024 * 1024)
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:  # images are already compressed
        for index, (row, path) in enumerate(generated, 1):
            archive.writestr(f"{index:02d} - {_safe(shot_label(row.shot_type))}.{_extension(path)}", storage.read(path))
    buffer.seek(0)

    def chunks():
        try:
            while True:
                chunk = buffer.read(1024 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            buffer.close()

    return StreamingResponse(
        chunks(), media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{_safe(item.name)}.zip"'},
    )
