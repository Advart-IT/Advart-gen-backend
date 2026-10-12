"""
Tables this app reads and writes, all in the "Ai" database.

Two groups:

1. STUDIO tables that already exist and belong to the task management Design module (Design/models.py and
   Design/brand_invoices.py there). They are declared here only so this app can query them; this app never
   creates or alters them. Only the columns the portal needs are listed.

   A customer's "product" IS a studio Brand Submissions item (brand_items): the customer uploads it here, the
   studio generates its images in task management, and the customer reviews them here. A brand is the free-text
   brand_item_types.brand_name key the studio already uses (e.g. "beelittle").

2. PORTAL tables (portal_*), which are new and owned by this app: customer logins, the moodboard, the history of
   review notes, staged uploads, and what the portal knows about an invoice (due, paid).
   scripts/create_tables.py creates exactly these and nothing else.
"""
from sqlalchemy import (
    JSON, BigInteger, Boolean, Column, Date, DateTime, DECIMAL, ForeignKey, Index, Integer, String, Text,
    UniqueConstraint, func,
)
from sqlalchemy.orm import declarative_base

Base = declarative_base()

# BIGINT in MySQL; plain INTEGER in the SQLite test database, where only INTEGER primary keys auto-increment.
ID = BigInteger().with_variant(Integer, "sqlite")


# ---- Studio tables (owned by task management; never created here) ----------------------------------------------

class BrandItemType(Base):
    __tablename__ = "brand_item_types"

    id = Column(ID, primary_key=True, autoincrement=True)
    brand_name = Column(String(255), nullable=False)
    name = Column(String(255), nullable=False)
    created_by = Column(BigInteger, nullable=False)
    is_deleted = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, server_default=func.current_timestamp())


class BrandItem(Base):
    __tablename__ = "brand_items"

    id = Column(ID, primary_key=True, autoincrement=True)
    item_type_id = Column(BigInteger, ForeignKey("brand_item_types.id"), nullable=False)
    name = Column(String(255), nullable=False)
    # What the customer wants for this product, in their own words (Add product's description box). The studio
    # shows it in Brand Submissions and adds it to the Generate brief.
    description = Column(Text, nullable=True)
    available_ages = Column(JSON, nullable=False, default=list)
    created_by = Column(BigInteger, nullable=False)
    is_deleted = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, server_default=func.current_timestamp())
    send_back_note = Column(Text, nullable=True)
    is_marked_generated = Column(Boolean, nullable=False, default=False)
    is_sent_back = Column(Boolean, nullable=False, default=False)


class BrandItemImage(Base):
    """A photo of the product itself, uploaded by the brand."""
    __tablename__ = "brand_item_images"

    id = Column(ID, primary_key=True, autoincrement=True)
    item_id = Column(BigInteger, ForeignKey("brand_items.id"), nullable=False)
    gcs_path = Column(String(1024), nullable=False)
    created_at = Column(DateTime, server_default=func.current_timestamp())


class BrandItemGeneratedImage(Base):
    """A shot the studio saved onto the item: AI generated (asset_id) or uploaded by hand (gcs_path)."""
    __tablename__ = "brand_item_generated_images"

    id = Column(ID, primary_key=True, autoincrement=True)
    item_id = Column(BigInteger, ForeignKey("brand_items.id"), nullable=False)
    asset_id = Column(BigInteger, ForeignKey("assets.id"), nullable=True)
    gcs_path = Column(String(1024), nullable=True)
    shot_type = Column(String(64), nullable=False, default="hero")
    generated_by = Column(BigInteger, nullable=False)
    generated_at = Column(DateTime, server_default=func.current_timestamp())


class DesignAsset(Base):
    __tablename__ = "assets"

    id = Column(ID, primary_key=True, autoincrement=True)
    storage_url = Column(String(1024), nullable=False)


class BrandInvoice(Base):
    __tablename__ = "brand_invoices"

    id = Column(ID, primary_key=True, autoincrement=True)
    invoice_number = Column(String(64), nullable=False, unique=True)
    brand_name = Column(String(255), nullable=False)
    invoice_date = Column(Date, nullable=False)
    profit_margin_pct = Column(DECIMAL(5, 2), nullable=False)
    gst_pct = Column(DECIMAL(5, 2), nullable=False)
    usd_to_inr_rate = Column(DECIMAL(10, 4), nullable=False)
    bill_discount_type = Column(String(16), nullable=False, default="none")
    bill_discount_value = Column(DECIMAL(12, 2), nullable=False, default=0)
    waive_gst = Column(Boolean, nullable=False, default=False)
    bill_to_name = Column(String(255), nullable=True)
    bill_to_address = Column(Text, nullable=True)
    bill_to_gstin = Column(String(32), nullable=True)
    seller_name = Column(String(255), nullable=True)
    seller_address = Column(Text, nullable=True)
    seller_gstin = Column(String(32), nullable=True)
    seller_phone = Column(String(64), nullable=True)
    seller_email = Column(String(255), nullable=True)
    notes = Column(Text, nullable=True)
    is_deleted = Column(Boolean, nullable=False, default=False)


