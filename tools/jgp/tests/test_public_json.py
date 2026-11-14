"""Public standings include ordered tournament ids and member ids, grouped by JGP age band.

Unknown, missing and wrongly typed fields must be rejected.
"""
import json
from datetime import UTC, datetime, timedelta

import pytest

from jgp_standings.generation_core import PUBLIC_STANDINGS_FIELDS, serialize_standings_json


def _row(**overrides):
    row = {
        "placement": 1,
        "member_id": 577097,
        "name": "Åsa Öberg",
        "birth_year": 2013,
        "club": "SK Rockaden",
        "tournaments": {"6544": 25.0, "6540": 0.0},
        "total_points": 25.0,
    }
    row.update(overrides)
    return row


class TestTheShape:

    def test_last_updated_records_generation_time_in_utc(self):
        before = datetime.now(UTC).replace(microsecond=0)
        snapshot = json.loads(serialize_standings_json({}, tournament_ids=[]))
        after = datetime.now(UTC)

        last_updated = datetime.fromisoformat(snapshot["last_updated"])
        assert last_updated.utcoffset() == timedelta(0)
        assert before <= last_updated <= after

    def test_bands_keep_their_order_and_rows_keep_exactly_the_allowlisted_fields(self):
        text = serialize_standings_json({
            "2013": [_row(), _row(placement=2)],
            "2006-2009": [_row()],
            "2018 onward": [],
        }, tournament_ids=[6544, 6540])

        snapshot = json.loads(text)
        assert snapshot["tournament_ids"] == [6544, 6540]
        snapshot = snapshot["standings"]
        assert list(snapshot) == ["2013", "2006-2009", "2018 onward"]
        assert [row["placement"] for row in snapshot["2013"]] == [1, 2]
        assert set(snapshot["2013"][0]) == PUBLIC_STANDINGS_FIELDS
        assert snapshot["2013"][0]["tournaments"] == {"6544": 25.0, "6540": 0.0}

    def test_text_is_written_as_utf8_rather_than_escaped(self):
        text = serialize_standings_json({"2013": [_row()]}, tournament_ids=[6544, 6540])

        assert "Åsa Öberg" in text
        assert text.endswith("\n")


class TestAMalformedRowIsRefused:

    def test_an_unknown_field_is_refused_not_trimmed(self):
        with pytest.raises(ValueError, match="non-allowlisted fields: contenderId"):
            serialize_standings_json({"2013": [_row(contenderId=577097)]}, tournament_ids=[6544, 6540])

    def test_a_missing_field_is_refused(self):
        row = _row()
        del row["club"]

        with pytest.raises(ValueError, match="row 0 in '2013' is missing public fields: club"):
            serialize_standings_json({"2013": [row]}, tournament_ids=[6544, 6540])

    @pytest.mark.parametrize("overrides, message", [
        ({"member_id": True}, "member_id must be a positive integer"),
        ({"member_id": 0}, "member_id must be a positive integer"),
        ({"member_id": "577097"}, "member_id must be a positive integer"),
        ({"placement": True}, "placement must be an integer"),
        ({"placement": 1.0}, "placement must be an integer"),
        ({"birth_year": "2013"}, "birth_year must be a four-digit year"),
        ({"birth_year": 20130101}, "birth_year must be a four-digit year"),
        ({"name": None}, "name must be text"),
        ({"club": 7}, "club must be text"),
        ({"total_points": "25"}, "total_points must be numeric"),
        ({"total_points": None}, "total_points must be numeric"),
        ({"tournaments": [25.0]}, "tournaments must be an object"),
        ({"tournaments": {6544: 25.0}}, "tournament identifiers must be text"),
        ({"tournaments": {"6544": None}}, "tournament 6544 must contain only standings points"),
        ({"tournaments": {"6544": False}}, "tournament 6544 must contain only standings points"),
    ])
    def test_a_wrongly_typed_field_is_refused(self, overrides, message):
        with pytest.raises(ValueError, match=message):
            serialize_standings_json({"2013": [_row(), _row(**overrides)]}, tournament_ids=[6544, 6540])

    def test_a_band_label_that_is_not_text_is_refused(self):
        with pytest.raises(ValueError, match="age-band labels must be text"):
            serialize_standings_json({2013: [_row()]}, tournament_ids=[6544, 6540])


@pytest.mark.parametrize("ids, message", [
    ([True], "positive integers"),
    ([0], "positive integers"),
    (["6544"], "positive integers"),
    ([6544, 6544], "unique"),
])
def test_invalid_tournament_ids_are_refused(ids, message):
    with pytest.raises(ValueError, match=message):
        serialize_standings_json({}, tournament_ids=ids)


@pytest.mark.parametrize("separators, expected", [
    ({}, None),
    ({"2013": ()}, None),
    ({"2013": (8, 16), "2014": ()}, {"2013": [8, 16]}),
])
def test_optional_separators_omit_empty_groups(separators, expected):
    snapshot = json.loads(serialize_standings_json(
        {"2013": [_row()]}, tournament_ids=[6544, 6540], finals_separators=separators,
    ))

    if expected is None:
        assert "finals_separators" not in snapshot
    else:
        assert snapshot["finals_separators"] == expected
