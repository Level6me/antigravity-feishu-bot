import os
import json
from config import SESSION_FILE, PROFILE_FILE

def load_sessions():
    if os.path.exists(SESSION_FILE):
        try:
            with open(SESSION_FILE, "r") as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_sessions(sessions):
    with open(SESSION_FILE, "w") as f:
        json.dump(sessions, f, indent=2)

def load_profiles():
    if os.path.exists(PROFILE_FILE):
        try:
            with open(PROFILE_FILE, "r") as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_profiles(profiles):
    with open(PROFILE_FILE, "w") as f:
        json.dump(profiles, f, ensure_ascii=False, indent=2)
