import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src import config, sync_stats


# --------------------------------------------------------------------------- #
# Fixtures / builders
# --------------------------------------------------------------------------- #

def garmin_activity(**overrides) -> dict:
    """A Garmin summary activity with the fields sync_stats reads."""
    activity = {
        "activityId": 111,
        "startTimeLocal": "2026-09-01 07:30:00",
        "activityName": "Morning Run",
        "activityType": {"typeKey": "running"},
        "distance": 10250.0,          # metres
        "duration": 3120.0,           # seconds
        "averageHR": 150,
        "maxHR": 172,
        "averageSpeed": 3.0,          # m/s
        "trainingEffect": 3.44,
        "anaerobicTrainingEffect": 1.26,
        "averageRunningCadenceInStepsPerMinute": 172.4,
        "maxRunningCadenceInStepsPerMinute": 186.6,
        "vO2MaxPreciseValue": 52.4,
        "calories": 720,
        "trainingEffectLabel": "TEMPO",
    }
    activity.update(overrides)
    return activity


def garmin_api_mock(activities, splits=None, hr_zones=None):
    api = MagicMock()
    api.get_activities.return_value = activities
    api.get_activity_hr_in_timezones.return_value = (
        {"1": 120, "2": 900} if hr_zones is None else hr_zones
    )
    api.get_activity_splits.return_value = {"splitDTOs": splits or []}
    return api


@pytest.fixture
def garmin(monkeypatch):
    """Install a Garmin API mock and return it for per-test configuration."""
    def install(activities, **kwargs):
        api = garmin_api_mock(activities, **kwargs)
        monkeypatch.setattr(sync_stats.garmin_api, "init_api", lambda: api)
        return api
    return install


def strava_activity(**overrides):
    extra = {
        "average_heartrate": 150.4,
        "max_heartrate": 172.0,
        "average_cadence": 86.0,      # rpm, Strava-style
        "max_cadence": 93.0,
        "type": "Run",
        "calories": 720.4,
        "splits_metric": [],
    }
    extra.update(overrides.pop("extra", {}))
    activity = SimpleNamespace(
        id=222,
        name="Evening Run",
        start_date_local=datetime(2026, 9, 1, 18, 15, 0),
        distance=10250.0,
        moving_time=3120.0,
        average_speed=3.0,
        model_extra=extra,
    )
    for key, value in overrides.items():
        setattr(activity, key, value)
    return activity


@pytest.fixture
def strava(monkeypatch):
    """Install a Strava API mock; HR-zone HTTP calls return an empty payload."""
    def install(activities, detailed=None, zones_response=None):
        api = MagicMock()
        api.settings.access_token = "token"
        api.get_activities.return_value = activities
        api.get_activity_detailed.return_value = detailed or activities[0]
        monkeypatch.setattr(sync_stats.strava_api, "init_api", lambda: api)
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = zones_response or []
        monkeypatch.setattr(sync_stats.requests, "get", lambda *a, **kw: response)
        return api
    return install


# --------------------------------------------------------------------------- #
# fetch_garmin_activities
# --------------------------------------------------------------------------- #

def test_garmin_maps_units_and_pace(garmin):
    garmin([garmin_activity()])

    (act,) = sync_stats.fetch_garmin_activities(set())

    assert act["distance"] == 10.25          # metres -> km
    assert act["duration"] == 52.0           # seconds -> minutes
    assert act["pace"] == 5.56               # 16.666 / 3.0 m/s
    assert act["activityType"] == "running"
    assert act["averageHR"] == 150
    assert act["maxHR"] == 172
    assert act["TE_aerobic"] == 3.4
    assert act["TE_anaerobic"] == 1.3
    assert act["avgCadence"] == 172.4
    assert act["vo2max"] == 52.4
    assert act["calories"] == 720
    assert json.loads(act["HR_zones"]) == {"1": 120, "2": 900}


