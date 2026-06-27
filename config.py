import os
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY")
JWT_SECRET = os.getenv("JWT_SECRET", "recall-secret-key-2026")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
STRIPE_PRICE_ID = os.getenv("STRIPE_PRICE_ID", "price_1TfRiDIjAxibchSEuB7L0Iy8")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
BASE_URL = os.getenv("BASE_URL", "http://100.53.1.66:8000")
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY")
FREE_QUERY_LIMIT = 50