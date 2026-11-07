"""Group-name parsing and klass eligibility.

Examples from `KLASS_NAMING.md` serve as test data. Invalid names must stop
generation and be reported together.
"""
import re
from pathlib import Path

import pytest

from jgp_standings import generation_orchestrator
from jgp_standings import cli
from jgp_standings import tournament_check
from jgp_standings.generation_orchestrator import GenerationStageError, KlassNamingError, run_generation
from jgp_standings.klass_naming import (
    KlassNameReason,
    KlassNameRefused,
    KlassNamingProblem,
    is_excluded_jgp_klass,
    is_jdm_tournament_name,
    read_klass,
)

KLASS_NAMING = Path(__file__).resolve().parent.parent / "KLASS_NAMING.md"


def _documented_rows(heading):
    """The backticked example rows of every table under one `##` heading of KLASS_NAMING.md."""
    section = KLASS_NAMING.read_text().split(f"\n## {heading}\n", 1)[1].split("\n## ", 1)[0]
    rows = []
    for line in section.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if line.startswith("| `") and len(cells) >= 2:
            rows.append(cells)
    return rows


ACCEPTED = [(row[0].strip("`"), row[1].strip("`")) for row in _documented_rows("Accepted formats")]
REFUSED = [(row[0].strip("`"), row[2].strip("`")) for row in _documented_rows("Refused names")]


class TestTheDocumentIsTheSpecification:

    def test_the_document_has_examples_of_every_level_and_every_refusal(self):
        assert len(ACCEPTED) >= 20
        assert {code for _, code in REFUSED} == {reason.value for reason in KlassNameReason}

    @pytest.mark.parametrize("name, klass", ACCEPTED)
    def test_every_accepted_example_reads_as_documented(self, name, klass):
        assert read_klass(name) == klass

    @pytest.mark.parametrize("name, code", REFUSED)
    def test_every_refused_example_is_refused_for_the_documented_reason(self, name, code):
        with pytest.raises(KlassNameRefused) as refused:
            read_klass(name)
        assert refused.value.reason == code

    def test_every_suggested_replacement_is_accepted(self):
        for row in _documented_rows("Refused names"):
            for replacement in re.findall(r"`([^`]+)`", row[3]):
                read_klass(replacement)


class TestNamesThatOnceMisreadAreReadCorrectly:
    """Each of these was read as something else by the parser this module replaced."""

    @pytest.mark.parametrize("name, klass", [
        ("Klass E Nybörjare", "e"),  # was "en": kept, and every player penalised
        ("Klass B tjej", "b"),       # was "bt"
        ("Klass A Elo", "a"),        # was "ae"
        ("Klass A - Junior", "a"),   # was "aj"
        ("JGP 2026 klass A", "a"),   # previously skipped without an error
    ])
    def test_a_word_after_the_letter_is_not_read_as_a_second_letter(self, name, klass):
        assert read_klass(name) == klass


class TestARefusalCarriesWhatAnAdapterNeeds:

    def test_the_reason_is_a_stable_code_and_the_detail_is_readable(self):
        with pytest.raises(KlassNameRefused) as refused:
            read_klass("Klass B/A")

        assert refused.value.reason is KlassNameReason.UNKNOWN_MERGE
        assert refused.value.reason == "unknown_merge"
        assert refused.value.group_name == "Klass B/A"
        assert "A and B" in refused.value.detail


class TestExclusionFromTheStandings:
    """Which klasses the ordinary JGP standings drop: E, F, G and H, unless the tournament is a JDM."""

    @pytest.mark.parametrize("klass", ["e", "f", "g", "h"])
    def test_e_and_above_are_excluded_at_an_ordinary_jgp(self, klass):
        assert is_excluded_jgp_klass(klass, is_jdm=False) is True

    @pytest.mark.parametrize("klass", ["a", "b", "ab", "c", "d"])
    def test_competing_klasses_are_never_excluded(self, klass):
        assert is_excluded_jgp_klass(klass, is_jdm=False) is False

    @pytest.mark.parametrize("klass", ["a", "b", "c", "d", "e", "f", "g", "h"])
    def test_a_jdm_tournament_drops_nothing(self, klass):
        assert is_excluded_jgp_klass(klass, is_jdm=True) is False