def test_garmin_skips_known_activities_without_detail_calls(garmin):
    known = "2026-09-01 07:30:00"
    api = garmin([
        garmin_activity(startTimeLocal=known),
        garmin_activity(activityId=112, startTimeLocal="2026-08-30 07:30:00"),
    ])

    result = sync_stats.fetch_garmin_activities({known})

    assert [a["startTimeLocal"] for a in result] == ["2026-08-30 07:30:00"]
    # The expensive per-activity endpoints must only run for the new activity.
    assert api.get_activity_splits.call_count == 1
    api.get_activity_splits.assert_called_once_with(112)


def test_garmin_returns_empty_when_login_fails(monkeypatch):
    monkeypatch.setattr(sync_stats.garmin_api, "init_api", lambda: None)

    assert sync_stats.fetch_garmin_activities(set()) == []


def test_garmin_exits_when_activity_listing_fails(garmin):
    api = garmin([])
    api.get_activities.side_effect = RuntimeError("boom")

    with pytest.raises(SystemExit):
        sync_stats.fetch_garmin_activities(set())


def test_garmin_hr_zone_failure_yields_empty_json(garmin):
    api = garmin([garmin_activity()])
    api.get_activity_hr_in_timezones.side_effect = RuntimeError("no zones")

    (act,) = sync_stats.fetch_garmin_activities(set())

    assert act["HR_zones"] == "{}"


def test_garmin_keeps_only_distance_splits(garmin):
    splits = [
        {"splitType": "INTERVAL_ACTIVE", "lapIndex": 9, "distance": 400, "duration": 90,
         "averageSpeed": 4.0},
        {"splitType": "RKM", "lapIndex": 1, "distance": 1000, "duration": 330.0,
         "averageSpeed": 3.03, "averageHR": 148.6, "averageRunCadence": 171.2},
        {"splitType": "RKM", "lapIndex": 2, "distance": 1000, "duration": 320.0,
         "averageSpeed": 3.12, "averageHR": 152.1, "averageRunCadence": 173.8},
    ]
    garmin([garmin_activity()], splits=splits)

    (act,) = sync_stats.fetch_garmin_activities(set())
    parsed = json.loads(act["split_stats"])

    assert [s["split"] for s in parsed] == [1, 2]
    assert parsed[0] == {
        "split": 1,
        "distance": 1.0,
        "time": "5:30",
        "pace": "5:30",
        "avg_hr": 149,
        "avg_cadence": 171,
    }


def test_garmin_falls_back_to_all_splits_when_no_distance_type(garmin):
    splits = [
        {"splitType": "INTERVAL_ACTIVE", "lapIndex": 1, "distance": 400,
         "duration": 96.0, "averageSpeed": 4.166},
    ]
    garmin([garmin_activity()], splits=splits)

    (act,) = sync_stats.fetch_garmin_activities(set())
    parsed = json.loads(act["split_stats"])

    assert len(parsed) == 1
    assert parsed[0]["distance"] == 0.4
    assert parsed[0]["time"] == "1:36"


def test_garmin_uses_max_metrics_when_vo2max_is_integral(garmin):
    api = garmin([garmin_activity(vO2MaxPreciseValue=None, vO2MaxValue=52)])
    api.get_max_metrics.return_value = [{"generic": "ignored"},
                                        {"vo2MaxPreciseValue": 52.7}]

    (act,) = sync_stats.fetch_garmin_activities(set())

    api.get_max_metrics.assert_called_once_with("2026-09-01")
    assert act["vo2max"] == 52.7


def test_garmin_keeps_precise_vo2max_without_extra_call(garmin):
    api = garmin([garmin_activity(vO2MaxPreciseValue=52.4)])

    (act,) = sync_stats.fetch_garmin_activities(set())

    api.get_max_metrics.assert_not_called()
    assert act["vo2max"] == 52.4


def test_garmin_max_metrics_failure_leaves_vo2max_unchanged(garmin):
    api = garmin([garmin_activity(vO2MaxPreciseValue=None, vO2MaxValue=52)])
    api.get_max_metrics.side_effect = RuntimeError("unavailable")

    (act,) = sync_stats.fetch_garmin_activities(set())

    assert act["vo2max"] == 52.0


