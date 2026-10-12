"""
Advart Gen customer portal: the API.

Run:  uvicorn main:app --port 8100 --reload
Every route lives under /api, which is the path the frontend proxies to this server (so the browser only ever
talks to the frontend's own address and the login cookie stays first-party).
"""
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import config
from routers import auth, invoices, moodboard, products, settings, summary, team, uploads

logging.basicConfig(level=logging.INFO)

app = FastAPI(root_path="/backend")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[config.FRONTEND_URL],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for module in (auth, settings, summary, team, uploads, moodboard, products, invoices):
    app.include_router(module.router, prefix="/api")


@app.get("/api/health")
def health():
    return {"ok": True}
