from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

from daily_ncaaf_auto import (
    LEGACY_HEADERS,
    LOG_HEADERS,
    build_prediction_records,
    ensure_log_headers,
    find_odds_for_game,
    get_log_stats,
    grade_pending_games,
    log_prediction_records,
)
from ncaaf_model import TeamState, WinnerModel
from scripts.analyze_ncaaf_log import analyze_rows


class FakeWorksheet:
    def __init__(self, values):
        self.values = deepcopy(values)
        self.col_count = max((len(row) for row in values), default=0)

    def get_all_values(self):
        return deepcopy(self.values)

    def append_row(self, row, **_kwargs):
        self.values.append(list(row))

    def append_rows(self, rows, **_kwargs):
        self.values.extend(deepcopy(rows))

    def resize(self, cols=None, **_kwargs):
        if cols:
            self.col_count = cols

    def update(self, _range_name=None, rows=None, range_name=None, values=None, **_kwargs):
        del _range_name, range_name
        rows = values if values is not None else rows
        if self.values:
            self.values[0] = list(rows[0])
        else:
            self.values = [list(rows[0])]

    def batch_update(self, updates, **_kwargs):
        for update in updates:
            cell = update["range"]
            letters = "".join(char for char in cell if char.isalpha())
            row_number = int("".join(char for char in cell if char.isdigit()))
            column = 0
            for char in letters:
                column = column * 26 + (ord(char.upper()) - 64)
            while len(self.values) < row_number:
                self.values.append([])
            row = self.values[row_number - 1]
            while len(row) < column:
                row.append("")
            row[column - 1] = update["values"][0][0]


class FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return deepcopy(self.payload)


class FakeSession:
    def __init__(self, payload):
        self.payload = payload

    def get(self, *_args, **_kwargs):
        return FakeResponse(self.payload)


def _scheduled_game(game_id=100, away="Away", home="Home"):
    return {
        "id": game_id,
        "game_id": game_id,
        "season": 2026,
        "week": 3,
        "start_date": "2026-09-12T17:00:00Z",
        "away_team": away,
        "home_team": home,
        "away_team_display": away,
        "home_team_display": home,
        "away_team_id": "1",
        "home_team_id": "2",
        "neutral_site": False,
        "completed": False,
        "status_state": "pre",
    }


def _scoreboard_payload(completed=True):
    return {
        "events": [
            {
                "id": "100",
                "date": "2026-09-12T17:00:00Z",
                "season": {"year": 2026},
                "week": {"number": 3},
                "status": {"type": {"completed": completed, "state": "post" if completed else "pre"}},
                "competitions": [
                    {
                        "neutralSite": False,
                        "competitors": [
                            {
                                "homeAway": "home",
                                "score": "31" if completed else "0",
                                "team": {"id": "2", "location": "Home", "displayName": "Home"},
                            },
                            {
                                "homeAway": "away",
                                "score": "20" if completed else "0",
                                "team": {"id": "1", "location": "Away", "displayName": "Away"},
                            },
                        ],
                    }
                ],
            }
        ]
    }


def test_header_extension_preserves_legacy_prefix():
    worksheet = FakeWorksheet([LEGACY_HEADERS, ["2026-09-01", "Away", "Home"]])
    headers, _ = ensure_log_headers(worksheet)
    assert headers[: len(LEGACY_HEADERS)] == LEGACY_HEADERS
    assert worksheet.values[1][:3] == ["2026-09-01", "Away", "Home"]
    assert "Game ID" in headers
    assert "Actual Winner" in headers


def test_blank_preallocated_sheet_receives_headers_in_first_row():
    worksheet = FakeWorksheet([[]])
    headers, _ = ensure_log_headers(worksheet)
    assert headers[: len(LEGACY_HEADERS)] == LEGACY_HEADERS
    assert worksheet.values[0][: len(LEGACY_HEADERS)] == LEGACY_HEADERS


def test_logs_missing_odds_once_without_inventing_market_values():
    worksheet = FakeWorksheet([LEGACY_HEADERS])
    model = WinnerModel.bootstrap()
    states = {"Away": TeamState(rating=1500), "Home": TeamState(rating=1600)}
    now = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)
    records, warnings = build_prediction_records([_scheduled_game()], [], states, model, now=now)
    assert len(records) == 1
    assert any("Odds unavailable" in warning for warning in warnings)

    first = log_prediction_records(records, worksheet=worksheet, now=now)
    second = log_prediction_records(records, worksheet=worksheet, now=now)
    headers = worksheet.values[0]
    row = dict(zip(headers, worksheet.values[1]))
    assert first["logged"] == 1
    assert second["duplicates"] == 1
    assert row["Away Odds"] == ""
    assert row["Home Odds"] == ""
    assert row["Edge"] == ""
    assert row["Notes"] == "No Vegas Odds"
    assert row["Result"] == "PENDING"


