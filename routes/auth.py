import sqlite3
from fastapi import APIRouter, HTTPException
from database import get_db
from auth import create_token, hash_password, verify_password
from models import SignupRequest, LoginRequest

router = APIRouter()

@router.post("/signup")
def signup(req: SignupRequest):
    if not req.email or "@" not in req.email:
        raise HTTPException(status_code=400, detail="Invalid email")
    if len(req.password) < 6:
        raise HTTPException(status_code=400, detail="Password too short (min 6 chars)")

    conn = get_db()
    try:
        cursor = conn.execute(
            "INSERT INTO users (email, password) VALUES (?, ?)",
            (req.email.strip().lower(), hash_password(req.password))
        )
        conn.commit()
        token = create_token(cursor.lastrowid)
        return {"token": token, "message": "Welcome to Recall!", "plan": "free"}
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Email already exists")
    finally:
        conn.close()

@router.post("/login")
def login(req: LoginRequest):
    conn = get_db()
    cursor = conn.execute(
        "SELECT id, password, plan, queries_used, is_banned FROM users WHERE email=?",
        (req.email.strip().lower(),)
    )
    user = cursor.fetchone()
    conn.close()

    if not user:
        raise HTTPException(status_code=401, detail="Email not found")
    if not verify_password(req.password, user["password"]):
        raise HTTPException(status_code=401, detail="Wrong password")
    if user["is_banned"]:
        raise HTTPException(status_code=403, detail="Account banned")

    return {
        "token": create_token(user["id"]),
        "plan": user["plan"],
        "queries_used": user["queries_used"]
    }