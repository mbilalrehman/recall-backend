import json
import anthropic
from fastapi import APIRouter, Depends, HTTPException
from database import get_db
from auth import get_user_id
from models import QueryRequest, ErrorRequest
from config import ANTHROPIC_KEY, FREE_QUERY_LIMIT

router = APIRouter()
client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

@router.post("/query")
def query(req: QueryRequest, user_id: int = Depends(get_user_id)):
    conn = get_db()
    user = conn.execute(
        "SELECT plan, queries_used, is_banned FROM users WHERE id=?", (user_id,)
    ).fetchone()

    if not user:
        conn.close()
        raise HTTPException(status_code=404, detail="User not found")
    if user["is_banned"]:
        conn.close()
        raise HTTPException(status_code=403, detail="Account banned")
    if user["plan"] == "free" and user["queries_used"] >= FREE_QUERY_LIMIT:
        conn.close()
        raise HTTPException(status_code=429, detail="Free limit reached (50/month). Upgrade: recall --upgrade")

    history = conn.execute(
        "SELECT query, command FROM memory WHERE user_id=? ORDER BY timestamp DESC LIMIT 5",
        (user_id,)
    ).fetchall()

    history_text = "\n".join(
        [f"- Asked: '{h['query']}' → Command: '{h['command']}'" for h in history]
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
        "INSERT INTO memory (user_id, query, command) VALUES (?, ?, ?)",
        (user_id, req.query, command)
    )
    conn.execute(
        "UPDATE users SET queries_used = queries_used + 1 WHERE id=?", (user_id,)
    )
    conn.commit()
    conn.close()

    return {
        "command": command,
        "plan": user["plan"],
        "queries_used": user["queries_used"] + 1,
        "limit": "unlimited" if user["plan"] == "pro" else f"{user['queries_used']+1}/{FREE_QUERY_LIMIT}"
    }

@router.post("/fix-error")
def fix_error(req: ErrorRequest, user_id: int = Depends(get_user_id)):
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
        return json.loads(message.content[0].text)
    except Exception:
        return {
            "cause": "Unknown",
            "fix": message.content[0].text.strip(),
            "confidence": "Low"
        }