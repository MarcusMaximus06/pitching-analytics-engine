from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone

import pandas as pd

from daily_nfl_auto import (
    NFL_LOG_COLUMNS,
    build_today_rows,
    build_window_rows,
    ensure_headers,
    grade_pending,
    log_rows,
    parse_odds_board,
)


class FakeWorksheet:
    def __init__(self, values):
        self.values = deepcopy(values)
        self.col_count = max((len(row) for row in values), default=0)

    def get_all_values(self):
        return deepcopy(self.values)

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

    def append_rows(self, rows, **_kwargs):
        self.values.extend(deepcopy(rows))

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


def _scoreboard_payload(completed: bool) -> dict:
    return {
        "events": [
            {
                "id": "401",
                "date": "2026-09-13T17:00:00Z",
                "season": {"year": 2026, "slug": "regular-season"},
                "week": {"number": 2},
                "status": {"type": {"completed": completed, "state": "post" if completed else "in"}},
                "competitions": [
                    {
                        "neutralSite": False,
                        "competitors": [
                            {
                                "homeAway": "home",
                                "score": "27" if completed else "10",
                                "team": {"displayName": "Seattle Seahawks"},
                            },
                            {
                                "homeAway": "away",
                                "score": "20" if completed else "7",
                                "team": {"displayName": "Arizona Cardinals"},
                            },
                        ],
                    }
                ],
            }
        ]
    }


def _pending_row() -> list[str]:
    row = {header: "" for header in NFL_LOG_COLUMNS}
    row.update(
        {
            "Log ID": "2026-09-13|arizonacardinals|seattleseahawks",
            "Date": "2026-09-13",
            "Away Team": "Arizona Cardinals",
            "Home Team": "Seattle Seahawks",
            "Model Pick": "Seattle Seahawks",
            "Status": "PENDING",
            "Event ID": "401",
        }
    )
    return [row[header] for header in NFL_LOG_COLUMNS]


def test_blank_sheet_receives_full_schema():
    worksheet = FakeWorksheet([[]])
    headers, _ = ensure_headers(worksheet)
    assert headers == NFL_LOG_COLUMNS
    assert worksheet.values[0] == NFL_LOG_COLUMNS


def test_grader_waits_for_final_then_updates_exact_event():
    worksheet = FakeWorksheet([NFL_LOG_COLUMNS, _pending_row()])
    pending = grade_pending(worksheet, session=FakeSession(_scoreboard_payload(False)))
    assert pending["graded"] == 0

    final = grade_pending(worksheet, session=FakeSession(_scoreboard_payload(True)))
    row = dict(zip(worksheet.values[0], worksheet.values[1]))
    assert final["graded"] == 1
    assert row["Actual Winner"] == "Seattle Seahawks"
    assert row["Model Result"] == "WIN"
    assert row["Status"] == "WIN"
    assert row["Early Vegas Result"] == ""


def test_model_only_pregame_row_logs_once():
    schedule = pd.DataFrame(
        [
            {
                "game_id": "402",
                "season": 2026,
                "week": 2,
                "start_date": "2026-09-14T23:00:00Z",
                "away_team": "Arizona Cardinals",
                "home_team": "Seattle Seahawks",
                "neutral_site": False,
                "completed": False,
                "away_score": float("nan"),
                "home_score": float("nan"),
            }
        ]
    )
    now = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)
    rows, warnings = build_today_rows(schedule, pd.DataFrame(), now.date(), now)
    assert len(rows) == 1
    assert rows[0]["Vegas Pick"] == ""
    assert any("Odds unavailable" in warning for warning in warnings)

    worksheet = FakeWorksheet([NFL_LOG_COLUMNS])
    first = log_rows(rows, worksheet)
    second = log_rows(rows, worksheet)
    assert first["logged"] == 1
    assert second["duplicates"] == 1


def test_window_rows_include_future_slate_and_render_percent_signs():
    schedule = pd.DataFrame(
        [
            {
                "game_id": "402",
                "season": 2026,
                "week": 2,
                "start_date": "2026-09-17T23:00:00Z",
                "away_team": "Arizona Cardinals",
                "home_team": "Seattle Seahawks",
                "neutral_site": False,
                "completed": False,
                "away_score": float("nan"),
                "home_score": float("nan"),
            }
        ]
    )
    now = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)
    rows, _ = build_window_rows(schedule, pd.DataFrame(), date(2026, 9, 15), now, lookahead_days=3)
    assert len(rows) == 1
    assert rows[0]["Model Away %"].endswith("%")
    assert rows[0]["Model Home %"].endswith("%")


def test_multi_book_odds_are_devigged_and_mapped_exactly():
    board = parse_odds_board(
        [
            {
                "id": "402",
                "away_team": "Arizona Cardinals",
                "home_team": "Seattle Seahawks",
                "bookmakers": [
                    {
                        "markets": [
                            {
                                "key": "h2h",
                                "outcomes": [
                                    {"name": "Arizona Cardinals", "price": 130},
                                    {"name": "Seattle Seahawks", "price": -150},
                                ],
                            }
                        ]
                    }
                ],
            }
        ]
    )
    assert len(board) == 1
    assert board.iloc[0]["Event ID"] == "402"
    assert 50 < board.iloc[0]["Vegas Home %"] < 70
