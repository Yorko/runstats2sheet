import contextlib
import logging
import os
import sys
import json
from datetime import date
from getpass import getpass
from pathlib import Path

import requests
import pandas as pd
from dotenv import load_dotenv
from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

# Load environment variables from .env file
load_dotenv()

logging.getLogger("garminconnect").setLevel(logging.CRITICAL)

GARMIN_EMAIL = os.getenv("GARMIN_EMAIL")
GARMIN_PASSWORD = os.getenv("GARMIN_PASSWORD")
GOOGLE_FORM_URL = os.getenv("GOOGLE_FORM_URL")

if not GOOGLE_FORM_URL:
    print("Error: Missing GOOGLE_FORM_URL environment variable. Please check your .env file.")
    sys.exit(1)

# Load entry IDs
ENTRY_START_TIME = os.getenv("ENTRY_START_TIME", "entry.111111")
ENTRY_NAME = os.getenv("ENTRY_NAME", "entry.222222")
ENTRY_DISTANCE = os.getenv("ENTRY_DISTANCE", "entry.333333")
ENTRY_DURATION = os.getenv("ENTRY_DURATION", "entry.444444")
ENTRY_AVG_HR = os.getenv("ENTRY_AVG_HR", "entry.555555")
ENTRY_MAX_HR = os.getenv("ENTRY_MAX_HR", "entry.666666")
ENTRY_PACE = os.getenv("ENTRY_PACE", "entry.777777")
ENTRY_TE_AEROBIC = os.getenv("ENTRY_TE_AEROBIC", "entry.888888")
ENTRY_TE_ANAEROBIC = os.getenv("ENTRY_TE_ANAEROBIC", "entry.999999")
ENTRY_HR_ZONES = os.getenv("ENTRY_HR_ZONES", "entry.000000")



def safe_api_call(api_method, *args, **kwargs):
    """Call an API method and return (success, result, error_message)."""
    try:
        result = api_method(*args, **kwargs)
        return True, result, None

    except GarminConnectAuthenticationError as e:
        return False, None, f"Authentication error: {e}"
    except GarminConnectTooManyRequestsError as e:
        return False, None, f"Rate limit exceeded: {e}"
    except GarminConnectConnectionError as e:
        error_str = str(e)
        if "400" in error_str:
            return (
                False,
                None,
                "Not available (400) — feature may not be enabled for your account",
            )
        if "401" in error_str:
            return False, None, "Authentication required (401) — please re-authenticate"
        if "403" in error_str:
            return False, None, "Access denied (403) — account may not have permission"
        if "404" in error_str:
            return False, None, "Not found (404) — endpoint may have moved"
        if "429" in error_str:
            return False, None, "Rate limit (429) — please wait before retrying"
        if "500" in error_str:
            return False, None, "Server error (500) — Garmin servers are having issues"
        return False, None, f"Connection error: {e}"
    except Exception as e:
        return False, None, f"Unexpected error: {e}"


def init_api() -> Garmin | None:
    """Initialise Garmin API, restoring saved tokens or logging in fresh."""
    tokenstore = os.getenv("GARMINTOKENS", "~/.garminconnect")
    tokenstore_path = str(Path(tokenstore).expanduser())

    # Try to restore saved tokens
    try:
        garmin = Garmin()
        garmin.login(tokenstore_path)
        print("Logged in using saved tokens.")
        return garmin

    except GarminConnectTooManyRequestsError as err:
        print(f"Rate limit: {err}")
        sys.exit(1)

    except (GarminConnectAuthenticationError, GarminConnectConnectionError):
        print("No valid tokens found — please log in.")

    # Fresh credential login with MFA support
    while True:
        try:
            email = os.getenv("GARMIN_EMAIL") or input("Email: ").strip()
            password = os.getenv("GARMIN_PASSWORD") or getpass("Password: ")

            garmin = Garmin(
                email=email,
                password=password,
                prompt_mfa=lambda: input("MFA code: ").strip(),
            )
            garmin.login(tokenstore_path)
            print(f"Login successful. Tokens saved to: {tokenstore_path}")
            return garmin

        except GarminConnectTooManyRequestsError as err:
            print(f"Rate limit: {err}")
            sys.exit(1)

        except GarminConnectAuthenticationError:
            print("Wrong credentials — please try again.")
            continue

        except GarminConnectConnectionError as err:
            print(f"Connection error: {err}")
            return None

        except KeyboardInterrupt:
            return None


def main():
    api = init_api()
    if not api:
        print("Failed to initialize Garmin API.")
        return

    print("Fetching activities...")
    # Get last 5 activities
    success, activities, err = safe_api_call(api.get_activities, 0, 5)
    if not success:
        print(f"Failed to fetch activities: {err}")
        sys.exit(1)

    print(f"Found {len(activities)} activities. Processing...")

    processed_activities = []
    for activity in activities:
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
            
        te_aerobic = activity.get('trainingEffect', 0)
        te_anaerobic = activity.get('anaerobicTrainingEffect', 0)
        start_time = activity['startTimeLocal']

        # Fetch HR zones
        success_hr, hr_zones, err_hr = safe_api_call(api.get_activity_hr_in_timezones, activity_id)
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
        success_count = 0
        for activity in processed_activities:
            form_data = {
                ENTRY_START_TIME: activity["startTimeLocal"],
                ENTRY_NAME: activity["activityName"],
                ENTRY_DISTANCE: activity["distance"],
                ENTRY_DURATION: activity.get("duration", 0),
                ENTRY_AVG_HR: activity.get("averageHR", 0),
                ENTRY_MAX_HR: activity.get("maxHR", 0),
                ENTRY_PACE: activity.get("pace", 0),
                ENTRY_TE_AEROBIC: activity.get("TE_aerobic", 0),
                ENTRY_TE_ANAEROBIC: activity.get("TE_anaerobic", 0),
                ENTRY_HR_ZONES: activity.get("HR_zones", "{}")
            }
            
            print(f"Submitting {activity['activityName']}...")
            response = requests.post(GOOGLE_FORM_URL, data=form_data)
            
            if response.status_code == 200:
                success_count += 1
            else:
                print(f"Failed to submit {activity['activityName']}. HTTP Error {response.status_code}")
        
        print(f"Successfully submitted {success_count} out of {len(processed_activities)} activities.")

    except Exception as e:
        print(f"Failed to send data to Google Sheets: {e}")

    # Show result as pandas dataframe
    print("\nResults as Pandas DataFrame:")
    df = pd.DataFrame(processed_activities)
    print(df)

if __name__ == "__main__":
    main()