def test_garmin_debug_prints_but_still_skips_duplicate(garmin, monkeypatch, capsys):
    monkeypatch.setattr(config, "DEBUG", True)
    known = "2026-09-01 07:30:00"
    garmin([garmin_activity(startTimeLocal=known)])

    assert sync_stats.fetch_garmin_activities({known}) == []
    assert "Morning Run" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# fetch_strava_hr_zones
# --------------------------------------------------------------------------- #

def test_strava_hr_zones_parsed_from_heartrate_bucket(monkeypatch):
    payload = [
        {"type": "power", "distribution_bucket": [{"time": 1}]},
        {"type": "heartrate", "distribution_bucket": [
            {"time": 120}, {"time": 900}, {"time": 60}]},
    ]
    response = MagicMock(status_code=200)
    response.json.return_value = payload
    monkeypatch.setattr(sync_stats.requests, "get", lambda *a, **kw: response)
    api = MagicMock()
    api.settings.access_token = "token"

    assert sync_stats.fetch_strava_hr_zones(api, 1) == {"1": 120, "2": 900, "3": 60}


@pytest.mark.parametrize("failure", ["status", "exception"])
def test_strava_hr_zones_degrade_to_empty(monkeypatch, failure):
    if failure == "status":
        response = MagicMock(status_code=401)
        monkeypatch.setattr(sync_stats.requests, "get", lambda *a, **kw: response)
    else:
        def boom(*a, **kw):
            raise RuntimeError("network down")
        monkeypatch.setattr(sync_stats.requests, "get", boom)
    api = MagicMock()
    api.settings.access_token = "token"

    assert sync_stats.fetch_strava_hr_zones(api, 1) == {}


# --------------------------------------------------------------------------- #
# fetch_strava_activities
# --------------------------------------------------------------------------- #

def test_strava_maps_units_and_start_time(strava):
    strava([strava_activity()])

    (act,) = sync_stats.fetch_strava_activities(set())

    assert act["startTimeLocal"] == "2026-09-01 18:15:00"
    assert act["distance"] == 10.25
    assert act["duration"] == 52.0
    assert act["pace"] == 5.56
    assert act["averageHR"] == 150
    assert act["activityType"] == "Run"
    assert act["calories"] == 720


def test_strava_doubles_run_cadence_from_rpm(strava):
    strava([strava_activity()])

    (act,) = sync_stats.fetch_strava_activities(set())

    assert act["avgCadence"] == 172.0
    assert act["maxCadence"] == 186.0


def test_strava_leaves_ride_cadence_alone(strava):
    strava([strava_activity(extra={"type": "Ride"})])

    (act,) = sync_stats.fetch_strava_activities(set())

    assert act["avgCadence"] == 86.0
    assert act["activityType"] == "Ride"


def test_strava_splits_are_formatted_and_cadence_corrected(strava):
    splits = [
        {"split": 1, "distance": 1000.0, "elapsed_time": 330,
         "average_speed": 3.03, "average_heartrate": 148.6, "average_cadence": 85.6},
    ]
    strava([strava_activity(extra={"splits_metric": splits})])

    (act,) = sync_stats.fetch_strava_activities(set())

    assert json.loads(act["split_stats"]) == [{
        "split": 1,
        "distance": 1.0,
        "time": "5:30",
        "pace": "5:30",
        "avg_hr": 149,
        "avg_cadence": 171,
    }]


def test_strava_falls_back_to_summary_when_detail_fetch_fails(strava):
    api = strava([strava_activity()])
    api.get_activity_detailed.side_effect = RuntimeError("429")

    (act,) = sync_stats.fetch_strava_activities(set())

    assert act["activityName"] == "Evening Run"
    assert act["distance"] == 10.25


def test_strava_skips_known_activities_without_detail_calls(strava):
    known = "2026-09-01 18:15:00"
    api = strava([strava_activity()])

    assert sync_stats.fetch_strava_activities({known}) == []
    api.get_activity_detailed.assert_not_called()


