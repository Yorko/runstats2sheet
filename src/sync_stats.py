import contextlib
import logging
import os
import sys
import json
from datetime import date, datetime, timedelta
from google.cloud import secretmanager
from getpass import getpass
from pathlib import Path

import gspread
import pandas as pd
from dotenv import load_dotenv
from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

from src import config
from src import garmin_api
from src.utils import decimal_pace_to_mmss

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logging.getLogger("garminconnect").setLevel(logging.CRITICAL)

if not config.SPREADSHEET_ID:
    logging.error("Missing SPREADSHEET_ID environment variable. Please check your .env file.")
    sys.exit(1)


def sync_garmin_stats():
    state_file = Path(".last_sync_timestamp")
    now = datetime.now()
    if state_file.exists():
        try:
            last_sync_str = state_file.read_text().strip()
            last_sync = datetime.fromisoformat(last_sync_str)
            if now - last_sync < timedelta(hours=1):
                logging.info(f"Last sync was at {last_sync_str} (less than 1 hour ago). Skipping.")
                return
        except ValueError:
            logging.warning("Could not parse last sync timestamp. Proceeding with sync.")
            pass

    api = garmin_api.init_api()
    if not api:
        logging.error("Failed to initialize Garmin API.")
        return

    logging.info("Fetching activities...")
    # Get last 100 activities
    success, activities, err = garmin_api.safe_api_call(api.get_activities, 0, 100)
    if not success:
        logging.error(f"Failed to fetch activities: {err}")
        sys.exit(1)

    logging.info(f"Found {len(activities)} activities. Processing...")

    processed_activities = []
    for i, activity in enumerate(activities):
            
        # Extract relevant data
        activity_id = activity['activityId']
        name = activity['activityName']
        distance = activity['distance'] / 1000  # Convert to km
        hr_avg = activity.get('averageHR', 0)
        hr_max = activity.get('maxHR', 0)
        duration = activity.get('duration', 0) / 60  # Convert to minutes
        
        # Pace calculation (assuming speed is in m/s)
        speed_ms = activity.get('averageSpeed', 0)
        pace = 0
        if speed_ms > 0:
            pace = 16.666 / speed_ms  # min/km
            
        te_aerobic = activity.get('trainingEffect')
        if te_aerobic is None:
            te_aerobic = activity.get('aerobicTrainingEffect', 0)
        te_anaerobic = activity.get('anaerobicTrainingEffect', 0)
        te_aerobic = round(float(te_aerobic), 1)
        te_anaerobic = round(float(te_anaerobic), 1)
        start_time = activity['startTimeLocal']

        # Extract additional fields
        activity_type_obj = activity.get('activityType', {})
        activity_type = activity_type_obj.get('typeKey', '') if isinstance(activity_type_obj, dict) else activity_type_obj
        vo2max = activity.get('vO2MaxValue') or activity.get('vo2MaxValue') or 0
        vo2max = round(float(vo2max), 1)
        calories = activity.get('calories', 0)
        te_label = activity.get('trainingEffectLabel', '')
        
        # Cadence
        avg_cadence = activity.get('averageRunningCadenceInStepsPerMinute') or activity.get('averageCadence', 0)
        max_cadence = activity.get('maxRunningCadenceInStepsPerMinute') or activity.get('maxCadence', 0)

        # Fetch HR zones
        success_hr, hr_zones, err_hr = garmin_api.safe_api_call(api.get_activity_hr_in_timezones, activity_id)
        if not success_hr:
            hr_zones = {}

        logging.info(f"Processing activity: {name} ({start_time})")

        processed_activities.append({
            "startTimeLocal": start_time,
            "activityName": name,
            "distance": round(distance, 2),
            "duration": round(duration, 2),
            "averageHR": hr_avg,
            "maxHR": hr_max,
            "pace": round(pace, 2),
            "TE_aerobic": te_aerobic,
            "TE_anaerobic": te_anaerobic,
            "HR_zones": json.dumps(hr_zones) if hr_zones else "{}",
            "activityType": activity_type,
            "vo2max": vo2max,
            "calories": calories,
            "trainingEffectLabel": te_label,
            "avgCadence": round(avg_cadence, 1),
            "maxCadence": round(max_cadence, 1)
        })

    # Save last workout to local file for frontend
    if processed_activities:
        frontend_data_dir = Path("src/frontend/data")
        frontend_data_dir.mkdir(parents=True, exist_ok=True)
        last_workout_file = frontend_data_dir / "last_workout.js"
        try:
            with open(last_workout_file, "w") as f:
                f.write("window.lastWorkout = ")
                json.dump(processed_activities[0], f, indent=4)
                f.write(";")
            logging.info(f"Saved last workout to {last_workout_file}")
        except Exception as e:
            logging.error(f"Failed to save last workout to file: {e}")

    if not processed_activities:
        logging.info("No activities to send.")
        return

    logging.info(f"Sending {len(processed_activities)} activities to Google Sheets...")
    try:
        # Initialize gspread
        secret_name = config.GARMIN_SHEET_SECRET_NAME
        if secret_name:
            logging.info(f"Fetching service account key from Secret Manager: {secret_name}")
            try:
                client = secretmanager.SecretManagerServiceClient()
                response = client.access_secret_version(request={"name": secret_name})
                secret_json = response.payload.data.decode("UTF-8")
                gc = gspread.service_account_from_dict(json.loads(secret_json))
            except Exception as e:
                logging.error(f"Error fetching secret or initializing gspread: {e}")
                sys.exit(1)
        else:
            logging.info("Using local service_account.json")
            gc = gspread.service_account(filename="service_account.json")
        sh = gc.open_by_key(config.SPREADSHEET_ID)
        wks = sh.sheet1

        # Read existing data to check for duplicates
        try:
            existing_values = wks.get_all_values()
        except Exception as e:
            logging.warning(f"Warning: Failed to read existing data from sheet: {e}. Assuming empty.")
            existing_values = []

        # Extract start times to check for duplicates (handles old schema with 10 cols and new with >=11)
        existing_start_times = {
            row[1] if len(row) >= 11 else row[0]
            for row in existing_values if row
        }

        sync_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Prepare rows
        rows_to_append = []
        for activity in processed_activities:
            if activity["startTimeLocal"] in existing_start_times:
                continue

            rows_to_append.append([
                sync_timestamp,
                activity["startTimeLocal"],
                activity["activityName"],
                activity.get("activityType", ""),
                activity["distance"],
                activity["duration"],
                activity["averageHR"],
                activity["maxHR"],
                decimal_pace_to_mmss(activity["pace"]),
                activity.get("avgCadence", 0),
                activity.get("maxCadence", 0),
                activity["TE_aerobic"],
                activity["TE_anaerobic"],
                activity["HR_zones"],
                activity.get("vo2max", 0),
                activity.get("calories", 0),
                activity.get("trainingEffectLabel", "")
            ])
        
        if rows_to_append:
            logging.info(f"Appending {len(rows_to_append)} new rows to Google Sheet...")
            wks.append_rows(rows_to_append)
            logging.info("Successfully updated Google Sheet.")
        else:
            logging.info("No new activities to append.")

        # Update state file on success
        state_file.write_text(now.isoformat())

    except Exception as e:
        logging.error(f"Failed to send data to Google Sheets: {e}")

if __name__ == "__main__":
    sync_garmin_stats()
