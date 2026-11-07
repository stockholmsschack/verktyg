"""Frozen event replay plus boundary cases for the 7132 policy."""
from copy import deepcopy
from dataclasses import replace
from datetime import date
import json
from pathlib import Path

import polars as pl
import pytest

from jgp_standings.active_configuration import load_configuration
from jgp_standings.generation_core import collate_standings, produce_artifacts
from jgp_standings.generation_orchestrator import GenerationStageError, fetch_tournament_info, run_generation
from jgp_standings.points import assign_standings_points
from jgp_standings.tournament_check import check_tournament
from jgp_standings.tournament_exceptions import policy_for
from jgp_standings.tournament_exceptions import trojanska_7132 as policy
from jgp_standings.tournament_exceptions.audit import BASE
from jgp_standings.upstream_client import FixtureReplay, _canonical_tournament

FIXTURES = Path(__file__).resolve().parents[1] / 'fixtures' / 'trojanska-7132'
METADATA = f'{BASE}/tournament/tournament/id/7132'


@pytest.fixture
def inputs():
    return json.loads((FIXTURES / 'responses.json').read_text())


def generate(inputs=None, configuration=None):
    fetch = FixtureReplay(FIXTURES) if inputs is None else lambda url, stage: deepcopy(inputs[url])
    return run_generation(
        configuration or load_configuration(FIXTURES), fetch=fetch,
        run_record_path=None, today=date(2026, 10, 30),
    )


@pytest.fixture(scope='module')
def result():
    return generate()


def awards(result):
    return {r['contenderId']: r for r in result.standings.award_breakdown}


def test_admission_generation_and_check_share_policy(inputs, tmp_path, monkeypatch, capsys):
    admitted = _canonical_tournament(7132, inputs[METADATA], 'test')
    def fetch(url, stage):
        return deepcopy(inputs[url])

    groups, _ = fetch_tournament_info(fetch, [7132])
    assert {g.group_id for g in admitted.processable_groups} == policy.INCLUDED
    assert {g['group_id'] for g in groups[7132]} == policy.INCLUDED
    monkeypatch.chdir(tmp_path)
    check_tournament(7132, load_configuration(FIXTURES).dispensation_dict, fetch=fetch)
    output = capsys.readouterr().out
    assert policy.VERSION in output
    assert 'Reyansh Gore' in output
    assert 'Hugo Hardwick (born' not in output
    assert 'Rishi Viswanath (born' not in output
    assert '2014-2016' in output


def test_named_awards_and_dispensations_keep_played_group(result):
    rows = awards(result)
    for cid, expected, gid in ((685669, 25, 19203), (609818, 25, 19204),
                               (590088, 22, 19203), (570261, 22, 19204), (577097, 12, 19204)):
        assert rows[cid]['awarded_points'] == expected
        assert rows[cid]['source_group_id'] == gid
        assert rows[cid]['annual_band'] == '2010-2012'
        assert rows[cid]['reason'] == ''


def test_other_bands_use_one_normal_ladder(result):
    for label, frame in result.standings.bands.items():
        if label == '2010-2012':
            continue
        eligible = frame.filter(pl.col('STD 1') > 0)
        expected = assign_standings_points(eligible, 'place_1', 'p_1', 'kvp_1', 'expected', 'contenderId')
        assert expected['expected'].to_list() == expected['STD 1'].to_list()
    youngest = result.standings.bands['2017']
    assert youngest.filter(pl.col('STD 1') == 25).height == 1
    assert awards(result)[691958]['awarded_points'] == 0


def test_one_bounded_contribution_and_no_internal_fields(result):
    public = json.loads(result.artifacts.standings_json)
    assert public['tournament_ids'] == [7132]
    for band in public['standings'].values():
        for row in band:
            assert set(row['tournaments']) == {'7132'}
            assert 0 <= row['total_points'] <= 25
    for frame in result.standings.bands.values():
        assert frame['played'].to_list() == [1] * frame.height
        assert not any(c.startswith(('award_', 'source_')) for c in frame.columns)
    assert result.standings.tournaments[0].starters == 163


