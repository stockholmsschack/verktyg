"""Configuration validation, revision checks and atomic persistence.

Saves preserve manual penalties and store verified dispensation identities.
Imports must work without configuration files.
"""
from dataclasses import replace
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from jgp_standings.active_configuration import (
    ACTIVE_CONFIGURATION_FILENAME,
    ActiveConfiguration,
    ConfigurationValidationError,
    DispensationPlayer,
    StaleConfigurationError,
    load_configuration,
    save_configuration,
)

ROOT = Path(__file__).resolve().parent.parent


def _configuration(**changes) -> ActiveConfiguration:
    configuration = ActiveConfiguration(
        tournament_ids=(6544, 6540),
        contributor={"name": "Test Contributor", "email": "test@example.com"},
        landscape=True,
        dispensation_dict={546393: 2009, 726286: 2009},
        dispensation_players={
            546393: DispensationPlayer("Pratyush Tripathi", 2010, "SS 4 Springare"),
            726286: DispensationPlayer("Igor Markov", 2010, ""),
        },
        penalty_dict={5429: (727750, 609818)},
        club_display_names={"Kista SC KFUM Schacksektion": "Kista SC KFUM"},
    )
    return replace(configuration, **changes)


def _write(state_directory: Path, document: str) -> None:
    state_directory.mkdir(parents=True, exist_ok=True)
    (state_directory / ACTIVE_CONFIGURATION_FILENAME).write_text(document)


class TestImportPerformsNoIO:

    @pytest.mark.parametrize("module", [
        "jgp_standings", "jgp_standings.active_configuration", "jgp_standings.generation_core",
        "jgp_standings.generation_orchestrator", "jgp_standings.artifact_publication", "jgp_standings.cli",
        "jgp_standings.tournament_check", "jgp_standings.dispensation_entry", "jgp_standings.web",
    ])
    def test_importing_in_an_empty_directory_succeeds_and_creates_nothing(self, module, tmp_path):
        # Use a fresh interpreter to execute the module body on import.
        # Check that the installed package can be imported from any directory.
        result = subprocess.run(
            [sys.executable, "-c", f"import {module}"],
            cwd=tmp_path, capture_output=True, text=True,
        )

        assert result.returncode == 0, result.stderr
        assert list(tmp_path.iterdir()) == []


class TestTheCommittedConfiguration:

    def test_it_loads_from_the_state_directory_with_integer_keyed_lookups(self):
        configuration = load_configuration(ROOT / "state")

        assert configuration.revision >= 1
        assert configuration.tournament_ids == (6544, 6540, 6534, 6685, 6826, 6853)
        assert configuration.landscape is False
        assert len(configuration.dispensation_dict) == 15
        assert configuration.dispensation_dict[546393] == 2009
        assert all(isinstance(key, int) for key in configuration.dispensation_dict)
        assert configuration.dispensation_players.keys() == configuration.dispensation_dict.keys()
        assert configuration.dispensation_players[546393] == DispensationPlayer(
            "Pratyush Tripathi", 2010, "SS 4 Springare"
        )
        assert configuration.penalty_dict == {}
        assert configuration.club_display_names == {"Kista SC KFUM Schacksektion": "Kista SC KFUM"}

    def test_it_is_exactly_what_the_writer_produces_apart_from_the_revision(self, tmp_path):
        committed = ROOT / "state" / ACTIVE_CONFIGURATION_FILENAME
        save_configuration(tmp_path, replace(load_configuration(ROOT / "state"), revision=0))

        def without_revision(document: str) -> list[str]:
            return [line for line in document.splitlines() if not line.startswith("revision = ")]

        written = (tmp_path / ACTIVE_CONFIGURATION_FILENAME).read_text()
        assert without_revision(written) == without_revision(committed.read_text())

    def test_the_old_configuration_file_is_gone(self):
        assert not (ROOT / "jgp_config.toml").exists()
        assert not (ROOT / "jgp_config.py").exists()