def test_strava_returns_empty_when_client_init_fails(monkeypatch):
    monkeypatch.setattr(sync_stats.strava_api, "init_api", lambda: None)

    assert sync_stats.fetch_strava_activities(set()) == []


def test_strava_exits_when_activity_listing_fails(monkeypatch):
    api = MagicMock()
    api.get_activities.side_effect = RuntimeError("boom")
    monkeypatch.setattr(sync_stats.strava_api, "init_api", lambda: api)

    with pytest.raises(SystemExit):
        sync_stats.fetch_strava_activities(set())


# --------------------------------------------------------------------------- #
# sync_stats
# --------------------------------------------------------------------------- #

@pytest.fixture
def sheet(monkeypatch):
    """Stub out gspread and return the worksheet mock."""
    wks = MagicMock()
    wks.get_all_values.return_value = []
    client = MagicMock()
    client.open_by_key.return_value.sheet1 = wks
    monkeypatch.setattr(sync_stats.gspread, "service_account", lambda filename: client)
    return wks


@pytest.fixture
def fetched(monkeypatch):
    """Stub both fetchers; returns the recorder for the Garmin one."""
    calls = []

    def install(activities):
        def fetch(existing_start_times):
            calls.append(existing_start_times)
            return activities
        monkeypatch.setattr(sync_stats, "fetch_garmin_activities", fetch)
        monkeypatch.setattr(sync_stats, "fetch_strava_activities", fetch)
        return calls
    return install


def synced_activity(**overrides) -> dict:
    activity = {
        "startTimeLocal": "2026-09-01 07:30:00",
        "activityName": "Morning Run",
        "activityType": "running",
        "distance": 10.25,
        "duration": 52.0,
        "averageHR": 150,
        "maxHR": 172,
        "pace": 5.56,
        "TE_aerobic": 3.4,
        "TE_anaerobic": 1.3,
        "HR_zones": '{"1": 120}',
        "split_stats": "[]",
        "vo2max": 52.4,
        "calories": 720,
        "trainingEffectLabel": "TEMPO",
        "avgCadence": 172.4,
        "maxCadence": 186.6,
    }
    activity.update(overrides)
    return activity


def test_sync_skips_when_last_sync_is_recent(sheet, fetched):
    Path(".last_sync_timestamp").write_text(
        (datetime.now() - timedelta(minutes=30)).isoformat()
    )
    calls = fetched([synced_activity()])

    sync_stats.sync_stats()

    assert calls == []
    sheet.get_all_values.assert_not_called()


def test_sync_runs_when_last_sync_is_old(sheet, fetched):
    Path(".last_sync_timestamp").write_text(
        (datetime.now() - timedelta(hours=2)).isoformat()
    )
    fetched([synced_activity()])

    sync_stats.sync_stats()

    sheet.insert_rows.assert_called_once()


def test_sync_runs_when_state_file_is_corrupt(sheet, fetched):
    Path(".last_sync_timestamp").write_text("not-a-timestamp")
    fetched([synced_activity()])

    sync_stats.sync_stats()

    sheet.insert_rows.assert_called_once()


def test_sync_writes_headers_to_an_empty_sheet(sheet, fetched):
    fetched([])

    sync_stats.sync_stats()

    headers = sheet.insert_row.call_args.args[0]
    assert headers[:4] == ["Timestamp", "Start time", "Activity Name", "Activity Type"]
    assert headers[13:15] == ["HR Zones", "split_stats"]
    assert len(headers) == 18


