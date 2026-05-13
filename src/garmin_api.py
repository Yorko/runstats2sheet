import os
import sys
import json
from pathlib import Path
from getpass import getpass
from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)
from src import config

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
    tokenstore = config.GARMINTOKENS
    tokenstore_path = str(Path(tokenstore).expanduser())

    # Wipe token if .env is newer
    token_file = Path(tokenstore_path) / "garmin_tokens.json"
    env_file = Path(".env")
    try:
        if token_file.exists() and env_file.exists():
            if env_file.stat().st_mtime > token_file.stat().st_mtime:
                print(".env file modified since last token save. Wiping token to force re-auth.")
                try:
                    token_file.unlink()
                except Exception as e:
                    print(f"Warning: Failed to remove token file: {e}")
    except PermissionError as e:
        print(f"Warning: Permission denied when accessing token file: {e}")

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
            email = config.GARMIN_EMAIL or input("Email: ").strip()
            password = config.GARMIN_PASSWORD or getpass("Password: ")

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
