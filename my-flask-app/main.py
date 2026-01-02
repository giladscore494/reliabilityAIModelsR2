import os
import logging

from flask import Flask, redirect, url_for, request
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from authlib.integrations.flask_client import OAuth
from dotenv import load_dotenv

# Load environment variables from .env file for local development
load_dotenv()

app = Flask(__name__)

# Configure database connection string
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable is not set")

# Determine if running on Render
is_render = bool(os.getenv("RENDER"))

# Render-only: SECRET_KEY must be set on Render
if is_render and not os.getenv("SECRET_KEY"):
    raise ValueError("SECRET_KEY environment variable must be set when running on Render")

# Set secret key for session management
app.secret_key = os.getenv("SECRET_KEY", "dev")

# Session / cookie config
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=bool(is_render),
)

# Minimal request logger (no cookies/tokens/bodies)
@app.before_request
def _log_request_meta():
    try:
        app.logger.info(
            "req method=%s path=%s host=%s scheme=%s xfp=%s xff=%s authenticated=%s",
            request.method,
            request.path,
            request.host,
            request.scheme,
            request.headers.get("X-Forwarded-Proto"),
            request.headers.get("X-Forwarded-For"),
            getattr(current_user, "is_authenticated", False),
        )
    except Exception:
        # Never fail the request due to logging
        pass

# Configure OAuth
oauth = OAuth(app)

google = oauth.register(
    name="google",
    client_id=os.getenv("GOOGLE_CLIENT_ID"),
    client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
    access_token_url="https://oauth2.googleapis.com/token",
    authorize_url="https://accounts.google.com/o/oauth2/auth",
    api_base_url="https://www.googleapis.com/oauth2/v1/",
    client_kwargs={"scope": "email profile"},
)

# Configure Login Manager
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"


class User:
    def __init__(self, user_id, email):
        self.id = user_id
        self.email = email

    def is_active(self):
        return True

    def is_authenticated(self):
        return True

    def is_anonymous(self):
        return False

    def get_id(self):
        return self.id


@login_manager.user_loader
def load_user(user_id):
    # In a real application, you would fetch user from the database
    return User(user_id, "user@example.com")


@app.route("/")
def index():
    return "Hello, Flask with OAuth!"


@app.route("/login")
def login():
    redirect_uri = url_for("authorize", _external=True)
    return oauth.google.authorize_redirect(redirect_uri)


@app.route("/authorize")
def authorize():
    token = oauth.google.authorize_access_token()
    resp = oauth.google.get("userinfo")
    user_info = resp.json()

    user = User(user_info["id"], user_info["email"])
    login_user(user)

    return redirect(url_for("index"))


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("index"))


if __name__ == "__main__":
    # Configure logging for local debugging
    logging.basicConfig(level=logging.INFO)

    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
