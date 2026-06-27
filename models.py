from pydantic import BaseModel

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