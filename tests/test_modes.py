# tests/test_modes.py
#
# PURPOSE: Check that every mode is fully defined and that the system prompt is built
# correctly. These tests protect you from a classic mistake: adding a new mode but
# forgetting one of its fields, which would show up as a blank button in the UI.

import pytest

from app.modes import DEFAULT_MODE, MODES, build_system_prompt, get_mode
from app.prompts import SYSTEM_PROMPT


def test_expected_modes_exist_in_order():
    # `list(MODES)` gives the mode ids in the order the buttons will appear.
    assert list(MODES) == ["learn", "analyze", "pentest", "defend", "report"]


def test_default_mode_is_a_real_mode():
    assert DEFAULT_MODE in MODES


# Runs once for EACH mode id, so a problem names the exact mode that is broken.
@pytest.mark.parametrize("mode_id", list(MODES))
def test_every_mode_is_fully_defined(mode_id):
    mode = get_mode(mode_id)

    assert mode.id == mode_id
    assert mode.label
    assert mode.tagline
    assert mode.placeholder
    assert mode.instructions
    assert len(mode.suggestions) >= 3


@pytest.mark.parametrize("mode_id", list(MODES))
def test_system_prompt_has_base_rules_first_then_mode_instructions(mode_id):
    prompt = build_system_prompt(mode_id)

    # The base safety rules must come FIRST in every mode, so no mode can drop them.
    assert prompt.startswith(SYSTEM_PROMPT)
    assert get_mode(mode_id).instructions in prompt


def test_unknown_mode_raises_a_clear_error():
    with pytest.raises(ValueError, match="Unknown mode"):
        get_mode("hacker")


def test_pentest_mode_keeps_the_authorization_and_no_exploit_rules():
    text = get_mode("pentest").instructions.lower()

    assert "authorized" in text
    assert "exploit" in text  # the mode tells the AI NOT to write exploit code


def test_analyze_mode_demands_evidence_structure():
    text = get_mode("analyze").instructions

    for heading in ("Observations", "Unknowns", "Confidence"):
        assert heading in text


def test_report_mode_forbids_inventing_details():
    text = get_mode("report").instructions

    assert "Not provided" in text
    assert "Never invent" in text