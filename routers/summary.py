"""The three counts shown beside the sidebar links."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from database import get_db
from models import BrandItem, BrandItemGeneratedImage, BrandItemType, PortalAsset
from routers.invoices import _visible, is_due
from security import Caller, get_caller

router = APIRouter()


@router.get("/summary")
def summary(caller: Caller = Depends(get_caller), db: Session = Depends(get_db)):
    # Only the areas this login may use are counted (none while the brand awaits approval).
    access = caller.access
    out = {"assets": 0, "products_to_review": 0, "invoices_due": 0}
    if access["invoices"]:
        out["invoices_due"] = sum(1 for invoice, portal in _visible(db, caller).all() if is_due(invoice, portal))
    if not access["products"]:
        return out
    assets = db.query(PortalAsset.id).filter(
        PortalAsset.brand_id == caller.brand.id, PortalAsset.is_deleted.is_(False),
    ).count()
    # "Generated" exactly as routers/products.py's status_of defines it.
    to_review = (
        db.query(BrandItem.id)
        .join(BrandItemType, BrandItem.item_type_id == BrandItemType.id)
        .filter(
            BrandItemType.brand_name == caller.brand_key, BrandItemType.is_deleted.is_(False),
            BrandItem.is_deleted.is_(False), BrandItem.is_marked_generated.is_(False), BrandItem.is_sent_back.is_(False),
            BrandItem.id.in_(db.query(BrandItemGeneratedImage.item_id).distinct()),
        ).count()
    )
    out.update(assets=assets, products_to_review=to_review)
    return out
