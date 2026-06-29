import json
import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from database import get_db
from auth import get_user_id
from config import STRIPE_PRICE_ID, STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET, BASE_URL

router = APIRouter()
stripe.api_key = STRIPE_SECRET_KEY

@router.post("/create-checkout")
def create_checkout(user_id: int = Depends(get_user_id)):
    conn = get_db()
    user = conn.execute("SELECT email, plan FROM users WHERE id=?", (user_id,)).fetchone()
    conn.close()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user["plan"] == "pro":
        raise HTTPException(status_code=400, detail="Already Pro!")

    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            customer_email=user["email"],
            line_items=[{"price": STRIPE_PRICE_ID, "quantity": 1}],
            mode="subscription",
            success_url=f"{BASE_URL}/payment-success?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{BASE_URL}/payment-cancel",
            metadata={"user_id": str(user_id)}
        )
        return {"checkout_url": session.url}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/stripe-webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    try:
        if STRIPE_WEBHOOK_SECRET:
            event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
        else:
            event = json.loads(payload)

        if event["type"] == "checkout.session.completed":
            session = event["data"]["object"]
            user_id = int(session["metadata"]["user_id"])
            conn = get_db()
            conn.execute("UPDATE users SET plan='pro', queries_used=0 WHERE id=?", (user_id,))
            conn.commit()
            conn.close()
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "ok"}

@router.get("/payment-success", response_class=HTMLResponse)
def payment_success(session_id: str = None):
    if session_id and session_id != "get":
        try:
            session = stripe.checkout.Session.retrieve(session_id)
            user_id = int(session.metadata.get("user_id"))
            conn = get_db()
            conn.execute("UPDATE users SET plan='pro', queries_used=0 WHERE id=?", (user_id,))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Payment success error: {e}")

    return """<!DOCTYPE html>
<html>
<head>
    <title>Payment Successful — Recall</title>
    <style>
        * { margin:0; padding:0; box-sizing:border-box; }
        body { font-family:Arial; background:#0f172a; color:white;
               display:flex; justify-content:center; align-items:center; height:100vh; }
        .box { text-align:center; background:#1e293b; padding:60px 80px;
               border-radius:16px; border:1px solid #22c55e; }
        h2 { color:#4ade80; font-size:32px; margin-bottom:12px; }
        .badge { display:inline-block; background:#14532d; color:#4ade80;
                 padding:6px 20px; border-radius:20px; font-weight:bold; margin:16px 0; }
        p { color:#94a3b8; margin-top:12px; }
        .cmd { background:#0f172a; color:#38bdf8; padding:12px 24px;
               border-radius:8px; margin-top:24px; font-family:monospace; }
    </style>
</head>
<body>
    <div class="box">
        <div style="font-size:64px;margin-bottom:20px">🎉</div>
        <h2>Payment Successful!</h2>
        <div class="badge">✦ PRO MEMBER</div>
        <p>Unlimited queries. Full access. No limits.</p>
        <div class="cmd">recall "your question"</div>
        <p style="margin-top:24px;color:#64748b">You can close this tab now.</p>
    </div>
</body>
</html>"""

@router.get("/payment-cancel", response_class=HTMLResponse)
def payment_cancel():
    return """<!DOCTYPE html>
<html>
<head>
    <title>Payment Cancelled — Recall</title>
    <style>
        body { font-family:Arial; background:#0f172a; color:white;
               display:flex; justify-content:center; align-items:center; height:100vh; }
        .box { text-align:center; background:#1e293b; padding:60px 80px; border-radius:16px; }
        h2 { color:#f87171; font-size:28px; margin-bottom:12px; }
        p { color:#94a3b8; margin-top:12px; }
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