class TestSaving:

    def test_every_persisted_setting_round_trips(self, tmp_path):
        state_directory = tmp_path / "state"

        saved = save_configuration(state_directory, _configuration())
        loaded = load_configuration(state_directory)

        assert saved == loaded
        assert loaded.revision == 1
        assert loaded.penalty_dict == {5429: (727750, 609818)}
        document = (state_directory / ACTIVE_CONFIGURATION_FILENAME).read_text()
        assert "tournament_ids = [6544, 6540]" in document
        assert "landscape = true" in document
        assert '"546393" = 2009' in document
        assert '"5429" = [727750, 609818]' in document
        assert '[club_display_names]\n"Kista SC KFUM Schacksektion" = "Kista SC KFUM"\n' in document

    def test_a_stale_save_cannot_overwrite_a_newer_configuration(self, tmp_path):
        first_view = save_configuration(tmp_path, _configuration())
        second_view = load_configuration(tmp_path)
        updated = save_configuration(tmp_path, replace(first_view, tournament_ids=(6685,)))

        with pytest.raises(StaleConfigurationError, match="revision 1 is stale") as raised:
            save_configuration(tmp_path, replace(second_view, tournament_ids=(6826,)))

        assert raised.value.current_revision == 2
        assert updated.revision == 2
        assert load_configuration(tmp_path).tournament_ids == (6685,)

    def test_an_explicit_expected_revision_is_checked_instead(self, tmp_path):
        saved = save_configuration(tmp_path, _configuration())

        with pytest.raises(StaleConfigurationError):
            save_configuration(tmp_path, saved, expected_revision=0)

    def test_an_interrupted_replacement_leaves_the_previous_document_readable(self, tmp_path):
        original = save_configuration(tmp_path, _configuration())

        with patch("jgp_standings.active_configuration.os.replace", side_effect=OSError("interrupted")):
            with pytest.raises(OSError, match="interrupted"):
                save_configuration(tmp_path, replace(original, tournament_ids=(6685,)))

        assert load_configuration(tmp_path) == original
        assert not any(path.name.startswith(f".{ACTIVE_CONFIGURATION_FILENAME}.") for path in tmp_path.iterdir())

    def test_validation_collects_every_problem_before_writing(self, tmp_path):
        invalid = _configuration(
            tournament_ids=(0, 0),
            contributor={"name": "", "email": ""},
            landscape="yes",
            dispensation_dict={546393: "2009"},
            penalty_dict={5429: (0,)},
            club_display_names={"": "Kista", "Wasa SK": " "},
        )
        state_directory = tmp_path / "state"

        with pytest.raises(ConfigurationValidationError) as raised:
            save_configuration(state_directory, invalid)

        assert raised.value.problems == (
            "tournament ids must be positive integers",
            "landscape must be true or false",
            "contributor name is required",
            "contributor email is required",
            "club display aliases require a canonical club name",
            "club display alias 'Wasa SK' requires a display name",
            "dispensation for 546393 must be a birth year, not '2009'",
            "dispensation player 726286 has no dispensation",
            "penalties for tournament 5429 must be a list of player ids",
        )
        assert not state_directory.exists()

    def test_every_dispensation_has_exactly_one_verified_player(self, tmp_path):
        invalid = _configuration(
            dispensation_dict={546393: 2009, 541890: 2009},
            dispensation_players={
                546393: DispensationPlayer(" ", 2010, "SS 4 Springare"),
                726286: DispensationPlayer("Igor Markov", 2010, ""),
            },
        )

        with pytest.raises(ConfigurationValidationError) as raised:
            save_configuration(tmp_path, invalid)

        assert raised.value.problems == (
            "dispensation for 541890 has no verified player",
            "dispensation player 546393 needs a name, a birth year and a club",
            "dispensation player 726286 has no dispensation",
        )

    def test_an_empty_tournament_list_is_never_saved(self, tmp_path):
        with pytest.raises(ConfigurationValidationError, match="at least one tournament id is required"):
            save_configuration(tmp_path, _configuration(tournament_ids=()))

    def test_saving_another_field_writes_hand_edited_penalties_back_unchanged(self, tmp_path):
        saved = save_configuration(tmp_path, _configuration())
        path = tmp_path / ACTIVE_CONFIGURATION_FILENAME
        # The operator adds a penalty by hand; that does not advance the revision.
        path.write_text(path.read_text() + '"6544" = [541890]\n')

        current = load_configuration(tmp_path)
        assert current.revision == saved.revision
        save_configuration(tmp_path, replace(current, landscape=False))

        assert load_configuration(tmp_path).penalty_dict == {5429: (727750, 609818), 6544: (541890,)}


class TestLoadingReportsEveryProblem:

    def test_every_problem_is_reported_together(self, tmp_path):
        _write(tmp_path, "\n".join([
            "tournament_ids = [6544, 6544]",
            'landscape = "yes"',
            "known_local_players = {}",
            "dispensation_dict_2025 = {}",
            "[contributor]",
            'name = ""',
            'phone = "1"',
            "[dispensation_dict]",
            '"abc" = 2009',
            '"546393" = "2009"',
            '"726286" = 2009',
            '[dispensation_players."726286"]',
            'name = "Igor Markov"',
            "birth_year = 2010",
            '[dispensation_players."x"]',
            '[dispensation_players."546393"]',
            'name = "Pratyush Tripathi"',
            "[penalty_dict]",
            '"5429" = 727750',
        ]) + "\n")

        with pytest.raises(ConfigurationValidationError) as raised:
            load_configuration(tmp_path)

        assert raised.value.problems == (
            "unknown active configuration fields: dispensation_dict_2025, known_local_players",
            "stored configuration revision is required",
            "unknown contributor fields: phone",
            "dispensation player id 'abc' must be a positive integer",
            "dispensation player 726286 needs exactly a name, a birth year and a club",
            "dispensation player id 'x' must be a positive integer",
            "dispensation player 546393 needs exactly a name, a birth year and a club",
            "penalties for tournament 5429 must be a list of player ids",
            "tournament ids must be unique",
            "landscape must be true or false",
            "contributor name is required",
            "contributor email is required",
            "dispensation for 546393 must be a birth year, not '2009'",
            "dispensation for 726286 has no verified player",
        )

    def test_the_old_keys_are_refused(self, tmp_path):
        _write(tmp_path, "revision = 1\nJGP_tournaments = [6544]\ntournament_year = 2026\n")

        with pytest.raises(ConfigurationValidationError) as raised:
            load_configuration(tmp_path)

        assert "JGP_tournaments" in str(raised.value)
        assert "tournament_year" in str(raised.value)

    def test_a_missing_document_is_a_validation_error(self, tmp_path):
        with pytest.raises(ConfigurationValidationError, match="could not read active configuration"):
            load_configuration(tmp_path)

    @pytest.mark.parametrize("tournament_ids", ["[0]", "[-1]", '["6544"]', "[true]"])
    def test_tournament_ids_must_be_positive_integers(self, tmp_path, tournament_ids):
        _write(tmp_path, f"revision = 1\ntournament_ids = {tournament_ids}\n"
                         '[contributor]\nname = "N"\nemail = "n@example.com"\n')

        with pytest.raises(ConfigurationValidationError, match="positive integers"):
            load_configuration(tmp_path)

    def test_the_value_cannot_be_changed_after_loading(self):
        configuration = load_configuration(ROOT / "state")

        with pytest.raises(AttributeError):
            configuration.landscape = True