class BrandInvoiceLine(Base):
    __tablename__ = "brand_invoice_lines"

    id = Column(ID, primary_key=True, autoincrement=True)
    invoice_id = Column(BigInteger, ForeignKey("brand_invoices.id"), nullable=False)
    item_id = Column(BigInteger, nullable=False)
    item_name = Column(String(255), nullable=False)
    item_type_name = Column(String(255), nullable=True)
    images_finalised = Column(Integer, nullable=False, default=0)
    cost_usd = Column(DECIMAL(12, 4), nullable=False, default=0)
    discount_type = Column(String(16), nullable=False, default="none")
    discount_value = Column(DECIMAL(12, 2), nullable=False, default=0)


# ---- Portal tables (owned by this app) ---------------------------------------------------------------------------

BRAND_PENDING, BRAND_ACTIVE, BRAND_REJECTED = "pending", "active", "rejected"


class PortalBrand(Base):
    """One customer workspace. brand_key is the studio's brand_name: it links the workspace to its items and
    invoices and never changes; company_name is what the customer sees and can edit.

    A brand that signs up by itself starts "pending": its people can sign in but use nothing until an admin
    accepts it in the Dot App (task management's Design/portal_admin.py), which also adds it to the studio's
    brand list so it appears in Brand Submissions. Brands set up by staff start "active"."""
    __tablename__ = "portal_brands"

    id = Column(ID, primary_key=True, autoincrement=True)
    brand_key = Column(String(255), nullable=False, unique=True)
    company_name = Column(String(255), nullable=False)
    status = Column(String(16), nullable=False, default=BRAND_ACTIVE)  # "pending" | "active" | "rejected"
    reviewed_at = Column(DateTime, nullable=True)
    review_note = Column(Text, nullable=True)  # why it was rejected (shown to the brand)
    gstin = Column(String(32), nullable=True)
    address = Column(Text, nullable=True)
    city = Column(String(128), nullable=True)
    state = Column(String(128), nullable=True)
    pin = Column(String(16), nullable=True)
    created_at = Column(DateTime, server_default=func.current_timestamp())
    updated_at = Column(DateTime, server_default=func.current_timestamp(), onupdate=func.current_timestamp())


class PortalUser(Base):
    """A login. Which brands it can use, and what it can do in each, is its PortalBrandMember rows; is_active off
    (the Dot App's Deactivate) shuts it out of everything."""
    __tablename__ = "portal_users"

    id = Column(ID, primary_key=True, autoincrement=True)
    brand_id = Column(BigInteger, ForeignKey("portal_brands.id"), nullable=False)  # home brand: where sign-in lands
    name = Column(String(255), nullable=False)
    email = Column(String(255), nullable=False, unique=True)
    phone = Column(String(64), nullable=True)
    password_hash = Column(String(255), nullable=True)  # NULL = no password yet (staff-created account)
    email_verified = Column(Boolean, nullable=False, default=False)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, server_default=func.current_timestamp())
    last_login_at = Column(DateTime, nullable=True)

    __table_args__ = (Index("idx_portal_users_brand", "brand_id"),)


ROLE_ADMIN, ROLE_MEMBER = "admin", "member"


class PortalBrandMember(Base):
    """A login's access to one brand: every brand it can use has a row here, its home brand included (e.g. a BeeLittle
    employee who also works on Indie by BeeLittle has two).

    role "admin" (whoever signed the brand up, or anyone staff make one): everything, including Invoices, inviting
    teammates and the company details. role "member": Products (with the moodboard) and/or Invoices, as can_products
    and can_invoices say. A teammate a brand admin invites gets Products only; Invoices for a member is granted from
    the Dot App alone."""
    __tablename__ = "portal_brand_members"

    id = Column(ID, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("portal_users.id"), nullable=False)
    brand_id = Column(BigInteger, ForeignKey("portal_brands.id"), nullable=False)
    role = Column(String(16), nullable=False, default=ROLE_MEMBER)
    can_products = Column(Boolean, nullable=False, default=True)
    can_invoices = Column(Boolean, nullable=False, default=False)
    invited_by = Column(BigInteger, nullable=True)  # portal_users.id of the brand admin who invited them
    created_at = Column(DateTime, server_default=func.current_timestamp())

    __table_args__ = (
        UniqueConstraint("user_id", "brand_id", name="uq_portal_brand_members"),
        Index("idx_portal_brand_members_brand", "brand_id"),
    )


class PortalSignupLead(Base):
    """Someone who started the sign-up form: saved as they type, so a sign-up that is never finished (or never
    verified) still shows up in the Dot App as a lead. user_id is set once they do create the account."""
    __tablename__ = "portal_signup_leads"

    id = Column(ID, primary_key=True, autoincrement=True)
    token_hash = Column(String(64), nullable=False, unique=True)  # the browser's key to keep updating its own row
    company = Column(String(255), nullable=True)
    contact_name = Column(String(255), nullable=True)
    phone = Column(String(64), nullable=True)
    email = Column(String(255), nullable=True)
    user_id = Column(BigInteger, nullable=True)
    created_at = Column(DateTime, server_default=func.current_timestamp())
    updated_at = Column(DateTime, server_default=func.current_timestamp(), onupdate=func.current_timestamp())


