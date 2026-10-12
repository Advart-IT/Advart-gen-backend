# Advart Gen portal: backend

The API for the Advart Gen customer portal (`../advart-gen-frontend`). FastAPI + SQLAlchemy, on the same MySQL
`Ai` database the task management Design module uses.

## How it fits with the studio (task management)

| In the portal | In the database |
|---|---|
| A brand's workspace | `portal_brands` (new). Its `brand_key` is the studio's `brand_name`, e.g. `beelittle`. |
| A customer login | `portal_users` (new); its `brand_id` is its home brand |
| More brands for one login (a brand's employee who also works on its sister brand) | `portal_brand_members` (new) |
| Moodboard | `portal_assets` (new) |
| A product | the studio's own `brand_items` (+ `brand_item_types`, `brand_item_images`) |
| Generated images | the studio's `brand_item_generated_images` / `assets` |
| "Send back with notes" history | `portal_review_notes` (new); the latest note also goes to `brand_items.send_back_note` |
| An invoice | the studio's `brand_invoices` / `brand_invoice_lines`: every live one shows as soon as the studio makes it |
| Due date / paid | `portal_invoices` (new) |

Product status is the studio's own rule, so both apps always agree:

- **Pending**: no generated image saved yet, or sent back.
- **Generated**: at least one generated image saved, not sent back, not marked done. The customer reviews it.
- **Done**: `is_marked_generated` (set by the customer's "Mark as done", or by the studio).

A product a customer adds appears in the studio's Brand Submissions under that brand. For a brand that signed up
by itself, add the brand in task management ("+ Add New Brand") and give staff access to it, as for any brand.

**Sign-up and approval.** The sign-up form is saved as it's typed (`portal_signup_leads`), so a sign-up never finished
still shows in the Dot App. A brand that signs up by itself starts *pending*: its people can sign in but see only "We're
reviewing your brand" until an admin accepts it in the Dot App's **Customers** page (task management's
`Design/portal_admin.py`). Accepting also adds it to the studio's brand list and to that admin's Brand Submissions
dropdown. Brands set up by staff start approved.

**Who can do what** (`portal_brand_members`, one row per login and brand): *admin* (whoever signed the brand up) has
everything, including Invoices, the Team page (inviting teammates) and the company details; a *member* has Products
(with the Moodboard) and/or Invoices. A teammate the brand's admin invites gets Products only; Invoices for a member,
roles, other brands and deactivating a login are all done in the Dot App's Customers page.

One login can work on several brands (e.g. a BeeLittle employee on both `beelittle` and `indie by beelittle`). It
signs in to its home brand and switches with the Brand menu at the top of the sidebar; everything it sees and adds
is then that brand's. The brand being worked in travels in the login cookie, and each page also sends the brand it
is showing, so a tab left on the old brand after a switch in another tab reloads instead of acting on the new one.

## Set up

```
python -m venv venv
venv\Scripts\pip install -r requirements.txt
copy .env.example .env        # then fill it in
venv\Scripts\python scripts\create_tables.py           # shows which portal_* tables it would create
venv\Scripts\python scripts\create_tables.py --apply   # creates them (never touches the studio's tables)
venv\Scripts\uvicorn main:app --port 8100
```

Images use the studio's own Google service account, loaded exactly as task management's `Design/gcs.py` does:
copy `task-management-system/key/auth.py` (a dict, `AUTH = {...}`) to `key/auth.py` here. It is git-ignored.
Every image is shown through a signed link made from its stored path (12 hours here; the studio uses 7 days).

## Day to day

```
# Usually done in the Dot App (Customers). From here: give someone a login to a brand that already exists in the
# studio (sign-up refuses those names on purpose); --role member [--no-invoices] for less than full access:
venv\Scripts\python scripts\create_user.py --brand "beelittle" --company "BeeLittle" --name "Asha Rao" --email asha@beelittle.com
# Let that login switch to another brand as well (run once per extra brand):
venv\Scripts\python scripts\create_user.py --brand "indie by beelittle" --company "Indie by BeeLittle" --email asha@beelittle.com

# Invoices show up by themselves (delete one in the studio to hide it). "Pay now" = the customer sends a screenshot of
# their payment + a note, and the invoice shows "Payment sent". An admin checks it in the studio (Brand Submissions ->
# Invoices -> Payment: Approve / Reject) and only then is it paid. The same from here, if needed:
venv\Scripts\python scripts\update_invoice.py INV/26-27/0004
venv\Scripts\python scripts\update_invoice.py INV/26-27/0004 --paid "Bank transfer" --reference UTR123
# Change a due date:
venv\Scripts\python scripts\update_invoice.py INV/26-27/0004 --due 2026-10-31
```

"Download PDF" is a real PDF built by the backend (`invoice_pdf.py`, fpdf2, with Noto Sans from `fonts/` for the ₹).

## Optional pieces (off until configured in `.env`)

- **Email** (`SMTP_*`): verification codes and reset links. In development without SMTP they are printed to the console.
- **Google sign-in** (`GOOGLE_CLIENT_ID`): signs in an existing account whose email matches.
- **Online payments** (`RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`): the Razorpay Checkout routes in `routers/invoices.py`
  are kept, but the page no longer uses them ("Pay now" sends a screenshot instead). Off without keys.

## Tests

```
venv\Scripts\python -m pytest tests -q
```

They run on a throwaway SQLite database and a temporary image folder, never on MySQL or the bucket. The `tests/`
folder is kept on the developer's machine and is not part of this repository.
