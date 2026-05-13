import contextlib
import logging
import os
import sys
import json
from datetime import date, datetime
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

logging.getLogger("garminconnect").setLevel(logging.CRITICAL)

if not config.SPREADSHEET_ID:
    print("Error: Missing SPREADSHEET_ID environment variable. Please check your .env file.")
    sys.exit(1)


def sync_garmin_stats():
    api = garmin_api.init_api()
    if not api:
        print("Failed to initialize Garmin API.")
        return

    print("Fetching activities...")
    # Get last 100 activities
    success, activities, err = garmin_api.safe_api_call(api.get_activities, 0, 100)
    if not success:
        print(f"Failed to fetch activities: {err}")
        sys.exit(1)

    print(f"Found {len(activities)} activities. Processing...")

    processed_activities = []
    for i, activity in enumerate(activities):
        if i == 0:
            print(f"Debug: Available keys in activity: {list(activity.keys())}")
            
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
        start_time = activity['startTimeLocal']

        # Fetch HR zones
        success_hr, hr_zones, err_hr = garmin_api.safe_api_call(api.get_activity_hr_in_timezones, activity_id)
        if not success_hr:
            hr_zones = {}

        print(f"Processing activity: {name} ({start_time})")

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
            "HR_zones": json.dumps(hr_zones) if hr_zones else "{}"
        })

    if not processed_activities:
        print("No activities to send.")
        return

    print(f"Sending {len(processed_activities)} activities to Google Sheets...")
    try:
        # Initialize gspread
        secret_name = config.GARMIN_SHEET_SECRET_NAME
        if secret_name:
            print(f"Fetching service account key from Secret Manager: {secret_name}")
            try:
                client = secretmanager.SecretManagerServiceClient()
                response = client.access_secret_version(request={"name": secret_name})
                secret_json = response.payload.data.decode("UTF-8")
                gc = gspread.service_account_from_dict(json.loads(secret_json))
            except Exception as e:
                print(f"Error fetching secret or initializing gspread: {e}")
                sys.exit(1)
        else:
            print("Using local service_account.json")
            gc = gspread.service_account(filename="service_account.json")
        sh = gc.open_by_key(config.SPREADSHEET_ID)
        wks = sh.sheet1

        # Read existing data to check for duplicates
        try:
            existing_values = wks.get_all_values()
        except Exception as e:
            print(f"Warning: Failed to read existing data from sheet: {e}. Assuming empty.")
            existing_values = []

        existing_start_times = set()
        for row in existing_values:
            if not row:
                continue
            # Old schema: 10 columns, startTimeLocal at index 0
            # New schema: 11 columns, startTimeLocal at index 1
            if len(row) == 10:
                existing_start_times.add(row[0])
            elif len(row) >= 11:
                existing_start_times.add(row[1])

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
                activity["distance"],
                activity["duration"],
                activity["averageHR"],
                activity["maxHR"],
                activity["pace"],
                activity["TE_aerobic"],
                activity["TE_anaerobic"],
                activity["HR_zones"]
            ])
        
        if rows_to_append:
            print(f"Appending {len(rows_to_append)} new rows to Google Sheet...")
            wks.append_rows(rows_to_append)
            print("Successfully updated Google Sheet.")
        else:
            print("No new activities to append.")

    except Exception as e:
        print(f"Failed to send data to Google Sheets: {e}")


    # Show result as pandas dataframe
    print("\nResults as Pandas DataFrame:")
    df = pd.DataFrame(processed_activities)
    print(df)

if __name__ == "__main__":
    sync_garmin_stats()
