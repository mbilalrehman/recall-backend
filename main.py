from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from fastapi.responses import HTMLResponse
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
import stripe

load_dotenv()

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
STRIPE_PRICE_ID = "price_1TfRiDIjAxibchSEuB7L0Iy8"

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
                 is_banned INTEGER DEFAULT 0,
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
    try:
        cursor = conn.execute(
            "INSERT INTO users (email, password) VALUES (?, ?)",
            (req.email, hashed.decode())
        )
        conn.commit()
        user_id = cursor.lastrowid
        token = create_token(user_id)
        return {"token": token, "message": "Welcome to Recall!"}
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

    cursor = conn.execute(
        "SELECT plan, queries_used, is_banned FROM users WHERE id=?", (user_id,)
    )
    user = cursor.fetchone()

    # Ban check
    if user[2] == 1:
        conn.close()
        raise HTTPException(
            status_code=403,
            detail="Your account has been banned."
        )

    # Free limit — 50 queries
    if user[0] == 'free' and user[1] >= 50:
        conn.close()
        raise HTTPException(
            status_code=429,
            detail="Free limit reached (50/month). Upgrade: recall --upgrade"
        )

    # Pro limit — unlimited
    # Get history
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
            History: {history_text}
            User asks: '{req.query}'
            Reply with ONLY the exact command. No explanation. No backticks."""
        }]
    )

    command = message.content[0].text.strip()

    conn.execute(
        "INSERT INTO memory VALUES (?, ?, ?, datetime('now'))",
        (user_id, req.query, command)
    )
    conn.execute(
        "UPDATE users SET queries_used = queries_used + 1 WHERE id=?",
        (user_id,)
    )
    conn.commit()
    conn.close()

    return {
        "command": command,
        "plan": user[0],
        "queries_used": user[1] + 1,
        "limit": "unlimited" if user[0] == 'pro' else f"{user[1]+1}/50"
    }

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

# ══════════════════════════════════════
# ADMIN PANEL
# ══════════════════════════════════════

from fastapi.responses import HTMLResponse

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")

def admin_auth(password: str = Header(None, alias="X-Admin-Password")):
    if password != ADMIN_PASSWORD:
        raise HTTPException(status_code=403, detail="Invalid admin password")
    return True

@app.get("/admin", response_class=HTMLResponse)
def admin_panel():
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Recall Admin</title>
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body { font-family: Arial, sans-serif; background: #0f172a; color: #e2e8f0; }
            .header { background: #1e3a8a; padding: 20px 40px; display: flex; justify-content: space-between; align-items: center; }
            .header h1 { color: #38bdf8; font-size: 24px; }
            .login-box { max-width: 400px; margin: 100px auto; background: #1e293b; padding: 40px; border-radius: 12px; }
            .login-box h2 { margin-bottom: 20px; color: #38bdf8; }
            input { width: 100%; padding: 12px; margin: 10px 0; background: #0f172a; border: 1px solid #334155; border-radius: 8px; color: white; font-size: 16px; }
            button { width: 100%; padding: 12px; background: #2563eb; color: white; border: none; border-radius: 8px; font-size: 16px; cursor: pointer; margin-top: 10px; }
            button:hover { background: #1d4ed8; }
            .dashboard { display: none; padding: 40px; }
            .stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 20px; margin-bottom: 40px; }
            .stat-card { background: #1e293b; padding: 24px; border-radius: 12px; border-left: 4px solid #2563eb; }
            .stat-card h3 { color: #94a3b8; font-size: 14px; margin-bottom: 8px; }
            .stat-card .number { font-size: 32px; font-weight: bold; color: #38bdf8; }
            table { width: 100%; background: #1e293b; border-radius: 12px; overflow: hidden; border-collapse: collapse; }
            th { background: #1e3a8a; padding: 16px; text-align: left; color: #94a3b8; font-size: 14px; }
            td { padding: 16px; border-bottom: 1px solid #334155; }
            .badge { padding: 4px 12px; border-radius: 20px; font-size: 12px; font-weight: bold; }
            .free { background: #1e3a8a; color: #38bdf8; }
            .pro { background: #14532d; color: #4ade80; }
            .ban-btn { background: #dc2626; color: white; border: none; padding: 6px 16px; border-radius: 6px; cursor: pointer; font-size: 12px; }
            .unban-btn { background: #16a34a; color: white; border: none; padding: 6px 16px; border-radius: 6px; cursor: pointer; font-size: 12px; }
            .section-title { font-size: 20px; font-weight: bold; margin-bottom: 20px; color: #e2e8f0; }
        </style>
    </head>
    <body>
        <div class="header">
            <h1>⚡ Recall Admin</h1>
            <span id="admin-email" style="color:#94a3b8"></span>
        </div>

        <div class="login-box" id="login-box">
            <h2>Admin Login</h2>
            <input type="password" id="password" placeholder="Admin Password" />
            <button onclick="login()">Login</button>
            <p id="error" style="color:red;margin-top:10px"></p>
        </div>

        <div class="dashboard" id="dashboard">
            <div class="stats" id="stats"></div>
            <div class="section-title">All Users</div>
            <table>
                <thead>
                    <tr>
                        <th>#</th>
                        <th>Email</th>
                        <th>Plan</th>
                        <th>Queries</th>
                        <th>Joined</th>
                        <th>Action</th>
                    </tr>
                </thead>
                <tbody id="users-table"></tbody>
            </table>
        </div>

        <script>
            let adminPass = '';

            async function login() {
                adminPass = document.getElementById('password').value;
                const res = await fetch('/admin/stats', {
                    headers: { 'X-Admin-Password': adminPass }
                });
                if (res.ok) {
                    document.getElementById('login-box').style.display = 'none';
                    document.getElementById('dashboard').style.display = 'block';
                    loadDashboard();
                } else {
                    document.getElementById('error').innerText = 'Wrong password!';
                }
            }

            async function loadDashboard() {
                const stats = await fetch('/admin/stats', {
                    headers: { 'X-Admin-Password': adminPass }
                }).then(r => r.json());

                document.getElementById('stats').innerHTML = `
                    <div class="stat-card"><h3>Total Users</h3><div class="number">${stats.total_users}</div></div>
                    <div class="stat-card"><h3>Total Queries</h3><div class="number">${stats.total_queries}</div></div>
                    <div class="stat-card"><h3>Pro Users</h3><div class="number">${stats.pro_users}</div></div>
                    <div class="stat-card"><h3>Today Queries</h3><div class="number">${stats.today_queries}</div></div>
                `;

                const users = await fetch('/admin/users', {
                    headers: { 'X-Admin-Password': adminPass }
                }).then(r => r.json());

                document.getElementById('users-table').innerHTML = users.map((u, i) => `
                    <tr>
                        <td>${i+1}</td>
                        <td>${u.email}</td>
                        <td><span class="badge ${u.plan}">${u.plan}</span></td>
                        <td>${u.queries_used}</td>
                        <td>${u.created_at}</td>
                        <td>
                            ${u.is_banned ? 
                                `<button class="unban-btn" onclick="banUser(${u.id}, false)">Unban</button>` :
                                `<button class="ban-btn" onclick="banUser(${u.id}, true)">Ban</button>`
                            }
                        </td>
                    </tr>
                `).join('');
            }

            async function banUser(userId, ban) {
                await fetch('/admin/ban', {
                    method: 'POST',
                    headers: { 'X-Admin-Password': adminPass, 'Content-Type': 'application/json' },
                    body: JSON.stringify({ user_id: userId, ban: ban })
                });
                loadDashboard();
            }

            document.getElementById('password').addEventListener('keypress', function(e) {
                if (e.key === 'Enter') login();
            });
        </script>
    </body>
    </html>
    """

