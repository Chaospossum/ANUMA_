"""Lock the Artemis note and the results page to cited inputs and job 5332991."""

from __future__ import annotations

from anuma.artemis import validation_markdown
from anuma.config import repo_root


def test_artemis_validation_note():
    report = validation_markdown()
    path = repo_root() / "out" / "artemis-validation.md"
    assert path.read_text() == report
    assert "Connecting Ridge | 0.40 |" in report or "| Connecting Ridge | 0.40 |" in report
    assert "0.40 km" in report
    assert "job 5332991" in report
    assert "coordinates not reported with job 5332991" in report
    assert "geometry stays empty" in report or "| null |" in report
    assert "No radius is assigned" in report


def test_results_page_marks_planned_work_and_keeps_measured_facts():
    text = (repo_root() / "docs" / "results.md").read_text()
    report = validation_markdown()
    assert "0.40 km" in report
    assert "0.40 km" in text
    for fact in (
        "5332991",
        "500 weight draws",
        "89.45°S",
        "137.3°W",
        "0.93",
        "99.8%",
        "83.66°S",
        "500/500",
        "LROC",
        "LOLA",
        "Lunar Prospector",
        "mission case is still open",
        "not affiliated",
        "Orbital debris affects trajectories, not site choice, so it is not modelled.",
        "no default plume radius",
    ):
        assert fact in text
    lowered = text.lower()
    for banned in ("official", "certified", "approved", "ispace-affiliated"):
        assert banned not in lowered
    assert "planned" in lowered
