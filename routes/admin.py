from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from database import get_db
from auth import admin_auth
from models import BanRequest, PlanRequest

router = APIRouter()

@router.get("/admin", response_class=HTMLResponse)
def admin_panel():
    return """<!DOCTYPE html>
<html>
<head>
    <title>Recall Admin</title>
    <style>
        * { margin:0; padding:0; box-sizing:border-box; }
        body { font-family:Arial; background:#0f172a; color:#e2e8f0; }
        .header { background:#1e3a8a; padding:20px 40px; }
        .header h1 { color:#38bdf8; font-size:24px; }
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
        .free-btn { background:#374151; color:white; border:none; padding:6px 16px; border-radius:6px; cursor:pointer; font-size:12px; margin-left:4px; }
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
        <p style="font-size:20px;font-weight:bold;margin:20px 0">All Users</p>
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
                            ? `<button class="pro-btn" onclick="setPlan(${u.id},'pro')">Set Pro</button>`
                            : `<button class="free-btn" onclick="setPlan(${u.id},'free')">Set Free</button>`}
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
        async function setPlan(userId, plan) {
            await fetch('/admin/set-plan', {
                method:'POST',
                headers:{'X-Admin-Password':adminPass,'Content-Type':'application/json'},
                body: JSON.stringify({user_id:userId, plan:plan})
            });
            loadDashboard();
        }
        document.getElementById('password').addEventListener('keypress', e=>{if(e.key==='Enter')login();});
    </script>
</body>
</html>"""

@router.get("/admin/stats")
def admin_stats(_ = Depends(admin_auth)):
    conn = get_db()
    result = {
        "total_users": conn.execute("SELECT COUNT(*) FROM users").fetchone()[0],
        "total_queries": conn.execute("SELECT SUM(queries_used) FROM users").fetchone()[0] or 0,
        "pro_users": conn.execute("SELECT COUNT(*) FROM users WHERE plan='pro'").fetchone()[0],
        "today_queries": conn.execute("SELECT COUNT(*) FROM memory WHERE date(timestamp)=date('now')").fetchone()[0]
    }
    conn.close()
    return result

@router.get("/admin/users")
def admin_users(_ = Depends(admin_auth)):
    conn = get_db()
    rows = conn.execute(
        "SELECT id,email,plan,queries_used,is_banned,created_at FROM users ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

@router.post("/admin/ban")
def ban_user(req: BanRequest, _ = Depends(admin_auth)):
    conn = get_db()
    conn.execute("UPDATE users SET is_banned=? WHERE id=?", (1 if req.ban else 0, req.user_id))
    conn.commit()
    conn.close()
    return {"message": "Done"}

@router.post("/admin/set-plan")
def set_plan(req: PlanRequest, _ = Depends(admin_auth)):
    conn = get_db()
    conn.execute("UPDATE users SET plan=? WHERE id=?", (req.plan, req.user_id))
    conn.commit()
    conn.close()
    return {"message": "Plan updated"}