def test_audit_freezes_inputs_configuration_and_raw_scores(result, inputs):
    reports = result.artifacts.diagnostic_reports
    audit = json.loads(reports['trojanska-7132-awards.json'])
    capture = json.loads(reports['calculation-inputs.json'])
    assert capture['responses'] == inputs
    assert capture['manifest']['policy_version'] == policy.VERSION
    assert capture['manifest']['source_sha256']['tournament_exceptions/trojanska_7132.py']
    assert capture['manifest']['configuration']['dispensation_dict']['570261'] == 2010
    assert len(audit['entries']) == sum(len(inputs[f'{BASE}/tournamentresults/table/id/{gid}']) for gid in policy.GROUPS)
    reasons = {row['reason'] for row in audit['entries']}
    assert {'excluded_group', 'no_played_games', 'outside_district', 'wrong_group', ''} <= reasons
    row = awards(result)[691958]
    raw = next(p for p in inputs[f'{BASE}/tournamentresults/table/id/19205'] if p['contenderId'] == 691958)
    assert (row['points'], row['secPoints']) == (raw['points'], raw['secPoints'])


def test_explicit_penalty_overrides_named_exception_without_consuming_slot(inputs):
    config = replace(load_configuration(FIXTURES), penalty_dict={7132: (570261,)})
    result = generate(inputs, config)
    assert awards(result)[570261]['awarded_points'] == 0
    assert awards(result)[570261]['reason'] == 'explicit_penalty'
    assert awards(result)[577097]['awarded_points'] == 14


def test_only_named_players_get_eligibility_exception(inputs):
    player = deepcopy(inputs[f'{BASE}/tournamentresults/table/id/19204'][0])
    player.update(source_group_id=19204, klass='b')
    player['contenderId'] = 999999
    assert policy.eligibility(player, {999999: 2010})[0] is False
    for cid in policy.ELIGIBILITY_EXCEPTIONS:
        player['contenderId'] = cid
        assert policy.eligibility(player, {cid: 2010})[0] is True
        player['source_group_id'] = 19205
        assert policy.eligibility(player, {cid: 2010})[0] is False
        player['source_group_id'] = 19204


def test_zero_score_penalty_is_still_zero(inputs):
    player = deepcopy(inputs[f'{BASE}/tournamentresults/table/id/19204'][0])
    player.update(source_group_id=19204, klass='b', points=0, secPoints=0)
    policy.validate_players([player], {}, [player['contenderId']])
    supplied, _ = policy.allocate([player], {})
    assert supplied[player['contenderId']] == 0


def test_excluded_transfer_cannot_replace_included_result(inputs, result):
    transferred = deepcopy(inputs[f'{BASE}/tournamentresults/table/id/19203'][0])
    transferred['secPoints'] = -999
    inputs[f'{BASE}/tournamentresults/table/id/19207'].append(transferred)
    assert awards(generate(inputs)) == awards(result)


def test_duplicate_included_players_fail(inputs):
    duplicate = deepcopy(inputs[f'{BASE}/tournamentresults/table/id/19203'][0])
    inputs[f'{BASE}/tournamentresults/table/id/19204'].append(duplicate)
    inputs[f'{BASE}/tournamentresults/roundresults/id/19204'][0]['games'].append(
        {'whiteId': duplicate['contenderId'], 'blackId': 570261, 'result': 0}
    )
    with pytest.raises(GenerationStageError, match='duplicate player IDs'):
        generate(inputs)


