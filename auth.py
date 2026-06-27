import jwt
import bcrypt
from datetime import datetime, timedelta
from fastapi import Header, HTTPException
from config import JWT_SECRET

def create_token(user_id: int) -> str:
    payload = {
        "user_id": user_id,
        "exp": datetime.utcnow() + timedelta(days=30)
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")

def get_user_id(authorization: str = Header(None)) -> int:
    if not authorization:
        raise HTTPException(status_code=401, detail="Not logged in")
    try:
        token = authorization.replace("Bearer ", "")
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        return payload["user_id"]
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")

def admin_auth(password: str = Header(None, alias="X-Admin-Password")) -> bool:
    from config import ADMIN_PASSWORD
    if password != ADMIN_PASSWORD:
        raise HTTPException(status_code=403, detail="Invalid admin password")
    return True

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())