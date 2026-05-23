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
import requests
from dotenv import load_dotenv

from src import config
from src import garmin_api
from src import strava_api
from src.utils import decimal_pace_to_mmss

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logging.getLogger("garminconnect").setLevel(logging.CRITICAL)

if not config.SPREADSHEET_ID:
    logging.error("Missing SPREADSHEET_ID environment variable. Please check your .env file.")
    sys.exit(1)


def fetch_garmin_activities() -> list[dict]:
    """Fetch and process activities from Garmin Connect."""
    api = garmin_api.init_api()
    if not api:
        logging.error("Failed to initialize Garmin API.")
        return []

    logging.info("Fetching activities from Garmin Connect...")
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
    return processed_activities


def fetch_strava_hr_zones(api_client, activity_id) -> dict:
    """Fetch heart rate zones for a Strava activity and format as a dictionary."""
    url = f"https://www.strava.com/api/v3/activities/{activity_id}/zones"
    headers = {"Authorization": f"Bearer {api_client.settings.access_token}"}
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json()
            for zone_data in data:
                if zone_data.get("type") == "heartrate":
                    buckets = zone_data.get("distribution_bucket", [])
                    hr_zones = {}
                    for idx, bucket in enumerate(buckets):
                        zone_num = str(idx + 1)
                        time_spent = bucket.get("time", 0)
                        hr_zones[zone_num] = time_spent
                    return hr_zones
    except Exception as e:
        logging.warning(f"Warning: Failed to fetch heart rate zones for Strava activity {activity_id}: {e}")
    return {}


def fetch_strava_activities(existing_start_times: set) -> list[dict]:
    """Fetch and process activities from Strava API, retrieving detailed metrics only for new activities."""
    api = strava_api.init_api()
    if not api:
        logging.error("Failed to initialize Strava API.")
        return []

    logging.info("Fetching activities from Strava...")
    try:
        # Get last 100 activities from Strava
        summary_activities = api.get_activities(per_page=100)
    except Exception as e:
        logging.error(f"Failed to fetch activities from Strava: {e}")
        sys.exit(1)

    logging.info(f"Found {len(summary_activities)} activities. Filtering for new activities...")

    processed_activities = []
    for summary_activity in summary_activities:
        # Format start time to local string matching Garmin format to check for duplicates
        start_time = summary_activity.start_date_local.strftime("%Y-%m-%d %H:%M:%S")
        
        # Skip if already in Google Sheets
        if start_time in existing_start_times:
            continue

        logging.info(f"New Strava activity detected: {summary_activity.name} ({start_time}). Fetching details...")
        activity_id = summary_activity.id
        
        # Fetch detailed activity to get Calories, Max Cadence, and other detailed fields
        try:
            detailed_activity = api.get_activity_detailed(str(activity_id))
        except Exception as e:
            logging.warning(f"Warning: Failed to fetch detailed activity for {activity_id}: {e}. Falling back to summary activity.")
            detailed_activity = summary_activity

        # Fetch heart rate zones
        hr_zones = fetch_strava_hr_zones(api, activity_id)

        # Extract fields
        name = detailed_activity.name
        distance = detailed_activity.distance / 1000.0  # Convert to km
        duration = detailed_activity.moving_time / 60.0  # Convert to minutes
        
        extra = detailed_activity.model_extra or {}
        hr_avg = extra.get("average_heartrate", 0) or 0
        hr_max = extra.get("max_heartrate", 0) or 0
        
        # Pace calculation (assuming speed is in m/s)
        speed_ms = detailed_activity.average_speed
        pace = 0
        if speed_ms > 0:
            pace = 16.666 / speed_ms  # min/km
            
        # Cadence
        avg_cadence = extra.get("average_cadence", 0) or 0
        max_cadence = extra.get("max_cadence", 0) or 0
        
        # Adjust running cadence (Strava rpm -> spm strides/minute)
        sport_type = extra.get("type", "")
        if sport_type == "Run":
            if 0 < avg_cadence < 120:
                avg_cadence = avg_cadence * 2
            if 0 < max_cadence < 120:
                max_cadence = max_cadence * 2

        calories = extra.get("calories", 0) or 0
        
        # Map/extract training effect & vo2max (with safe fallbacks)
        te_aerobic = extra.get("aerobic_training_effect") or extra.get("aerobic_te") or 0.0
        te_anaerobic = extra.get("anaerobic_training_effect") or extra.get("anaerobic_te") or 0.0
        vo2max = extra.get("vo2max") or extra.get("vo2_max") or 0.0
        te_label = extra.get("training_effect_label") or extra.get("te_label") or ""

        processed_activities.append({
            "startTimeLocal": start_time,
            "activityName": name,
            "distance": round(distance, 2),
            "duration": round(duration, 2),
            "averageHR": round(hr_avg) if hr_avg else 0,
            "maxHR": round(hr_max) if hr_max else 0,
            "pace": round(pace, 2),
            "TE_aerobic": round(float(te_aerobic), 1) if te_aerobic else 0.0,
            "TE_anaerobic": round(float(te_anaerobic), 1) if te_anaerobic else 0.0,
            "HR_zones": json.dumps(hr_zones) if hr_zones else "{}",
            "activityType": sport_type,
            "vo2max": round(float(vo2max), 1) if vo2max else 0.0,
            "calories": round(calories) if calories else 0,
            "trainingEffectLabel": te_label,
            "avgCadence": round(avg_cadence, 1),
            "maxCadence": round(max_cadence, 1)
        })
    return processed_activities


