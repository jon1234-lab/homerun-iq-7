from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import is_supabase_enabled
from app.routers import games, players, predictions, stripe_routes
from app.routers import websocket as ws_router
from app.services.cache import cache_enabled_backend
from app.services.statcast import statcast_enabled

app = FastAPI(
    title="HomerunIQ API",
    description="Real-time MLB home run probability analytics.",
    version="2.0.0",
)

# Allow the configured frontend, localhost for dev, and any *.onrender.com /
# *.vercel.app preview URL (regex), so deploys work without reconfiguring CORS.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_URL, "http://localhost:3000"],
    allow_origin_regex=r"https://.*\.(onrender\.com|vercel\.app)",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(predictions.router)
app.include_router(games.router)
app.include_router(players.router)
app.include_router(stripe_routes.router)
app.include_router(ws_router.router)


@app.get("/")
def root():
    return {
        "service": "HomerunIQ API",
        "version": "2.0.0",
        "status": "ok",
        "database_backend": "supabase" if is_supabase_enabled() else "in-memory",
        "cache_backend": cache_enabled_backend(),
        "statcast_enabled": statcast_enabled(),
        "stripe_configured": bool(settings.STRIPE_SECRET_KEY),
        "docs": "/docs",
    }


@app.get("/health")
def health():
    return {"status": "healthy"}
