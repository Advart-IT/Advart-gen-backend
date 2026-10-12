"""
Gives someone a login to a brand's workspace, or adds another brand to a login they already have. This is the only
way onto a brand that ALREADY exists in the studio (e.g. "beelittle") besides the Dot App (task management's
Design/portal_admin.py): the sign-up page refuses such names, because whoever typed one would see that brand's
products and invoices.

    python scripts/create_user.py --brand "beelittle" --company "BeeLittle" --name "Asha Rao" --email asha@beelittle.com
    python scripts/create_user.py --brand "indie by beelittle" --company "Indie by BeeLittle" --email asha@beelittle.com
    python scripts/create_user.py --brand "beelittle" --name "Ravi" --email ravi@beelittle.com --role member --no-invoices

--brand is the studio's brand key (brand_item_types.brand_name). The workspace is created the first time (approved:
staff set it up); later runs add more people to it. --role admin (the default) has every access, including Invoices
and inviting teammates; --role member has Products, plus Invoices unless --no-invoices. A new login's first brand is
its home brand (where it lands after signing in); running again with the same email and another --brand lets it
switch to that brand too. A new person gets no password here: they set one with "Forgot password?" on the sign-in
page, which also proves the email is theirs.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import func

from database import SessionLocal
from models import ROLE_ADMIN, ROLE_MEMBER, BrandItemType, PortalBrand, PortalBrandMember, PortalUser

parser = argparse.ArgumentParser()
parser.add_argument("--brand", required=True, help="the studio's brand key, e.g. beelittle")
parser.add_argument("--company", help="name shown to the customer (needed the first time a brand is set up)")
parser.add_argument("--name", help="the person's name (needed for a new login)")
parser.add_argument("--email", required=True)
parser.add_argument("--phone", default="")
parser.add_argument("--role", choices=(ROLE_ADMIN, ROLE_MEMBER), default=ROLE_ADMIN)
parser.add_argument("--no-invoices", action="store_true", help="a member without Invoices access")
args = parser.parse_args()

db = SessionLocal()
key = " ".join(args.brand.lower().split())
email = args.email.strip().lower()
user = db.query(PortalUser).filter(func.lower(PortalUser.email) == email).first()
if not user and not (args.name or "").strip():
    sys.exit("--name is needed for a new login.")

brand = db.query(PortalBrand).filter(PortalBrand.brand_key == key).first()
if not brand:
    items = db.query(BrandItemType.id).filter(func.lower(BrandItemType.brand_name) == key).count()
    if not items:
        print(f'Note: the studio has no item types under "{key}" yet. Check the spelling if you expected some.')
    brand = PortalBrand(brand_key=key, company_name=args.company or args.brand)
    db.add(brand)
    db.flush()
    print(f'Created workspace "{brand.company_name}" for brand key "{key}".')

if user and db.query(PortalBrandMember.id).filter(
        PortalBrandMember.user_id == user.id, PortalBrandMember.brand_id == brand.id).first():
    sys.exit(f'{email} can already use "{brand.company_name}". Nothing changed (change access in the Dot App).')
if not user:
    user = PortalUser(brand_id=brand.id, name=args.name.strip(), email=email, phone=args.phone.strip(),
                      password_hash=None, email_verified=False)
    db.add(user)
    db.flush()
db.add(PortalBrandMember(user_id=user.id, brand_id=brand.id, role=args.role, can_products=True,
                         can_invoices=args.role == ROLE_ADMIN or not args.no_invoices))
db.commit()
access = "everything" if args.role == ROLE_ADMIN else "Products" + ("" if args.no_invoices else " and Invoices")
print(f'{email} can use "{brand.company_name}" as {args.role} ({access}).'
      + (' They set their password with "Forgot password?" on the sign-in page.' if user.password_hash is None else ""))
db.close()
