from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager
from authlib.integrations.flask_client import OAuth

# Global extension instances
db = SQLAlchemy()
migrate = Migrate()
login_manager = LoginManager()
oauth = OAuth()

# AI client placeholders (initialized in factory)
ai_client = None
advisor_client = None
GEMINI3_MODEL_ID = "gemini-3-flash-preview"