def test_started_game_is_not_modeled_or_logged():
    model = WinnerModel.bootstrap()
    states = {"Away": TeamState(), "Home": TeamState()}
    records, warnings = build_prediction_records(
        [_scheduled_game()],
        [],
        states,
        model,
        now=datetime(2026, 9, 12, 18, 0, tzinfo=timezone.utc),
    )
    assert records == []
    assert any("already started" in warning for warning in warnings)


def test_grader_uses_stable_game_id_and_only_final_status():
    worksheet = FakeWorksheet([LEGACY_HEADERS])
    model = WinnerModel.bootstrap()
    states = {"Away": TeamState(rating=1500), "Home": TeamState(rating=1600)}
    now = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
    game = _scheduled_game()
    game["start_date"] = "2026-09-05T17:00:00Z"
    records, _ = build_prediction_records([game], [], states, model, now=now)
    log_prediction_records(records, worksheet=worksheet, now=now)

    pending_payload = _scoreboard_payload(False)
    pending_payload["events"][0]["date"] = "2026-09-05T17:00:00Z"
    pending = grade_pending_games(worksheet=worksheet, session=FakeSession(pending_payload))
    assert pending["graded"] == 0
    final_payload = _scoreboard_payload(True)
    final_payload["events"][0]["date"] = "2026-09-05T17:00:00Z"
    final = grade_pending_games(worksheet=worksheet, session=FakeSession(final_payload))
    headers = worksheet.values[0]
    row = dict(zip(headers, worksheet.values[1]))
    assert final["graded"] == 1
    assert row["Result"] == "WIN"
    assert row["Actual Winner"] == "Home"


def test_miami_ohio_odds_never_pair_to_miami_florida():
    game = _scheduled_game(away="Clemson", home="Miami")
    game["home_team_display"] = "Miami Hurricanes"
    odds = [{"away_team": "Clemson Tigers", "home_team": "Miami (OH) RedHawks"}]
    match, warning = find_odds_for_game(game, odds)
    assert match is None
    assert warning is None


def test_vegas_accuracy_excludes_games_without_real_odds():
    worksheet = FakeWorksheet([LEGACY_HEADERS])
    model = WinnerModel.bootstrap()
    states = {
        "Away": TeamState(rating=1500),
        "Home": TeamState(rating=1600),
        "Away Two": TeamState(rating=1500),
        "Home Two": TeamState(rating=1600),
    }
    odds = [
        {
            "away_team": "Away",
            "home_team": "Home",
            "bookmakers": [
                {
                    "title": "Test Book",
                    "markets": [
                        {
                            "key": "h2h",
                            "outcomes": [
                                {"name": "Away", "price": 130},
                                {"name": "Home", "price": -150},
                            ],
                        }
                    ],
                }
            ],
        }
    ]
    games = [_scheduled_game(), _scheduled_game(101, "Away Two", "Home Two")]
    now = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)
    records, _ = build_prediction_records(games, odds, states, model, now=now)
    log_prediction_records(records, worksheet=worksheet, now=now)

    headers = worksheet.values[0]
    result_column = headers.index("Result")
    actual_column = headers.index("Actual Winner")
    predicted_column = headers.index("Predicted Winner")
    for row in worksheet.values[1:]:
        row[result_column] = "WIN"
        row[actual_column] = row[predicted_column]

    stats = get_log_stats(worksheet=worksheet)
    assert stats["graded"] == 2
    assert stats["model_accuracy"] == 1.0
    assert stats["vegas_games"] == 1
    assert stats["vegas_accuracy"] == 1.0


def test_live_review_measures_model_market_disagreement_from_snapshots():
    row = {header: "" for header in LOG_HEADERS}
    row.update(
        {
            "Date": "2026-09-12",
            "Away Team": "Away",
            "Home Team": "Home",
            "Model Home %": "60%",
            "Vegas Home %": "40%",
            "Predicted Winner": "Home",
            "Actual Winner": "Away",
            "Result": "LOSS",
            "Confidence": "Medium",
        }
    )
    report = analyze_rows([LOG_HEADERS, [row[header] for header in LOG_HEADERS]])
    assert report["graded"] == 1
    assert report["disagreement"]["games"] == 1
    assert report["disagreement"]["model_accuracy"] == 0.0
    assert report["disagreement"]["vegas_accuracy"] == 1.0