class TestJdmTournamentNames:

    @pytest.mark.parametrize("name", ["Junior-DM i Snabbschack", "JuniorDM 2026", "JDM Sthlm i Blixt 2026"])
    def test_markers_are_recognised(self, name):
        assert is_jdm_tournament_name(name) is True

    @pytest.mark.parametrize("name", ["Wasa JGP 2026", "jdm sthlm i blixt", "Junior DM"])
    def test_marker_matching_is_case_sensitive_and_exact(self, name):
        assert is_jdm_tournament_name(name) is False


def _tournament(tournament_id, name, group_names):
    return {
        "id": tournament_id,
        "name": name,
        "start": "2026-03-07",
        "rootClasses": [{
            "groups": [
                {"id": tournament_id * 100 + index, "name": group_name, "nrofrounds": 5}
                for index, group_name in enumerate(group_names, start=1)
            ]
        }],
    }


class TestGroupSelectionInTheStandingsPass:
    """
    The fetch step keeps a group whose klass is read and not excluded, records the tournament as
    JDM from its name, and refuses the run if any group name in any tournament is refused.
    """

    @staticmethod
    def _fetch(*tournaments):
        """A `Fetch` serving each tournament at the URL ending in its id."""
        by_id = {tournament["id"]: tournament for tournament in tournaments}
        return lambda url, stage: by_id[int(url.rsplit("/", 1)[1])]

    def test_only_competing_klasses_reach_the_standings(self):
        fetch = self._fetch(_tournament(6544, "Wasa JGP 2026", [
            "JGP Klass A", "JGP Klass B", "JGP Klass C", "JGP Klass D", "JGP Klass E", "JGP Klass F",
        ]))

        groups, jdm_tournament_ids = generation_orchestrator.fetch_tournament_info(fetch, [6544])

        assert [group["klass"] for group in groups[6544]] == ["a", "b", "c", "d"]
        assert jdm_tournament_ids == []

    def test_a_jdm_event_keeps_its_e_klass_and_is_recorded_as_jdm(self):
        fetch = self._fetch(_tournament(5628, "Junior-DM i Snabbschack 2026", [
            "Junior-DM Klass A", "Junior-DM Klass E",
        ]))

        groups, jdm_tournament_ids = generation_orchestrator.fetch_tournament_info(fetch, [5628])

        assert [group["klass"] for group in groups[5628]] == ["a", "e"]
        assert jdm_tournament_ids == [5628]

    def test_a_jdm_tournament_keeps_e_even_when_its_groups_carry_no_marker(self):
        # The tournament's name already chose the JDM bands; the groups need not repeat it.
        fetch = self._fetch(_tournament(5628, "Junior-DM i Snabbschack 2026", ["Klass D", "Klass E"]))

        groups, _ = generation_orchestrator.fetch_tournament_info(fetch, [5628])

        assert [group["klass"] for group in groups[5628]] == ["d", "e"]

    def test_a_jdm_marker_on_a_group_of_an_ordinary_jgp_keeps_nothing_extra(self):
        fetch = self._fetch(_tournament(6544, "Wasa JGP 2026", ["Klass A", "JDM Klass E"]))

        groups, jdm_tournament_ids = generation_orchestrator.fetch_tournament_info(fetch, [6544])

        assert [group["klass"] for group in groups[6544]] == ["a"]
        assert jdm_tournament_ids == []

    def test_every_refused_name_in_every_tournament_is_reported_together(self):
        fetch = self._fetch(
            _tournament(6544, "Tyresö JGP 2026", ["Klass A", "Öppen klass", "Klass B/A"]),
            _tournament(6540, "Wasa JGP 2026", ["Klass C", "KLASS D"]),
        )

        with pytest.raises(KlassNamingError) as refused:
            generation_orchestrator.fetch_tournament_info(fetch, [6544, 6540])

        assert [
            (problem.tournament_id, problem.tournament_name, problem.group_id, problem.group_name,
             problem.reason)
            for problem in refused.value.klass_problems
        ] == [
            (6544, "Tyresö JGP 2026", 654402, "Öppen klass", KlassNameReason.UNREADABLE_KLASS),
            (6544, "Tyresö JGP 2026", 654403, "Klass B/A", KlassNameReason.UNKNOWN_MERGE),
            (6540, "Wasa JGP 2026", 654002, "KLASS D", KlassNameReason.KEYWORD_SPELLING),
        ]
        assert refused.value.stage == "validation"
        assert len(refused.value.problems) == 3

    def test_a_refused_name_in_an_excluded_klass_still_refuses_the_run(self):
        # Parse the klass before checking whether to exclude the group.
        fetch = self._fetch(_tournament(6544, "Wasa JGP 2026", ["Klass A", "Klass En nybörjare"]))

        with pytest.raises(KlassNamingError):
            generation_orchestrator.fetch_tournament_info(fetch, [6544])


