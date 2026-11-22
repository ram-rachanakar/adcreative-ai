from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import uuid

from app.api import auth, creatives, editor, jobs, marketing, product, quality, resources, templates
from app.core.config import get_settings

app = FastAPI(title="AdCreative AI", version="0.1.0")
app.add_middleware(
    CORSMiddleware, allow_origins=get_settings().cors_origins.split(","), allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)
app.include_router(auth.router)
app.include_router(resources.router)
app.include_router(product.router)
app.include_router(marketing.router)
app.include_router(creatives.router)
app.include_router(jobs.router)
app.include_router(quality.router)
app.include_router(editor.router)
app.include_router(templates.router)


@app.middleware("http")
async def request_id(request, call_next):
    request.state.request_id = request.headers.get("x-request-id", "")[:64] or uuid.uuid4().hex[:16]
    response = await call_next(request)
    response.headers["x-request-id"] = request.state.request_id
    return response


@app.get("/health")
def health():
    return {"status": "ok"}
