"""
Central configuration for HomerunIQ backend.
Loads from environment variables (.env file in local dev, dashboard vars in prod).
"""
import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    APP_ENV: str = os.getenv("APP_ENV", "development")
    FRONTEND_URL: str = os.getenv("FRONTEND_URL", "http://localhost:3000")

    # Supabase / Postgres
    SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
    SUPABASE_KEY: str = os.getenv("SUPABASE_KEY", "")

    # Redis
    REDIS_URL: str = os.getenv("REDIS_URL", "")

    # Stripe
    STRIPE_SECRET_KEY: str = os.getenv("STRIPE_SECRET_KEY", "")
    STRIPE_WEBHOOK_SECRET: str = os.getenv("STRIPE_WEBHOOK_SECRET", "")
    STRIPE_PRICE_PRO: str = os.getenv("STRIPE_PRICE_PRO", "")
    STRIPE_PRICE_ELITE: str = os.getenv("STRIPE_PRICE_ELITE", "")
    STRIPE_PRICE_EDGE: str = os.getenv("STRIPE_PRICE_EDGE", "")

    # Weather
    WEATHER_API_KEY: str = os.getenv("WEATHER_API_KEY", "")

    # Plan pricing (cents), for display/reference
    PLAN_PRICES = {"free": 0, "pro": 1900, "elite": 4900, "edge": 9900}

    PLAN_PRICE_IDS: dict = {}

    def refresh_price_ids(self):
        self.PLAN_PRICE_IDS = {
            "pro": self.STRIPE_PRICE_PRO,
            "elite": self.STRIPE_PRICE_ELITE,
            "edge": self.STRIPE_PRICE_EDGE,
        }


settings = Settings()
settings.refresh_price_ids()