def sync_stats():
    """Sync fitness stats from either Garmin or Strava to Google Sheets."""
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

    source = config.SYNC_SOURCE
    logging.info(f"Sync source configured: {source.upper()}")

    # 1. Initialize gspread and read existing sheet FIRST
    try:
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
    except Exception as e:
        logging.error(f"Failed to initialize Google Sheet access: {e}")
        sys.exit(1)

    # Expected headers for a new / empty sheet
    headers = [
        "Timestamp", "Start time", "Activity Name", "Activity Type",
        "Distance", "Duration", "Av HR", "Max HR", "Pace",
        "Avg Cadence", "Max Cadence", "Aerobic TE", "Anaerobic TE",
        "HR Zones", "VO2MAX", "Calories", "Training Effect"
    ]

    # If sheet is completely empty, insert the header row first
    if not existing_values:
        logging.info("Sheet is empty. Writing headers...")
        try:
            wks.insert_row(headers, index=1)
        except Exception as e:
            logging.error(f"Failed to write headers: {e}")
            sys.exit(1)
        existing_start_times = set()
    else:
        # Extract start times to check for duplicates, skipping the header row if present
        existing_start_times = set()
        for row in existing_values:
            if not row:
                continue
            # Skip header row
            if row[0] == "Timestamp" or (len(row) >= 2 and row[1] == "Start time"):
                continue
            existing_start_times.add(row[1] if len(row) >= 11 else row[0])

    # 2. Fetch activities based on the source, using filtered duplicates to avoid excessive API calls
    if source == "strava":
        processed_activities = fetch_strava_activities(existing_start_times)
    elif source == "garmin":
        processed_activities = fetch_garmin_activities()
    else:
        logging.error(f"Unknown sync source: {source}. Must be 'garmin' or 'strava'.")
        sys.exit(1)

    if not processed_activities:
        logging.info("No new activities to append. Sync complete.")
        return

    logging.info(f"Sending {len(processed_activities)} activities to Google Sheets...")
    try:
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
            logging.info(f"Inserting {len(rows_to_append)} new rows to Google Sheet at row 2...")
            wks.insert_rows(rows_to_append, row=2)
            logging.info("Successfully updated Google Sheet.")
        else:
            logging.info("No new activities to append.")

        # Update state file on success
        state_file.write_text(now.isoformat())

    except Exception as e:
        logging.error(f"Failed to send data to Google Sheets: {e}")


if __name__ == "__main__":
    sync_stats()
