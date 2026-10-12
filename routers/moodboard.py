"""Moodboard: a brand's models (kids or adults), backgrounds and accessories. Each has any number of photos and/or a
prompt in the customer's own words ("3-year-old girl, curly hair, big smile"); at least one of the two."""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

import storage
from database import get_db
from models import PortalAsset
from routers.uploads import take_uploads
from security import Caller, allowed

router = APIRouter()
PRODUCTS = allowed("products")  # the moodboard goes with Products access

KINDS = ("model", "background", "accessory")
AGE_GROUPS = ("kid", "adult")
GENDERS = {"kid": ("Girl", "Boy", "Any"), "adult": ("Female", "Male", "Non-binary")}
AGE_RANGES = {
    "kid": ("0–6 months", "6–12 months", "1–2 years", "2–4 years", "4–6 years", "6–9 years", "9–12 years"),
    "adult": ("18–24", "25–34", "35–44", "45–54", "55+"),
}
DEFAULT_AGE = {"kid": "2–4 years", "adult": "25–34"}
SETTINGS = ("Studio", "Indoor", "Outdoor", "Urban", "Nature")
ACCESSORY_TYPES = ("Bag", "Sunglasses", "Watch", "Jewellery", "Hat", "Scarf", "Footwear", "Belt", "Toy", "Hair accessory")
MAX_IMAGES = 20
MAX_PROMPT = 2000


def _serialize(asset: PortalAsset) -> dict:
    paths = asset.image_paths or []
    return {
        "id": asset.id, "kind": asset.kind, "name": asset.name,
        # Each photo's key is its storage path: it is how an edit says which photos to keep.
        "images": [{"key": path, "url": storage.url(path)} for path in paths],
        "image_url": storage.url(paths[0]) if paths else None,  # the cover
        "prompt": asset.prompt or "",
        "favourite": bool(asset.is_favourite), "age_group": asset.age_group, "gender": asset.gender,
        "age_range": asset.age_range, "setting": asset.setting, "accessory_type": asset.accessory_type,
    }


def _own_asset(db: Session, caller: Caller, asset_id: int) -> PortalAsset:
    asset = db.query(PortalAsset).filter(
        PortalAsset.id == asset_id, PortalAsset.brand_id == caller.brand.id, PortalAsset.is_deleted.is_(False),
    ).first()
    if not asset:
        raise HTTPException(status_code=404, detail="That moodboard item no longer exists.")
    return asset


def _apply_details(asset: PortalAsset, age_group, gender, age_range, setting, accessory_type) -> None:
    """Sets the fields that belong to this asset's kind, checked against the lists the form offers."""
    def pick(value, allowed, current, what):
        if value is None:
            return current if current in allowed else allowed[0]
        if value not in allowed:
            raise HTTPException(status_code=400, detail=f"Choose a {what} from the list.")
        return value

    if asset.kind == "model":
        group = pick(age_group, AGE_GROUPS, asset.age_group or "adult", "model type")
        asset.age_group = group
        asset.gender = pick(gender, GENDERS[group], asset.gender, "gender")
        asset.age_range = pick(age_range, AGE_RANGES[group], asset.age_range if asset.age_range in AGE_RANGES[group] else DEFAULT_AGE[group], "age range")
    elif asset.kind == "background":
        asset.setting = pick(setting, SETTINGS, asset.setting, "setting")
    else:
        asset.accessory_type = pick(accessory_type, ACCESSORY_TYPES, asset.accessory_type, "type")


def _check_content(paths: list, prompt: str) -> None:
    if not paths and not prompt:
        raise HTTPException(status_code=400, detail="Add at least one photo, or describe it in the prompt.")
    if len(paths) > MAX_IMAGES:
        raise HTTPException(status_code=400, detail=f"Add at most {MAX_IMAGES} photos.")
    if len(prompt) > MAX_PROMPT:
        raise HTTPException(status_code=400, detail=f"Keep the prompt under {MAX_PROMPT} characters.")


@router.get("/assets")
def list_assets(caller: Caller = Depends(PRODUCTS), db: Session = Depends(get_db)):
    rows = db.query(PortalAsset).filter(
        PortalAsset.brand_id == caller.brand.id, PortalAsset.is_deleted.is_(False),
    ).order_by(PortalAsset.id.desc()).all()
    return {
        "models": [_serialize(a) for a in rows if a.kind == "model"],
        "backgrounds": [_serialize(a) for a in rows if a.kind == "background"],
        "accessories": [_serialize(a) for a in rows if a.kind == "accessory"],
    }


class AssetCreate(BaseModel):
    kind: str
    name: str
    upload_ids: List[int] = []
    prompt: str = ""
    age_group: Optional[str] = None
    gender: Optional[str] = None
    age_range: Optional[str] = None
    setting: Optional[str] = None
    accessory_type: Optional[str] = None


@router.post("/assets")
def create_asset(payload: AssetCreate, caller: Caller = Depends(PRODUCTS), db: Session = Depends(get_db)):
    if payload.kind not in KINDS:
        raise HTTPException(status_code=400, detail="Unknown moodboard section.")
    name, prompt = payload.name.strip(), payload.prompt.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Give it a name so you can find it later.")
    _check_content(payload.upload_ids, prompt)
    paths = [upload.storage_path for upload in take_uploads(db, caller, payload.upload_ids)]
    asset = PortalAsset(brand_id=caller.brand.id, kind=payload.kind, name=name[:255], image_paths=paths, prompt=prompt or None)
    _apply_details(asset, payload.age_group, payload.gender, payload.age_range, payload.setting, payload.accessory_type)
    db.add(asset)
    db.commit()
    db.refresh(asset)
    return _serialize(asset)


class AssetUpdate(BaseModel):
    name: Optional[str] = None
    keep_images: Optional[List[str]] = None  # keys of the photos to keep, in order; None = keep them all
    upload_ids: List[int] = []                # new photos, added after the kept ones
    prompt: Optional[str] = None
    age_group: Optional[str] = None
    gender: Optional[str] = None
    age_range: Optional[str] = None
    setting: Optional[str] = None
    accessory_type: Optional[str] = None
    favourite: Optional[bool] = None


@router.patch("/assets/{asset_id}")
def update_asset(asset_id: int, payload: AssetUpdate, caller: Caller = Depends(PRODUCTS), db: Session = Depends(get_db)):
    asset = _own_asset(db, caller, asset_id)
    if payload.name is not None:
        name = payload.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="Give it a name so you can find it later.")
        asset.name = name[:255]
    current = list(asset.image_paths or [])
    if payload.keep_images is None:
        kept = current
    else:
        # Only this asset's own photos can be kept: a key is never a way to attach some other image.
        kept = [path for path in dict.fromkeys(payload.keep_images) if path in current]
    prompt = (payload.prompt if payload.prompt is not None else asset.prompt or "").strip()
    _check_content(kept + [None] * len(payload.upload_ids), prompt)
    asset.image_paths = kept + [upload.storage_path for upload in take_uploads(db, caller, payload.upload_ids)]
    asset.prompt = prompt or None
    _apply_details(asset, payload.age_group, payload.gender, payload.age_range, payload.setting, payload.accessory_type)
    if payload.favourite is not None:
        asset.is_favourite = payload.favourite
    db.commit()
    db.refresh(asset)
    return _serialize(asset)


@router.delete("/assets/{asset_id}")
def delete_asset(asset_id: int, caller: Caller = Depends(PRODUCTS), db: Session = Depends(get_db)):
    asset = _own_asset(db, caller, asset_id)
    asset.is_deleted = True
    db.commit()
    return {"ok": True}
