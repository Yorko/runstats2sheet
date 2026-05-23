import os
from pathlib import Path
from strava_client.client import StravaClient
from src import config

def init_api() -> StravaClient | None:
    """Initialize the Strava API client.
    If .strava.secrets does not exist, create it with CLIENT_ID, CLIENT_SECRET
    and a dummy ACCESS_TOKEN to trigger the browser authentication code exchange flow.
    """
    client_id = config.STRAVA_CLIENT_ID
    client_secret = config.STRAVA_CLIENT_SECRET

    if not client_id or not client_secret:
        print("Error: STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET must be set in .env file for Strava sync.")
        return None

    secrets_path = Path(".strava.secrets")
    if not secrets_path.exists():
        print("Initializing .strava.secrets with Client ID and Secret from .env...")
        try:
            # Create initial .strava.secrets file to satisfy Pydantic Settings requirements
            secrets_content = (
                f"CLIENT_ID={client_id}\n"
                f"CLIENT_SECRET={client_secret}\n"
                f"ACCESS_TOKEN=initial_dummy_token\n"
            )
            secrets_path.write_text(secrets_content)
        except Exception as e:
            print(f"Error creating .strava.secrets: {e}")
            return None

    try:
        # Initialize and return the StravaClient.
        # Under the hood, if REFRESH_TOKEN is missing, it will trigger the
        # webbrowser code request flow and block on user console input.
        client = StravaClient()
        return client
    except Exception as e:
        print(f"Failed to initialize StravaClient: {e}")
        return None
