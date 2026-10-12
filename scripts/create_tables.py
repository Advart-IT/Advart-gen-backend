"""
Creates the portal's own tables (portal_*) in the "Ai" database. Safe to run again: existing tables are left
exactly as they are, and the studio's tables are never created or changed.

    python scripts/create_tables.py           # shows what it would create
    python scripts/create_tables.py --apply   # creates them
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import inspect

from database import engine
from models import PORTAL_TABLES, Base

existing = set(inspect(engine).get_table_names())
missing = [table for table in PORTAL_TABLES if table.name not in existing]
print("Database:", engine.url.render_as_string(hide_password=True))
print("Already there:", ", ".join(t.name for t in PORTAL_TABLES if t.name in existing) or "none")
print("To create:    ", ", ".join(t.name for t in missing) or "none")

if "--apply" not in sys.argv:
    print("\nNothing was changed. Run again with --apply to create them.")
elif missing:
    Base.metadata.create_all(engine, tables=missing)
    print("\nCreated.")