def test_sync_collects_existing_start_times_and_ignores_header(sheet, fetched):
    sheet.get_all_values.return_value = [
        ["Timestamp", "Start time", "Activity Name", "Activity Type", "Distance",
         "Duration", "Av HR", "Max HR", "Pace", "Avg Cadence", "Max Cadence",
         "Aerobic TE", "Anaerobic TE", "HR Zones", "split_stats", "VO2MAX",
         "Calories", "Training Effect"],
        ["2026-09-01 08:00:00", "2026-09-01 07:30:00", "Morning Run", "running",
         "10.25", "52.0", "150", "172", "5:34", "172.4", "186.6", "3.4", "1.3",
         "{}", "[]", "52.4", "720", "TEMPO"],
        [],
    ]
    calls = fetched([])

    sync_stats.sync_stats()

    assert calls == [{"2026-09-01 07:30:00"}]
    sheet.insert_row.assert_not_called()  # headers already present


def test_sync_appends_row_in_column_order_at_row_two(sheet, fetched):
    fetched([synced_activity()])

    sync_stats.sync_stats()

    rows = sheet.insert_rows.call_args.args[0]
    assert sheet.insert_rows.call_args.kwargs == {"row": 2}
    assert len(rows) == 1
    row = rows[0]
    assert row[1:] == [
        "2026-09-01 07:30:00", "Morning Run", "running", 10.25, 52.0, 150, 172,
        "5:34", 172.4, 186.6, 3.4, 1.3, '{"1": 120}', "[]", 52.4, 720, "TEMPO",
    ]
    datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")  # sync timestamp column


def test_sync_updates_state_file_after_a_successful_write(sheet, fetched):
    fetched([synced_activity()])

    sync_stats.sync_stats()

    written = datetime.fromisoformat(Path(".last_sync_timestamp").read_text())
    assert datetime.now() - written < timedelta(minutes=1)


def test_sync_does_not_touch_state_file_when_nothing_is_new(sheet, fetched):
    fetched([])

    sync_stats.sync_stats()

    sheet.insert_rows.assert_not_called()
    assert not Path(".last_sync_timestamp").exists()


def test_sync_debug_mode_writes_nothing(sheet, fetched, monkeypatch):
    monkeypatch.setattr(config, "DEBUG", True)
    fetched([synced_activity()])

    sync_stats.sync_stats()

    sheet.insert_rows.assert_not_called()
    assert not Path(".last_sync_timestamp").exists()


def test_sync_exits_on_unknown_source(sheet, monkeypatch):
    monkeypatch.setattr(config, "SYNC_SOURCE", "runkeeper")

    with pytest.raises(SystemExit):
        sync_stats.sync_stats()


def test_sync_uses_strava_fetcher_when_configured(sheet, monkeypatch):
    monkeypatch.setattr(config, "SYNC_SOURCE", "strava")
    monkeypatch.setattr(sync_stats, "fetch_strava_activities", lambda s: [])
    monkeypatch.setattr(sync_stats, "fetch_garmin_activities",
                        lambda s: pytest.fail("Garmin fetcher must not run"))

    sync_stats.sync_stats()


def test_sync_reads_credentials_from_secret_manager_when_configured(
    monkeypatch, fetched
):
    monkeypatch.setattr(config, "GARMIN_SHEET_SECRET_NAME",
                        "projects/p/secrets/s/versions/latest")
    secret_client = MagicMock()
    secret_client.access_secret_version.return_value.payload.data = b'{"type": "sa"}'
    monkeypatch.setattr(sync_stats.secretmanager, "SecretManagerServiceClient",
                        lambda: secret_client)
    gs_client = MagicMock()
    gs_client.open_by_key.return_value.sheet1.get_all_values.return_value = []
    from_dict = MagicMock(return_value=gs_client)
    monkeypatch.setattr(sync_stats.gspread, "service_account_from_dict", from_dict)
    fetched([])

    sync_stats.sync_stats()

    from_dict.assert_called_once_with({"type": "sa"})
    secret_client.access_secret_version.assert_called_once_with(
        request={"name": "projects/p/secrets/s/versions/latest"}
    )


def test_sync_exits_when_sheet_access_fails(monkeypatch, fetched):
    def boom(filename):
        raise RuntimeError("no such spreadsheet")
    monkeypatch.setattr(sync_stats.gspread, "service_account", boom)
    fetched([])

    with pytest.raises(SystemExit):
        sync_stats.sync_stats()