class TestTheRefusalReachesTheOperatorUnwrapped:

    @staticmethod
    def _refusal():
        return KlassNamingError([KlassNamingProblem(
            6685, "Wasa JGP 2026", 31877, "Klass Junior", KlassNameReason.UNREADABLE_KLASS,
            "'Klass' must be followed by a space and one capital klass letter, A to H, standing on its own",
        )])

    def test_run_generation_raises_the_klass_naming_error_itself(self, monkeypatch):
        refusal = self._refusal()

        def refuse(*_args, **_kwargs):
            raise refusal

        monkeypatch.setattr(generation_orchestrator, "_fetch_inputs", refuse)

        with pytest.raises(KlassNamingError) as raised:
            run_generation(object(), fetch=None, run_record_path=Path("unused"), today=None,
                           override_tournament_ids=[6685])
        assert raised.value is refusal
        assert isinstance(raised.value, GenerationStageError)

    def test_the_cli_lists_every_refused_group_and_exits_with_status_one(self, monkeypatch, capsys):
        refusal = self._refusal()

        def refuse(*_args, **_kwargs):
            raise refusal

        monkeypatch.setattr(cli, "load_configuration", lambda state_directory: None)
        monkeypatch.setattr(cli, "generate_and_publish", refuse)

        with pytest.raises(SystemExit) as exited:
            cli.app(["generate"], standalone_mode=False)

        assert exited.value.code == 1
        stderr = capsys.readouterr().err
        assert "1 group name does not follow the klass naming rules in KLASS_NAMING.md." in stderr
        assert "Wasa JGP 2026 (6685), group 'Klass Junior' (id 31877)" in stderr


class TestTheValidationUtilityUsesTheSameRules:
    """The single-tournament check shares the name parser and includes klass E and F."""

    def test_e_and_f_groups_are_reported_on(self):
        groups = tournament_check.extract_groups(_tournament(6544, "Wasa JGP 2026", ["Klass E", "Klass F"]))
        assert [group["klass"] for group in groups] == ["e", "f"]

    def test_g_and_h_groups_are_dropped(self):
        groups = tournament_check.extract_groups(_tournament(6544, "Wasa JGP 2026", ["Klass G", "Klass H"]))
        assert groups == []

    def test_g_and_h_are_kept_for_a_jdm_tournament(self):
        groups = tournament_check.extract_groups(_tournament(5628, "Junior-DM 2026", ["Klass G", "Klass H"]))
        assert [group["klass"] for group in groups] == ["g", "h"]

    def test_a_refused_name_refuses_the_check_with_every_problem(self):
        with pytest.raises(KlassNamingError) as refused:
            tournament_check.extract_groups(
                _tournament(6544, "Wasa JGP 2026", ["Klass A", "Wasa JGP 2026", "Öppen Klass för alla"])
            )

        assert [problem.reason for problem in refused.value.klass_problems] == [
            KlassNameReason.NO_KLASS, KlassNameReason.UNREADABLE_KLASS,
        ]
