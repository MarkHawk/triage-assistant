from pathlib import Path

import pytest

from triage.config import load_config
from triage.scenarios import load_scenarios, parse_scenario
from helpers import lab_dir

SAMPLE = '''# shellcheck shell=bash disable=SC2034
# a comment
SCENARIO_SUMMARY="NO_PROXY sends calls around the proxy"
SCENARIO_RUNBOOK="runbooks/14-proxy-bypass.md"
SCENARIO_APP=oidc-egress
SCENARIO_UNAFFECTED="oidc alice"
SCENARIO_EXPECT="Failed to resolve 'keycloak'"
SCENARIO_LOG='oidc-rp-egress event=oidc_login_failed .*NameResolutionError'

scenario_detect() { :; }
'''


def test_parse_sample(tmp_path):
    f = tmp_path / "proxy-bypass.sh"
    f.write_text(SAMPLE)
    s = parse_scenario(f)
    assert s.name == "proxy-bypass"
    assert s.runbook_number == 14
    assert s.app == "oidc-egress"
    assert s.expect == "Failed to resolve 'keycloak'"
    assert s.log_service == "oidc-rp-egress"
    assert s.log_pattern == "event=oidc_login_failed .*NameResolutionError"
    assert s.unaffected == "oidc alice"


def test_missing_field_is_an_error(tmp_path):
    f = tmp_path / "x.sh"
    f.write_text('SCENARIO_SUMMARY="s"\n')
    with pytest.raises(ValueError):
        parse_scenario(f)


def test_every_real_scenario_parses_and_has_a_runbook():
    lab = lab_dir()
    if not (lab / "scenarios").is_dir():
        pytest.skip("identity-lab not found at LAB_DIR")
    scenarios = load_scenarios(lab)
    assert len(scenarios) >= 21
    for s in scenarios.values():
        assert Path(lab, s.runbook).is_file(), s.name


def test_silent_log_is_a_first_class_state(tmp_path):
    f = tmp_path / "quiet.sh"
    f.write_text(SAMPLE.replace("SCENARIO_LOG=", "SCENARIO_LOG_ABSENT="))
    s = parse_scenario(f)
    assert s.log_absent and s.log_service == "oidc-rp-egress"
    assert s.log_evidence.startswith("silence in oidc-rp-egress: no line matching")
    f.write_text(SAMPLE)
    loud = parse_scenario(f)
    assert not loud.log_absent and loud.log_evidence.startswith("a line in oidc-rp-egress matching")


def test_every_real_scenario_has_a_service_and_a_pattern_that_compiles():
    import re

    lab = lab_dir()
    if not (lab / "scenarios").is_dir():
        pytest.skip("identity-lab not found at LAB_DIR")
    for s in load_scenarios(lab).values():
        # a scenario proves itself by a log line, or by the absence of one
        assert s.log_service and s.log_pattern, s.name
        re.compile(s.log_pattern)
