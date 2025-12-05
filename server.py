import os
import re
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from pymongo import MongoClient
import bcrypt
import uvicorn
from datetime import datetime, timedelta
import jwt
from graph_app import graph, get_chat_history, checkpointer
from langchain_core.messages import HumanMessage
from fastapi import Request, HTTPException
from groq import APIStatusError


# 1. SETUP & CONFIGURATION
load_dotenv()
app = FastAPI(title="DSLA")
templates = Jinja2Templates(directory="templates")


# Password hashing configuration
def hash_password(password: str) -> str:
    """
    Hash a plaintext password with bcrypt + random salt.
    """
    hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())
    return hashed.decode("utf-8")  # store as string in MongoDB

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verify plaintext password against stored bcrypt hash.
    """
    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8"),
            hashed_password.encode("utf-8"),
        )
    except ValueError:
        # if stored hash is malformed
        return False


# Basic validation helpers
EMAIL_REGEX = re.compile(r"^[^@]+@[^@]+\.[^@]+$")

def is_valid_email(email: str) -> bool:
    return bool(EMAIL_REGEX.match(email))

def is_valid_password(password: str) -> tuple[bool, str]:
    """
    Basic rules:
      - At least 8 characters
      - At least 1 letter
      - At least 1 digit or special character
    """
    if len(password) < 8:
        return False, "Password must be at least 8 characters long."

    if not re.search(r"[A-Za-z]", password):
        return False, "Password must contain at least one letter."

    if not re.search(r"[\d\W]", password):
        return False, "Password must contain at least one number or special character."

    return True, ""


def create_access_token(data: dict, expires_delta: int | None = None) -> str:
    """
    Create a JWT access token.
    `data` will typically contain {"sub": user_email}.
    """
    to_encode = data.copy()
    if expires_delta is None:
        expires_delta = ACCESS_TOKEN_EXPIRE_MINUTES
    expire = datetime.utcnow() + timedelta(minutes=expires_delta)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt



# 2. DATABASE CONNECTION
MONGO_URI = os.getenv("MONGO_URI")

users_collection = None

if MONGO_URI:
    client = MongoClient(MONGO_URI)
    db = client["DSLA_db"]
    users_collection = db["users"]
else:
    print("WARNING: MONGO_URI not found in .env")

# --- SIMPLE AUTH HELPERS (COOKIE-BASED) ---

AUTH_COOKIE_NAME = "access_token"  # instead of "user_email"

SECRET_KEY = os.getenv("SECRET_KEY", "change-this-in-prod")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60  # 1 hour, adjust as you like


def get_current_user_email(request: Request) -> str | None:
    token = request.cookies.get(AUTH_COOKIE_NAME)
    if not token or users_collection is None:
        return None

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str | None = payload.get("sub")
        if email is None:
            return None
    except jwt.ExpiredSignatureError:
        # token expired
        return None
    except jwt.PyJWTError:
        # any other token error
        return None

    user = users_collection.find_one({"email": email})
    if not user:
        return None

    return email



# --- ROUTES ---

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    # If already logged in, go straight to home
    if get_current_user_email(request):
        return RedirectResponse(url="/home", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request})

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    # If already logged in, go straight to home
    if get_current_user_email(request):
        return RedirectResponse(url="/home", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request})

@app.get("/signup", response_class=HTMLResponse)
async def signup_page(request: Request):
    # If already logged in, go straight to home
    if get_current_user_email(request):
        return RedirectResponse(url="/home", status_code=303)
    return templates.TemplateResponse("signup.html", {"request": request})

@app.get("/home", response_class=HTMLResponse)
async def home_page(request: Request):
    # Protect this route: only accessible when logged in
    current_email = get_current_user_email(request)
    if not current_email:
        return RedirectResponse(url="/login", status_code=303)

    return templates.TemplateResponse(
        "home.html",
        {
            "request": request,
            "user_email": current_email,
        },
    )

@app.get("/logout")
async def logout(request: Request):
    """
    Clear auth cookie and send back to login.
    """
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(AUTH_COOKIE_NAME)
    return response

# --- AUTHENTICATION LOGIC ---

@app.post("/signup", response_class=HTMLResponse)
async def sign_up_user(
    request: Request,
    first_name: str = Form(...),
    last_name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
    # tos: str = Form(...),
):
    if users_collection is None:
        return templates.TemplateResponse(
            "signup.html",
            {
                "request": request,
                "error": "Server configuration error: database not available.",
            },
        )

    # Server-side validation
    if not is_valid_email(email):
        return templates.TemplateResponse(
            "signup.html",
            {"request": request, "error": "Please enter a valid email address."},
        )

    valid_pw, pw_error = is_valid_password(password)
    if not valid_pw:
        return templates.TemplateResponse(
            "signup.html",
            {"request": request, "error": pw_error},
        )

    if password != password_confirm:
        return templates.TemplateResponse(
            "signup.html",
            {"request": request, "error": "Passwords do not match."},
        )

    # Check if email already exists
    if users_collection.find_one({"email": email}):
        return templates.TemplateResponse(
            "signup.html",
            {"request": request, "error": "Email is already registered."},
        )

    # Hash the password before storing
    password_hash = hash_password(password)

    user_data = {
        "first_name": first_name.strip(),
        "last_name": last_name.strip(),
        "email": email.strip().lower(),
        "password_hash": password_hash,
    }
    users_collection.insert_one(user_data)

    # After signup, send them to login with a message
    return templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "message": "Account created successfully! Please log in.",
        },
    )

@app.post("/login", response_class=HTMLResponse)
async def login_user(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
):
    if users_collection is None:
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "error": "Server configuration error: database not available.",
            },
        )

    email_normalized = email.strip().lower()
    user = users_collection.find_one({"email": email_normalized})

    if not user:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Invalid email or password."},
        )

    # Get hashed password from DB
    hashed_pw = user.get("password_hash")

    if not hashed_pw or not verify_password(password, hashed_pw):
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Invalid email or password."},
        )

    # Successful login: issue JWT and set it in an HTTP-only cookie
    access_token = create_access_token({"sub": email})

    response = RedirectResponse(url="/home", status_code=303)
    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=access_token,
        httponly=True,
        samesite="lax",
        # secure=True,  # enable this when using HTTPS
    )
    return response


@app.post("/chat")
async def chat(request: Request):
    # --- DEBUG: see exactly what the frontend sends ---
    data = await request.json()

    messages = data.get("message")
    thread_id = get_current_user_email(request)


    state = {
        "messages": [HumanMessage(content=messages)]
    }

    config = {'configurable': {'thread_id': thread_id}}
    try:
        response = graph.invoke(state, config=config)
    except APIStatusError as e:
        # Groq specific "request too large" error
        if e.status_code == 413 or getattr(e, "code", None) == "rate_limit_exceeded":
            # Return a friendly error message to the frontend
            return {
                "output": (
                    "I'm overloaded right now – the conversation context "
                    "got too large for my token limit. "
                    "Try clearing the chat or shortening your message."
                )
            }
        raise
    print(response['messages'][-1].content)
    return {"output": response['messages'][-1].content}



# NEW: expose chat history for the logged-in user
@app.get("/chat_history")
async def chat_history(request: Request):
    """
    Return the full saved conversation for the current user
    as JSON, so the frontend can pre-populate the chat box.
    """
    current_email = get_current_user_email(request)
    if not current_email:
        raise HTTPException(status_code=401, detail="Not authenticated")

    history = get_chat_history(graph, current_email)
    # JSON-serialisable: [{role: "human"/"ai", content: "..."}]
    history_payload = [
        {"role": role, "content": content} for role, content in history
    ]
    return {"history": history_payload}



@app.post("/clear_history")
async def clear_history(request: Request):
    thread_id = get_current_user_email(request)
    checkpointer.delete_thread(thread_id)

    return {"status": "ok"}


@app.get("/{full_path:path}", include_in_schema=False)
async def catch_all(full_path: str, request: Request):
    # optional: print or log the invalid path
    # print("Invalid path:", full_path)
    return RedirectResponse(url="/home")



if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
    
