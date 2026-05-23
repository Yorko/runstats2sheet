import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

def load_config_yaml(file_path="config.yaml") -> dict:
    config = {}
    if os.path.exists(file_path):
        try:
            with open(file_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if ":" in line:
                        key, val = line.split(":", 1)
                        config[key.strip()] = val.strip().strip('"').strip("'")
        except Exception as e:
            print(f"Warning: Failed to read {file_path}: {e}")
    return config

yaml_config = load_config_yaml()
SYNC_SOURCE = yaml_config.get("sync_source", "garmin").lower()

# Garmin credentials
GARMIN_EMAIL = os.getenv("GARMIN_EMAIL")
GARMIN_PASSWORD = os.getenv("GARMIN_PASSWORD")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
GARMIN_SHEET_SECRET_NAME = os.getenv("GARMIN_SHEET_SECRET_NAME")
GARMINTOKENS = os.getenv("GARMINTOKENS", "~/.garminconnect")

# Strava credentials
STRAVA_CLIENT_ID = os.getenv("STRAVA_CLIENT_ID")
STRAVA_CLIENT_SECRET = os.getenv("STRAVA_CLIENT_SECRET")

