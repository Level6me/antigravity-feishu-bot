import os
from dotenv import load_dotenv

load_dotenv()

APP_ID = os.getenv("FEISHU_APP_ID", "")
APP_SECRET = os.getenv("FEISHU_APP_SECRET", "")

SESSION_FILE = "chat_sessions.json"
PROFILE_FILE = "user_profiles.json"
