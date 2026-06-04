from fastapi import FastAPI, HTTPException, Depends, Header
from pydantic import BaseModel
import anthropic
import sqlite3
import os
import jwt
import bcrypt
import platform
from datetime import datetime, timedelta
from dotenv import load_dotenv
import resend
import secrets

load_dotenv()
resend.api_key = os.getenv("re_AVTiNYTf_MNafHirFxYg4cKowQZgUcLSF")
FRONTEND_URL = "http://100.53.1.66:8000"

app = FastAPI(title="Recall API")

ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY")
JWT_SECRET = os.getenv("JWT_SECRET", "recall-secret-key")
client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

# ══════════════════════════════════════
# DATABASE
# ══════════════════════════════════════

def get_db():
    conn = sqlite3.connect('recall.db')
    conn.execute('''CREATE TABLE IF NOT EXISTS users
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                 email TEXT UNIQUE,
                 password TEXT,
                 plan TEXT DEFAULT 'free',
                 queries_used INTEGER DEFAULT 0,
                 is_verified INTEGER DEFAULT 0,
                 verification_token TEXT,
                 created_at DATETIME DEFAULT CURRENT_TIMESTAMP)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS memory
                 (user_id INTEGER,
                 query TEXT,
                 command TEXT,
                 timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')
    conn.commit()
    return conn

# ══════════════════════════════════════
# AUTH
# ══════════════════════════════════════

class SignupRequest(BaseModel):
    email: str
    password: str

class LoginRequest(BaseModel):
    email: str
    password: str

def create_token(user_id: int):
    payload = {
        "user_id": user_id,
        "exp": datetime.utcnow() + timedelta(days=30)
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")

def get_user_id(authorization: str = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401, detail="Not logged in")
    try:
        token = authorization.replace("Bearer ", "")
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        return payload["user_id"]
    except:
        raise HTTPException(status_code=401, detail="Invalid token")

@app.post("/signup")
def signup(req: SignupRequest):
    conn = get_db()
    hashed = bcrypt.hashpw(req.password.encode(), bcrypt.gensalt())
    verification_token = secrets.token_urlsafe(32)
    try:
        conn.execute(
            "INSERT INTO users (email, password, verification_token) VALUES (?, ?, ?)",
            (req.email, hashed.decode(), verification_token)
        )
        conn.commit()

        cursor = conn.execute(
            "SELECT id FROM users WHERE email=?", (req.email,)
        )
        user_id = cursor.fetchone()[0]

        # Send verification email
        verify_url = f"{FRONTEND_URL}/verify?token={verification_token}"
        resend.Emails.send({
            "from": "Recall <onboarding@resend.dev>",
            "to": req.email,
            "subject": "Verify your Recall account",
            "html": f"""
            <h2>Welcome to Recall!</h2>
            <p>Click the link below to verify your account:</p>
            <a href="{verify_url}" style="background:#2563EB;color:white;padding:12px 24px;border-radius:6px;text-decoration:none;">
                Verify Email
            </a>
            <p>If you did not create this account, ignore this email.</p>
            """
        })

        token = create_token(user_id)
        return {"message": "Account created! Check your email to verify.", "token": token}

    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Email already exists")
    finally:
        conn.close()

@app.get("/verify")
def verify_email(token: str):
    conn = get_db()
    cursor = conn.execute(
        "SELECT id FROM users WHERE verification_token=?", (token,)
    )
    user = cursor.fetchone()

    if not user:
        raise HTTPException(status_code=400, detail="Invalid verification token")

    conn.execute(
        "UPDATE users SET is_verified=1, verification_token=NULL WHERE id=?",
        (user[0],)
    )
    conn.commit()
    conn.close()

    return {"message": "Email verified! You can now use Recall."}

@app.post("/login")
def login(req: LoginRequest):
    conn = get_db()
    cursor = conn.execute(
        "SELECT id, password, plan, queries_used, is_verified FROM users WHERE email=?",
        (req.email,)
    )
    user = cursor.fetchone()
    conn.close()

    if not user:
        raise HTTPException(status_code=401, detail="Email not found")

    if not bcrypt.checkpw(req.password.encode(), user[1].encode()):
        raise HTTPException(status_code=401, detail="Wrong password")

    if not user[4]:
        raise HTTPException(status_code=403, detail="Please verify your email first")

    token = create_token(user[0])
    return {
        "token": token,
        "plan": user[2],
        "queries_used": user[3]
    }

# ══════════════════════════════════════
# QUERY
# ══════════════════════════════════════

class QueryRequest(BaseModel):
    query: str
    os_type: str = "unknown"

@app.post("/query")
def query(req: QueryRequest, user_id: int = Depends(get_user_id)):
    conn = get_db()

    # Check free limit
    cursor = conn.execute(
        "SELECT plan, queries_used FROM users WHERE id=?", (user_id,)
    )
    user = cursor.fetchone()

    if user[0] == 'free' and user[1] >= 50:
        conn.close()
        raise HTTPException(
            status_code=429,
            detail="Free limit reached. Upgrade to Pro — $12/month"
        )

    # Get user history
    cursor = conn.execute(
        "SELECT query, command FROM memory WHERE user_id=? ORDER BY timestamp DESC LIMIT 5",
        (user_id,)
    )
    history = cursor.fetchall()
    history_text = "\n".join(
        [f"- Asked: '{h[0]}' → Command: '{h[1]}'" for h in history]
    ) if history else "No history yet."

    # AI call
    message = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=200,
        messages=[{
            "role": "user",
            "content": f"""You are a terminal expert on {req.os_type}.

History:
{history_text}

User asks: '{req.query}'

Reply with ONLY the exact command. No explanation. No backticks."""
        }]
    )

    command = message.content[0].text.strip()

    # Save to memory
    conn.execute(
        "INSERT INTO memory VALUES (?, ?, ?, datetime('now'))",
        (user_id, req.query, command)
    )

    # Update query count
    conn.execute(
        "UPDATE users SET queries_used = queries_used + 1 WHERE id=?",
        (user_id,)
    )
    conn.commit()
    conn.close()

    return {"command": command}

# ══════════════════════════════════════
# HEALTH CHECK
# ══════════════════════════════════════

@app.get("/")
def root():
    return {"status": "Recall API running"}

# ══════════════════════════════════════
# FIX ERROR
# ══════════════════════════════════════

class ErrorRequest(BaseModel):
    error: str
    os_type: str = "unknown"

@app.post("/fix-error")
def fix_error_endpoint(req: ErrorRequest, user_id: int = Depends(get_user_id)):
    message = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=200,
        messages=[{
            "role": "user",
            "content": f"""Expert developer on {req.os_type}.
Error: '{req.error}'
Reply ONLY in JSON format, nothing else:
{{"cause": "one line", "fix": "exact command", "confidence": "High/Medium/Low"}}"""
        }]
    )
    import json
    try:
        data = json.loads(message.content[0].text)
    except:
        data = {
            "cause": "Unknown",
            "fix": message.content[0].text.strip(),
            "confidence": "Low"
        }
    return data