class PortalEmailCode(Base):
    """A 6-digit email verification code or a password reset token. Only its hash is stored."""
    __tablename__ = "portal_email_codes"

    id = Column(ID, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("portal_users.id"), nullable=False)
    purpose = Column(String(16), nullable=False)  # "verify" | "reset"
    code_hash = Column(String(64), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    attempts = Column(Integer, nullable=False, default=0)
    used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.current_timestamp())

    __table_args__ = (Index("idx_portal_email_codes_user", "user_id", "purpose"),)


class PortalUpload(Base):
    """An image the browser has finished uploading but that is not attached to anything yet (the forms upload
    each file as soon as it is picked, with its own progress bar, and attach them on save)."""
    __tablename__ = "portal_uploads"

    id = Column(ID, primary_key=True, autoincrement=True)
    brand_id = Column(BigInteger, ForeignKey("portal_brands.id"), nullable=False)
    user_id = Column(BigInteger, ForeignKey("portal_users.id"), nullable=False)
    storage_path = Column(String(1024), nullable=False)
    original_name = Column(String(255), nullable=True)
    size_bytes = Column(BigInteger, nullable=False, default=0)
    used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.current_timestamp())

    __table_args__ = (Index("idx_portal_uploads_brand", "brand_id"),)


class PortalAsset(Base):
    """A moodboard entry: a model, a background or an accessory. Any number of photos (the first is its cover) and/or
    a prompt in the customer's words describing it; at least one of the two."""
    __tablename__ = "portal_assets"

    id = Column(ID, primary_key=True, autoincrement=True)
    brand_id = Column(BigInteger, ForeignKey("portal_brands.id"), nullable=False)
    kind = Column(String(16), nullable=False)  # "model" | "background" | "accessory"
    name = Column(String(255), nullable=False)
    image_paths = Column(JSON, nullable=False, default=list)  # storage paths, in the customer's order
    prompt = Column(Text, nullable=True)
    is_favourite = Column(Boolean, nullable=False, default=False)
    age_group = Column(String(16), nullable=True)       # models: "kid" | "adult"
    gender = Column(String(32), nullable=True)          # models
    age_range = Column(String(32), nullable=True)       # models
    setting = Column(String(32), nullable=True)         # backgrounds
    accessory_type = Column(String(32), nullable=True)  # accessories
    is_deleted = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, server_default=func.current_timestamp())
    updated_at = Column(DateTime, server_default=func.current_timestamp(), onupdate=func.current_timestamp())

    __table_args__ = (Index("idx_portal_assets_brand_kind", "brand_id", "kind"),)


class PortalReviewNote(Base):
    """Every "Send back with notes" a customer made on a product. The studio's own brand_items.send_back_note
    holds only the latest one and is cleared when new images are saved; this keeps the history."""
    __tablename__ = "portal_review_notes"

    id = Column(ID, primary_key=True, autoincrement=True)
    # brand_items.id. Deliberately not a database FOREIGN KEY: a constraint from this app onto a studio table
    # could make one of the studio's own deletes fail.
    item_id = Column(BigInteger, nullable=False)
    brand_id = Column(BigInteger, ForeignKey("portal_brands.id"), nullable=False)
    user_id = Column(BigInteger, ForeignKey("portal_users.id"), nullable=False)
    text = Column(Text, nullable=True)
    tags = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime, server_default=func.current_timestamp())

    __table_args__ = (Index("idx_portal_review_notes_item", "item_id"),)


class PortalInvoice(Base):
    """What the portal adds to a studio invoice: a due date other than the default, the payment order, the payment.
    A customer sees every live studio invoice whether or not it has a row here; one is added when there is
    something to record (routers/invoices.py, scripts/update_invoice.py)."""
    __tablename__ = "portal_invoices"

    invoice_id = Column(BigInteger, primary_key=True, autoincrement=False)  # brand_invoices.id (no FK, as above)
    due_date = Column(Date, nullable=True)
    # "Pay now": the customer's screenshot of the payment and their note. An admin checks it in the studio (task
    # management's Brand Submissions -> Invoices, Design/brand_invoices.py) and approves it, which sets paid_at below,
    # or rejects it: proof_submitted_at is cleared and proof_rejected_note tells the customer why.
    proof_path = Column(String(1024), nullable=True)
    proof_note = Column(Text, nullable=True)
    proof_submitted_at = Column(DateTime, nullable=True)
    proof_rejected_note = Column(Text, nullable=True)
    paid_at = Column(DateTime, nullable=True)
    payment_method = Column(String(32), nullable=True)
    payment_reference = Column(String(128), nullable=True)
    gateway_order_id = Column(String(128), nullable=True)
    updated_at = Column(DateTime, server_default=func.current_timestamp(), onupdate=func.current_timestamp())


PORTAL_TABLES = [
    PortalBrand.__table__, PortalUser.__table__, PortalBrandMember.__table__, PortalSignupLead.__table__,
    PortalEmailCode.__table__, PortalUpload.__table__, PortalAsset.__table__, PortalReviewNote.__table__,
    PortalInvoice.__table__,
]
