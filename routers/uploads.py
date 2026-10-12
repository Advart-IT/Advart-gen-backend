"""
Staged image uploads. The forms upload each file the moment it is picked (so each shows its own progress bar)
and get back an upload id; saving the form then attaches those ids to a product or a moodboard entry.
"""
import os
from typing import List

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

import config
import security
import storage
from database import get_db
from models import PortalUpload
from security import Caller, allowed, get_caller

router = APIRouter()


@router.post("/uploads")
async def upload_image(file: UploadFile = File(...), caller: Caller = Depends(allowed("products", "invoices")),
                       db: Session = Depends(get_db)):
    # Product photos and moodboard images (Products), or a payment screenshot (Invoices).
    data = await file.read()
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="That image is over 20 MB. Choose a smaller one.")
    try:
        prepared = storage.prepare_image(data)
    except storage.NotAnImage:
        raise HTTPException(status_code=400, detail="That file isn't an image we can read. Use JPG, PNG or HEIC.")
    upload = PortalUpload(
        brand_id=caller.brand.id, user_id=caller.user.id, storage_path=storage.save_image(prepared),
        original_name=(file.filename or "image")[:255], size_bytes=len(data),
    )
    db.add(upload)
    db.commit()
    db.refresh(upload)
    return {"id": upload.id, "name": upload.original_name, "size": upload.size_bytes, "url": storage.url(upload.storage_path)}


def take_uploads(db: Session, caller: Caller, upload_ids: List[int]) -> List[PortalUpload]:
    """The caller's own, not-yet-used uploads for these ids, in the order given, now marked used. Anything else
    (another brand's id, an id used before, an unknown id) is refused: an upload id must never let one brand
    attach another brand's image. The caller commits."""
    if not upload_ids:
        return []
    rows = db.query(PortalUpload).filter(
        PortalUpload.id.in_(set(upload_ids)), PortalUpload.brand_id == caller.brand.id, PortalUpload.used_at.is_(None),
    ).all()
    by_id = {row.id: row for row in rows}
    if len(by_id) != len(set(upload_ids)):
        raise HTTPException(status_code=400, detail="One of the images didn't finish uploading. Remove it and add it again.")
    used = security.now()
    ordered = []
    for upload_id in dict.fromkeys(upload_ids):
        by_id[upload_id].used_at = used
        ordered.append(by_id[upload_id])
    return ordered


@router.get("/files/{path:path}")
def local_file(path: str, caller: Caller = Depends(get_caller)):
    """Development only (STORAGE_BACKEND=local): the stand-in for a signed bucket link."""
    if config.STORAGE_BACKEND != "local":
        raise HTTPException(status_code=404, detail="Not found")
    try:
        full = storage._local_file(path)
    except ValueError:
        raise HTTPException(status_code=404, detail="Not found")
    if not os.path.isfile(full):
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(full, media_type="image/jpeg")