def test_wo_only_transfer_gets_no_elit_start_or_award(inputs, result):
    gid = 19205
    player = deepcopy(inputs[f'{BASE}/tournamentresults/table/id/19207'][0])
    player['contenderId'] = 999999
    player.update(wonGames=1, drawGames=0, lostGames=0, points=1)
    inputs[f'{BASE}/tournamentresults/table/id/{gid}'].append(player)
    inputs[f'{BASE}/tournamentresults/roundresults/id/{gid}'][0]['games'].append(
        {'whiteId': 999999, 'blackId': 763653, 'result': 2}
    )
    changed = generate(inputs)
    assert changed.standings.tournaments[0].starters == result.standings.tournaments[0].starters
    assert awards(changed) == awards(result)
    rows = json.loads(changed.artifacts.diagnostic_reports['trojanska-7132-awards.json'])['entries']
    assert next(r for r in rows if r['contenderId'] == 999999)['reason'] == 'no_played_games'


@pytest.mark.parametrize('change', ['missing', 'extra', 'name', 'rounds', 'points', 'season', 'roots'])
def test_changed_structure_fails(inputs, change):
    data = inputs[METADATA]
    groups = data['rootClasses'][0]['groups']
    if change == 'missing':
        groups.pop()
    elif change == 'extra':
        groups.append(dict(groups[0], id=99999))
    elif change == 'name':
        groups[0]['name'] = 'Klass A'
    elif change == 'rounds':
        groups[0]['nrofrounds'] = 9
    elif change == 'points':
        groups[0]['pointSystem'] = 1
    elif change == 'season':
        data['start'] = '2027-10-24'
    else:
        data['rootClasses'].append({'groups': []})
    with pytest.raises(ValueError, match='7132'):
        policy.select_groups(data)


def test_supplied_awards_follow_date_order(result):
    exceptional = result.standings.tournaments[0]
    ordinary = replace(exceptional, tournament_id=9999, date='20260101')
    configuration = load_configuration(FIXTURES)
    standings = collate_standings(
        run_input=replace(result.standings.run_input, tournament_ids=(7132, 9999)),
        tournaments=(exceptional, ordinary), player_list={},
        dispensation_dict=configuration.dispensation_dict, diagnostics=result.standings.diagnostics,
    )
    artifacts = produce_artifacts(standings, contributor=configuration.contributor, landscape=False)
    public = json.loads(artifacts.standings_json)
    assert public['tournament_ids'] == [9999, 7132]
    row = next(r for r in public['standings']['2010-2012'] if r['member_id'] == 685669)
    assert row['tournaments'] == {'9999': 20, '7132': 25}
    assert standings.bands['2010-2012']['played'].to_list() == [2] * 32
    headers = [line for line in artifacts.typst_source.splitlines() if line.startswith('table.header([*')]
    assert headers
    assert all(line == r'table.header([*\#*], [*Född*], [*Namn*], [*Klubb*], [*1*], [*2*], [*Σ*]),' for line in headers)


def test_historical_season_is_verified_even_with_supplied_run_record(result):
    with pytest.raises(ValueError, match='requires season 2026'):
        collate_standings(
            run_input=replace(result.standings.run_input, effective_year=2027),
            tournaments=result.standings.tournaments, player_list={}, dispensation_dict={},
            diagnostics=result.standings.diagnostics,
        )
    assert policy_for(9999) is None


def test_district_rating_membership_and_exclusion_precede_allocation(inputs, result):
    table = inputs[f'{BASE}/tournamentresults/table/id/19203']
    dananjey = next(p for p in table if p['contenderId'] == 685669)
    dananjey['playerInfo']['clubId'] = 999999
    rating_url = f'{BASE}/ratinglist/district/5821/date/2026-10-24/ratingtype/1/category/0'
    inputs[rating_url].append({'id': 685669})
    assert awards(generate(inputs))[685669]['awarded_points'] == 25
    inputs[rating_url] = [p for p in inputs[rating_url] if p['id'] != 685669]
    changed = generate(inputs)
    assert 685669 not in awards(changed)
    assert awards(changed)[590088]['awarded_points'] == 25
    assert changed.standings.tournaments[0].starters == result.standings.tournaments[0].starters


