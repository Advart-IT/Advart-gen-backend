"""Settings, all from the environment (.env). See .env.example for what each one is for."""
import os
import urllib.parse

from dotenv import load_dotenv

load_dotenv()

APP_ENV = os.getenv("APP_ENV", "production").strip().lower()
IS_DEV = APP_ENV == "development"

# ---- Database: the task management Design module's own "Ai" database ----
AI_DB_NAME = os.getenv("AI_DB_NAME", "Ai")


def database_url() -> str:
    # DATABASE_URL replaces the MySQL settings outright (the tests point it at SQLite).
    override = os.getenv("DATABASE_URL")
    if override:
        return override
    user = urllib.parse.quote_plus(os.getenv("DB_USER", ""))
    password = urllib.parse.quote_plus(os.getenv("DB_PASSWORD", ""))
    return f"mysql+pymysql://{user}:{password}@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{AI_DB_NAME}"


# ---- Login ----
JWT_SECRET = os.getenv("JWT_SECRET", "")
if not JWT_SECRET:
    # No built-in fallback: a known secret would let anyone forge a login for any brand.
    raise RuntimeError("JWT_SECRET is not set. Add it to .env (see .env.example).")
COOKIE_NAME = "advart_gen_session"
COOKIE_SECURE = not IS_DEV  # https only, except on a developer's own machine
SESSION_HOURS = 12          # without "Keep me signed in"
REMEMBER_DAYS = 30          # with it
VERIFY_CODE_MINUTES = 15
VERIFY_CODE_MAX_ATTEMPTS = 5
# For trying the app out with made-up email addresses: the "Verify your email" step accepts ANY 6 digits.
# Honoured only when APP_ENV=development, so it can never be switched on in production by this setting alone.
DEV_ACCEPT_ANY_CODE = IS_DEV and os.getenv("DEV_ACCEPT_ANY_CODE", "").strip().lower() == "true"
RESET_LINK_MINUTES = 30
INVITE_LINK_DAYS = 7      # a teammate's "choose your password" link (it is a reset link that lasts longer)

FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3100").rstrip("/")

# ---- Images ----
STORAGE_BACKEND = os.getenv("STORAGE_BACKEND", "gcs").strip().lower()
GCS_BUCKET = os.getenv("GCS_BUCKET", "advart")  # signed in with key/auth.py's AUTH, as the studio is (see storage.py)
LOCAL_STORAGE_DIR = os.getenv("LOCAL_STORAGE_DIR", "local_storage")
MAX_UPLOAD_BYTES = 20 * 1024 * 1024

# ---- Email ----
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
MAIL_FROM = os.getenv("MAIL_FROM", "") or SMTP_USER

# ---- Optional integrations ----
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "")

INVOICE_DUE_DAYS = int(os.getenv("INVOICE_DUE_DAYS", "15"))
