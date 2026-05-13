import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

GARMIN_EMAIL = os.getenv("GARMIN_EMAIL")
GARMIN_PASSWORD = os.getenv("GARMIN_PASSWORD")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
GARMIN_SHEET_SECRET_NAME = os.getenv("GARMIN_SHEET_SECRET_NAME")
GARMINTOKENS = os.getenv("GARMINTOKENS", "~/.garminconnect")