def test_no_game_registration_consumes_no_start_or_slot(inputs, result):
    row = deepcopy(inputs[f'{BASE}/tournamentresults/table/id/19203'][0])
    row.update(contenderId=999999, wonGames=0, drawGames=0, lostGames=0)
    inputs[f'{BASE}/tournamentresults/table/id/19203'].append(row)
    changed = generate(inputs)
    assert awards(changed) == awards(result)
    assert changed.standings.tournaments[0].starters == result.standings.tournaments[0].starters


def test_event_age_ranges_apply_at_boundaries(inputs):
    row = deepcopy(inputs[f'{BASE}/tournamentresults/table/id/19206'][0])
    row['source_group_id'] = 19206
    for year, valid in ((2016, False), (2017, True), (2019, True), (2020, False)):
        row['playerInfo']['birthdate'] = str(year)
        assert policy.eligibility(row, {})[0] == valid


def test_partial_and_full_publication_include_downloadable_audit(tmp_path):
    from jgp_standings.artifact_publication import generate_and_publish, read_public_snapshot
    from jgp_standings.web.available_files import list_report_files

    config = load_configuration(FIXTURES)
    args = dict(fetch=FixtureReplay(FIXTURES), state_directory=tmp_path / 'state', today=date(2026, 10, 30))
    partial = generate_and_publish(
        config, tmp_path / 'output', override_tournament_ids=[7132], run_id='preview', **args,
    )
    assert read_public_snapshot(tmp_path / 'output') is None
    assert partial.pdf_path.read_bytes().startswith(b'%PDF-')
    full = generate_and_publish(config, tmp_path / 'output', run_id='full', **args)
    assert read_public_snapshot(tmp_path / 'output') == full.result.artifacts.standings_json.encode()
    reports = list_report_files(tmp_path / 'output')
    for key in ('trojanska_awards', 'calculation_inputs'):
        matches = [r for r in reports if r.label_key == f'available_files.names.{key}']
        assert len(matches) == 2
        assert {r.partial for r in matches} == {True, False}
    capture = next((full.run_directory / 'diagnostics').glob('calculation-inputs-*.json'))
    responses = json.loads(capture.read_text())['responses']
    replayed = generate(responses)
    assert awards(replayed) == awards(full.result)


def test_historical_regeneration_reuses_event_policy():
    regenerated = run_generation(
        load_configuration(FIXTURES), fetch=FixtureReplay(FIXTURES), run_record_path=None,
        today=date(2028, 1, 1), override_year=2026, override_tournament_ids=[7132],
    )
    assert awards(regenerated)[570261]['awarded_points'] == 22


def test_empty_exceptional_tournament_does_not_allocate_to_absent_players(result):
    exceptional = replace(result.standings.tournaments[0], entries=(), starters=0)
    ordinary = replace(result.standings.tournaments[0], tournament_id=9999, date='20260101')
    standings = collate_standings(
        run_input=replace(result.standings.run_input, tournament_ids=(7132, 9999)),
        tournaments=(exceptional, ordinary), player_list={},
        dispensation_dict=load_configuration(FIXTURES).dispensation_dict,
        diagnostics=result.standings.diagnostics,
    )
    for frame in standings.bands.values():
        assert frame['STD 2'].null_count() == frame.height
        assert frame['played'].to_list() == [1] * frame.height


def test_checker_reports_changed_structure_as_actionable_error(inputs):
    from jgp_standings.tournament_check import TournamentCheckError, extract_groups

    inputs[METADATA]['rootClasses'] = []
    with pytest.raises(TournamentCheckError, match='expected one root class'):
        extract_groups(inputs[METADATA])
    with pytest.raises(ValueError, match='expected one root class'):
        fetch_tournament_info(lambda url, stage: inputs[url], [7132])