@app.get("/admin/stats")
def admin_stats(_ = Depends(admin_auth)):
    conn = get_db()
    total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    total_queries = conn.execute("SELECT SUM(queries_used) FROM users").fetchone()[0] or 0
    pro_users = conn.execute("SELECT COUNT(*) FROM users WHERE plan='pro'").fetchone()[0]
    today_queries = conn.execute(
        "SELECT COUNT(*) FROM memory WHERE date(timestamp) = date('now')"
    ).fetchone()[0]
    conn.close()
    return {
        "total_users": total_users,
        "total_queries": total_queries,
        "pro_users": pro_users,
        "today_queries": today_queries
    }

@app.get("/admin/users")
def admin_users(_ = Depends(admin_auth)):
    conn = get_db()
    cursor = conn.execute(
        "SELECT id, email, plan, queries_used, is_banned, created_at FROM users ORDER BY created_at DESC"
    )
    users = []
    for row in cursor.fetchall():
        users.append({
            "id": row[0],
            "email": row[1],
            "plan": row[2],
            "queries_used": row[3],
            "is_banned": row[4],
            "created_at": row[5]
        })
    conn.close()
    return users

class BanRequest(BaseModel):
    user_id: int
    ban: bool

@app.post("/admin/ban")
def ban_user(req: BanRequest, _ = Depends(admin_auth)):
    conn = get_db()
    conn.execute(
        "UPDATE users SET is_banned=? WHERE id=?",
        (1 if req.ban else 0, req.user_id)
    )
    conn.commit()
    conn.close()
    return {"message": "Done"}


# ══════════════════════════════════════
# STRIPE PAYMENTS
# ══════════════════════════════════════

@app.post("/create-checkout")
def create_checkout(user_id: int = Depends(get_user_id)):
    try:
        # User email lo
        conn = get_db()
        cursor = conn.execute("SELECT email FROM users WHERE id=?", (user_id,))
        user = cursor.fetchone()
        conn.close()

        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            customer_email=user[0],  # email add karo
            line_items=[{
                "price": STRIPE_PRICE_ID,
                "quantity": 1,
            }],
            mode="subscription",
            success_url="http://100.53.1.66:8000/payment-success?session_id={CHECKOUT_SESSION_ID}",
            cancel_url="http://100.53.1.66:8000/payment-cancel",
            metadata={"user_id": str(user_id)}
        )
        return {"checkout_url": session.url}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/payment-success", response_class=HTMLResponse)
def payment_success(session_id: str = None):
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Payment Successful</title>
        <style>
            body { font-family: Arial; background: #0f172a; color: white; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; }
            .box { text-align: center; background: #1e293b; padding: 60px; border-radius: 16px; }
        </style>
    </head>
    <body>
        <div class="box">
            <h1>🎉</h1>
            <h2 style="color:#4ade80">Payment Successful!</h2>
            <p style="color:#94a3b8">You are now a PRO member</p>
            <p style="color:#64748b">Close this tab and continue using Recall.</p>
        </div>
    </body>
    </html>
    """

@app.get("/payment-cancel")
def payment_cancel():
    return {"message": "Payment cancelled."}