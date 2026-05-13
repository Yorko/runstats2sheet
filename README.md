# Garmin to Google Sheets Sync

This application fetches your running stats from Garmin Connect and writes them directly to a Google Sheet using the `gspread` library.

## Prerequisites

- Python 3.12+
- `uv` for dependency management

## Setup

### 1. Project Environment

Copy the `.env.example` file to `.env` and fill in your Garmin credentials:

```bash
cp .env.example .env
```

Edit `.env`:
```env
GARMIN_EMAIL=your_email@example.com
GARMIN_PASSWORD=your_password
SPREADSHEET_ID=your_spreadsheet_id
```

### 2. Google Sheets Setup (via `gspread`)

For a clean integration, we use the `gspread` library with a Google Cloud Service Account.

1.  Go to the [Google Cloud Console](https://console.cloud.google.com/) and create a project.
2.  Enable the **Google Sheets API**.
3.  Go to **IAM & Admin > Service Accounts**, create a Service Account, and download a **JSON key**.
4.  You have two options for managing this key:
    -   **Option A (Local File):** Rename the downloaded file to `service_account.json` and place it in this project root.
    -   **Option B (Secret Manager):** Store the contents of the JSON key in Google Cloud Secret Manager.
        1.  Enable the **Secret Manager API**.
        2.  Create a secret and paste the JSON key content as the secret value.
        3.  Add `GARMIN_SHEET_SECRET_NAME` to your `.env` file with the full resource name (e.g., `projects/YOUR_PROJECT_ID/secrets/YOUR_SECRET_NAME/versions/latest`).
        4.  Ensure the environment where you run the script has access to Google Cloud credentials (e.g., via `gcloud auth application-default login` or a service account attached to the resource).
5.  **Share** your Google Sheet with the Service Account email (found in the JSON file) as "Editor".
6.  Add `SPREADSHEET_ID` to your `.env` file.


## Installation & Running

You can use the provided `Makefile` for common commands:

```bash
make sync
```

Or run the script using `uv` directly:

```bash
uv lock
uv run python -m src.sync_stats
```

## Form Example
![Garmin Running Stats](img/garmin_running_stats.png)

## TODOs
- [ ] Setup a cron job or GitHub Actions to run the script periodically.
- [ ] Deduplicate records (ensure same activity is not logged multiple times).
- [ ] (Stretch) Add an agent to analyze the stats.
