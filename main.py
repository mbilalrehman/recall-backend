from fastapi import FastAPI
from routes import auth, query, payments, admin

app = FastAPI(title="Recall API", version="1.0.0")

app.include_router(auth.router)
app.include_router(query.router)
app.include_router(payments.router)
app.include_router(admin.router)

@app.get("/")
def root():
    return {"status": "Recall API running", "version": "1.0.0"}