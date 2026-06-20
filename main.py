from fastapi import FastAPI, HTTPException, Depends, Header, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import anthropic
import sqlite3
import os
import jwt
import bcrypt
import stripe
import json
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

# ══════════════════════════════════════
# CONFIG
# ══════════════════════════════════════

app = FastAPI(title="Recall API")

ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY")
JWT_SECRET = os.getenv("JWT_SECRET", "recall-secret-key-2026")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
STRIPE_PRICE_ID = os.getenv("STRIPE_PRICE_ID", "price_1TfRiDIjAxibchSEuB7L0Iy8")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
BASE_URL = os.getenv("BASE_URL", "http://100.53.1.66:8000")

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

# ══════════════════════════════════════
# DATABASE
# ══════════════════════════════════════

def get_db():
    conn = sqlite3.connect('recall.db')
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            plan TEXT DEFAULT 'free',
            queries_used INTEGER DEFAULT 0,
            is_banned INTEGER DEFAULT 0,
            stripe_customer_id TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            query TEXT NOT NULL,
            command TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            stripe_subscription_id TEXT,
            status TEXT DEFAULT 'active',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
    ''')
    conn.commit()
    return conn

# ══════════════════════════════════════
# AUTH HELPERS
# ══════════════════════════════════════

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

def admin_auth(password: str = Header(None, alias="X-Admin-Password")):
    if password != ADMIN_PASSWORD:
        raise HTTPException(status_code=403, detail="Invalid admin password")
    return True

# ══════════════════════════════════════
# MODELS
# ══════════════════════════════════════

class SignupRequest(BaseModel):
    email: str
    password: str

class LoginRequest(BaseModel):
    email: str
    password: str

class QueryRequest(BaseModel):
    query: str
    os_type: str = "unknown"

class ErrorRequest(BaseModel):
    error: str
    os_type: str = "unknown"

class BanRequest(BaseModel):
    user_id: int
    ban: bool

class PlanRequest(BaseModel):
    user_id: int
    plan: str

# ══════════════════════════════════════
# AUTH ENDPOINTS
# ══════════════════════════════════════

@app.post("/signup")
def signup(req: SignupRequest):
    if not req.email or "@" not in req.email:
        raise HTTPException(status_code=400, detail="Invalid email")
    if len(req.password) < 6:
        raise HTTPException(status_code=400, detail="Password too short (min 6 chars)")

    conn = get_db()
    hashed = bcrypt.hashpw(req.password.encode(), bcrypt.gensalt())
    try:
        cursor = conn.execute(
            "INSERT INTO users (email, password) VALUES (?, ?)",
            (req.email.strip().lower(), hashed.decode())
        )
        conn.commit()
        user_id = cursor.lastrowid
        token = create_token(user_id)
        return {"token": token, "message": "Welcome to Recall!", "plan": "free"}
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Email already exists")
    finally:
        conn.close()

@app.post("/login")
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

    if not bcrypt.checkpw(req.password.encode(), user[1].encode()):
        raise HTTPException(status_code=401, detail="Wrong password")

    if user[4] == 1:
        raise HTTPException(status_code=403, detail="Account banned")

    token = create_token(user[0])
    return {
        "token": token,
        "plan": user[2],
        "queries_used": user[3]
    }

# ══════════════════════════════════════
# QUERY
# ══════════════════════════════════════

@app.post("/query")
def query(req: QueryRequest, user_id: int = Depends(get_user_id)):
    conn = get_db()
    cursor = conn.execute(
        "SELECT plan, queries_used, is_banned FROM users WHERE id=?", (user_id,)
    )
    user = cursor.fetchone()

    if not user:
        conn.close()
        raise HTTPException(status_code=404, detail="User not found")

    if user[2] == 1:
        conn.close()
        raise HTTPException(status_code=403, detail="Account banned")

    if user[0] == 'free' and user[1] >= 50:
        conn.close()
        raise HTTPException(
            status_code=429,
            detail="Free limit reached (50/month). Upgrade: recall --upgrade"
        )

    cursor = conn.execute(
        "SELECT query, command FROM memory WHERE user_id=? ORDER BY timestamp DESC LIMIT 5",
        (user_id,)
    )
    history = cursor.fetchall()
    history_text = "\n".join(
        [f"- Asked: '{h[0]}' → Command: '{h[1]}'" for h in history]
    ) if history else "No history yet."

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
# FIX ERROR
# ══════════════════════════════════════

@app.post("/fix-error")
def fix_error_endpoint(req: ErrorRequest, user_id: int = Depends(get_user_id)):
    message = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=200,
        messages=[{
            "role": "user",
            "content": f"""Expert developer on {req.os_type}.
Error: '{req.error}'
Reply ONLY in JSON:
{{"cause": "one line", "fix": "exact command", "confidence": "High/Medium/Low"}}"""
        }]
    )
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
# STRIPE PAYMENTS
# ══════════════════════════════════════

@app.post("/create-checkout")
def create_checkout(user_id: int = Depends(get_user_id)):
    try:
        conn = get_db()
        cursor = conn.execute("SELECT email, plan FROM users WHERE id=?", (user_id,))
        user = cursor.fetchone()
        conn.close()

        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        if user[1] == 'pro':
            raise HTTPException(status_code=400, detail="Already Pro!")

        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            customer_email=user[0],
            line_items=[{"price": STRIPE_PRICE_ID, "quantity": 1}],
            mode="subscription",
            success_url=f"{BASE_URL}/payment-success?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{BASE_URL}/payment-cancel",
            metadata={"user_id": str(user_id)}
        )
        return {"checkout_url": session.url}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/stripe-webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    try:
        if STRIPE_WEBHOOK_SECRET:
            event = stripe.Webhook.construct_event(
                payload, sig_header, STRIPE_WEBHOOK_SECRET
            )
        else:
            event = json.loads(payload)

        if event["type"] == "checkout.session.completed":
            session = event["data"]["object"]
            user_id = int(session["metadata"]["user_id"])
            conn = get_db()
            conn.execute(
                "UPDATE users SET plan='pro', queries_used=0 WHERE id=?",
                (user_id,)
            )
            conn.commit()
            conn.close()
            print(f"User {user_id} upgraded to Pro!")

    except Exception as e:
        print(f"Webhook error: {e}")
        raise HTTPException(status_code=400, detail=str(e))

    return {"status": "ok"}

@app.get("/payment-success", response_class=HTMLResponse)
def payment_success(session_id: str = None):
    if session_id:
        try:
            session = stripe.checkout.Session.retrieve(session_id)
            user_id = int(session.metadata.get("user_id"))
            conn = get_db()
            conn.execute(
                "UPDATE users SET plan='pro', queries_used=0 WHERE id=?",
                (user_id,)
            )
            conn.commit()
            conn.close()
            print(f"Payment success: User {user_id} upgraded to Pro!")
        except Exception as e:
            print(f"Payment success error: {e}")

    return """<!DOCTYPE html>
<html>
<head>
    <title>Payment Successful — Recall</title>
    <style>
        * { margin:0; padding:0; box-sizing:border-box; }
        body { font-family: Arial; background: #0f172a; color: white;
               display: flex; justify-content: center; align-items: center;
               height: 100vh; }
        .box { text-align: center; background: #1e293b; padding: 60px 80px;
               border-radius: 16px; border: 1px solid #22c55e; }
        .icon { font-size: 64px; margin-bottom: 20px; }
        h2 { color: #4ade80; font-size: 32px; margin-bottom: 12px; }
        .badge { display: inline-block; background: #14532d; color: #4ade80;
                 padding: 6px 20px; border-radius: 20px; font-weight: bold;
                 margin: 16px 0; }
        p { color: #94a3b8; margin-top: 12px; }
        .cmd { background: #0f172a; color: #38bdf8; padding: 12px 24px;
               border-radius: 8px; margin-top: 24px; font-family: monospace; }
    </style>
</head>
<body>
    <div class="box">
        <div class="icon">🎉</div>
        <h2>Payment Successful!</h2>
        <div class="badge">✦ PRO MEMBER</div>
        <p>Unlimited queries. Full access. No limits.</p>
        <div class="cmd">recall "your question"</div>
        <p style="margin-top:24px;color:#64748b">You can close this tab now.</p>
    </div>
</body>
</html>"""

@app.get("/payment-cancel", response_class=HTMLResponse)
def payment_cancel():
    return """<!DOCTYPE html>
<html>
<head>
    <title>Payment Cancelled — Recall</title>
    <style>
        body { font-family: Arial; background: #0f172a; color: white;
               display: flex; justify-content: center; align-items: center; height: 100vh; }
        .box { text-align: center; background: #1e293b; padding: 60px 80px; border-radius: 16px; }
        h2 { color: #f87171; font-size: 28px; margin-bottom: 12px; }
        p { color: #94a3b8; margin-top: 12px; }
    </style>
</head>
<body>
    <div class="box">
        <div style="font-size:48px">😔</div>
        <h2>Payment Cancelled</h2>
        <p>No worries — you can upgrade anytime.</p>
        <p style="margin-top:20px;color:#64748b">recall --upgrade</p>
    </div>
</body>
</html>"""

# ══════════════════════════════════════
# ADMIN PANEL
# ══════════════════════════════════════

@app.get("/admin", response_class=HTMLResponse)
def admin_panel():
    return """<!DOCTYPE html>
<html>
<head>
    <title>Recall Admin</title>
    <style>
        * { margin:0; padding:0; box-sizing:border-box; }
        body { font-family: Arial; background: #0f172a; color: #e2e8f0; }
        .header { background: #1e3a8a; padding: 20px 40px; }
        .header h1 { color: #38bdf8; font-size: 24px; }
        .login-box { max-width:400px; margin:100px auto; background:#1e293b; padding:40px; border-radius:12px; }
        .login-box h2 { margin-bottom:20px; color:#38bdf8; }
        input { width:100%; padding:12px; margin:10px 0; background:#0f172a; border:1px solid #334155; border-radius:8px; color:white; font-size:16px; }
        button { width:100%; padding:12px; background:#2563eb; color:white; border:none; border-radius:8px; font-size:16px; cursor:pointer; margin-top:10px; }
        button:hover { background:#1d4ed8; }
        .dashboard { display:none; padding:40px; }
        .stats { display:grid; grid-template-columns:repeat(4,1fr); gap:20px; margin-bottom:40px; }
        .stat-card { background:#1e293b; padding:24px; border-radius:12px; border-left:4px solid #2563eb; }
        .stat-card h3 { color:#94a3b8; font-size:14px; margin-bottom:8px; }
        .stat-card .number { font-size:32px; font-weight:bold; color:#38bdf8; }
        table { width:100%; background:#1e293b; border-radius:12px; overflow:hidden; border-collapse:collapse; }
        th { background:#1e3a8a; padding:16px; text-align:left; color:#94a3b8; font-size:14px; }
        td { padding:16px; border-bottom:1px solid #334155; }
        .badge { padding:4px 12px; border-radius:20px; font-size:12px; font-weight:bold; }
        .free { background:#1e3a8a; color:#38bdf8; }
        .pro { background:#14532d; color:#4ade80; }
        .ban-btn { background:#dc2626; color:white; border:none; padding:6px 16px; border-radius:6px; cursor:pointer; font-size:12px; }
        .unban-btn { background:#16a34a; color:white; border:none; padding:6px 16px; border-radius:6px; cursor:pointer; font-size:12px; }
        .pro-btn { background:#7c3aed; color:white; border:none; padding:6px 16px; border-radius:6px; cursor:pointer; font-size:12px; margin-left:4px; }
    </style>
</head>
<body>
    <div class="header"><h1>⚡ Recall Admin</h1></div>

    <div class="login-box" id="login-box">
        <h2>Admin Login</h2>
        <input type="password" id="password" placeholder="Admin Password" />
        <button onclick="login()">Login</button>
        <p id="error" style="color:red;margin-top:10px"></p>
    </div>

    <div class="dashboard" id="dashboard">
        <div class="stats" id="stats"></div>
        <p style="font-size:20px;font-weight:bold;margin-bottom:20px;margin-top:20px">All Users</p>
        <table>
            <thead><tr><th>#</th><th>Email</th><th>Plan</th><th>Queries</th><th>Joined</th><th>Actions</th></tr></thead>
            <tbody id="users-table"></tbody>
        </table>
    </div>

    <script>
        let adminPass = '';
        async function login() {
            adminPass = document.getElementById('password').value;
            const res = await fetch('/admin/stats', {headers:{'X-Admin-Password':adminPass}});
            if (res.ok) {
                document.getElementById('login-box').style.display = 'none';
                document.getElementById('dashboard').style.display = 'block';
                loadDashboard();
            } else {
                document.getElementById('error').innerText = 'Wrong password!';
            }
        }
        async function loadDashboard() {
            const stats = await fetch('/admin/stats', {headers:{'X-Admin-Password':adminPass}}).then(r=>r.json());
            document.getElementById('stats').innerHTML = `
                <div class="stat-card"><h3>Total Users</h3><div class="number">${stats.total_users}</div></div>
                <div class="stat-card"><h3>Total Queries</h3><div class="number">${stats.total_queries}</div></div>
                <div class="stat-card"><h3>Pro Users</h3><div class="number">${stats.pro_users}</div></div>
                <div class="stat-card"><h3>Today Queries</h3><div class="number">${stats.today_queries}</div></div>
            `;
            const users = await fetch('/admin/users', {headers:{'X-Admin-Password':adminPass}}).then(r=>r.json());
            document.getElementById('users-table').innerHTML = users.map((u,i) => `
                <tr>
                    <td>${i+1}</td>
                    <td>${u.email}</td>
                    <td><span class="badge ${u.plan}">${u.plan.toUpperCase()}</span></td>
                    <td>${u.queries_used}</td>
                    <td>${u.created_at}</td>
                    <td>
                        ${u.is_banned
                            ? `<button class="unban-btn" onclick="banUser(${u.id},false)">Unban</button>`
                            : `<button class="ban-btn" onclick="banUser(${u.id},true)">Ban</button>`}
                        ${u.plan !== 'pro'
                            ? `<button class="pro-btn" onclick="setPro(${u.id})">Set Pro</button>`
                            : `<button class="pro-btn" style="background:#374151" onclick="setFree(${u.id})">Set Free</button>`}
                    </td>
                </tr>
            `).join('');
        }
        async function banUser(userId, ban) {
            await fetch('/admin/ban', {
                method:'POST',
                headers:{'X-Admin-Password':adminPass,'Content-Type':'application/json'},
                body: JSON.stringify({user_id:userId, ban:ban})
            });
            loadDashboard();
        }
        async function setPro(userId) {
            await fetch('/admin/set-plan', {
                method:'POST',
                headers:{'X-Admin-Password':adminPass,'Content-Type':'application/json'},
                body: JSON.stringify({user_id:userId, plan:'pro'})
            });
            loadDashboard();
        }
        async function setFree(userId) {
            await fetch('/admin/set-plan', {
                method:'POST',
                headers:{'X-Admin-Password':adminPass,'Content-Type':'application/json'},
                body: JSON.stringify({user_id:userId, plan:'free'})
            });
            loadDashboard();
        }
        document.getElementById('password').addEventListener('keypress', e => {if(e.key==='Enter') login();});
    </script>
</body>
</html>"""

@app.get("/admin/stats")
def admin_stats(_ = Depends(admin_auth)):
    conn = get_db()
    total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    total_queries = conn.execute("SELECT SUM(queries_used) FROM users").fetchone()[0] or 0
    pro_users = conn.execute("SELECT COUNT(*) FROM users WHERE plan='pro'").fetchone()[0]
    today_queries = conn.execute(
        "SELECT COUNT(*) FROM memory WHERE date(timestamp)=date('now')"
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
    rows = conn.execute(
        "SELECT id,email,plan,queries_used,is_banned,created_at FROM users ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    return [{"id":r[0],"email":r[1],"plan":r[2],"queries_used":r[3],"is_banned":r[4],"created_at":r[5]} for r in rows]

@app.post("/admin/ban")
def ban_user(req: BanRequest, _ = Depends(admin_auth)):
    conn = get_db()
    conn.execute("UPDATE users SET is_banned=? WHERE id=?", (1 if req.ban else 0, req.user_id))
    conn.commit()
    conn.close()
    return {"message": "Done"}

@app.post("/admin/set-plan")
def set_plan(req: PlanRequest, _ = Depends(admin_auth)):
    conn = get_db()
    conn.execute("UPDATE users SET plan=? WHERE id=?", (req.plan, req.user_id))
    conn.commit()
    conn.close()
    return {"message": "Plan updated"}

# ══════════════════════════════════════
# HEALTH
# ══════════════════════════════════════

@app.get("/")
def root():
    return {"status": "Recall API running", "version": "1.0.0"}