from __future__ import annotations

import json
from pathlib import Path

from worksheet_generator.color_grid_generation import (
    ColorGridGenerationError,
    PRESET_PICTURE_OPTIONS,
    PresetColorGridGenerator,
    difficulty_to_grid_size,
)
from worksheet_generator.webapp.generation_service import WorksheetRunGenerationService


SNAPSHOT_PATH = Path(__file__).resolve().parent / "snapshots" / "preset_visual_metrics.json"


def _preset_visual_metrics() -> dict[str, object]:
    service = WorksheetRunGenerationService(artifact_root=Path("/tmp/preset-visual-metrics"))
    generator = PresetColorGridGenerator()
    metrics: dict[str, object] = {}
    for option in PRESET_PICTURE_OPTIONS:
        preset_name = option["value"]
        palette = service._palette_entries_for_picture(source="preset", preset_name=preset_name, color_count=8)
        labels = [label for label, _ in palette]
        background_label = labels[0]
        grid = generator.generate(
            preset_name=preset_name,
            grid_size=max(difficulty_to_grid_size(3), service._minimum_grid_size_for_preset(preset_name)),
            palette_labels=labels,
        )
        coords = [(row_index, column_index) for row_index, row in enumerate(grid.cells) for column_index, value in enumerate(row) if value != background_label]
        counts: dict[str, int] = {}
        for row in grid.cells:
            for value in row:
                counts[value] = counts.get(value, 0) + 1
        rows = [row_index for row_index, _ in coords]
        cols = [column_index for _, column_index in coords]
        metrics[preset_name] = {
            "grid_size": grid.grid_size,
            "background_label": background_label,
            "used_counts": {label: count for label, count in counts.items() if label != background_label and count > 0},
            "non_background_count": len(coords),
            "bbox": {
                "top": min(rows),
                "bottom": max(rows),
                "left": min(cols),
                "right": max(cols),
            },
        }
    return metrics


def test_preset_visual_metrics_match_snapshot() -> None:
    expected = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    assert _preset_visual_metrics() == expected


def test_critical_presets_keep_expected_semantic_colors() -> None:
    metrics = _preset_visual_metrics()

    assert metrics["flower"]["used_counts"]["Stem Green"] > 0
    assert metrics["flower"]["used_counts"]["Center Yellow"] > 0
    assert metrics["apple"]["used_counts"]["Stem Brown"] > 0
    assert metrics["apple"]["used_counts"]["Leaf Green"] > 0
    assert metrics["tree"]["used_counts"]["Trunk Brown"] > 0
    assert metrics["christmas_tree"]["used_counts"]["Trunk Brown"] > 0
    assert metrics["rocket"]["used_counts"]["Flame Gold"] > 0
    assert metrics["rocket"]["used_counts"]["Flame Orange"] > 0
    assert metrics["blue_tang"]["used_counts"]["Tail Yellow"] > 0
    assert metrics["clown_fish"]["used_counts"]["Stripe White"] > 0


def test_gemini_color_picture_falls_back_to_supported_preset_subject() -> None:
    class FailingGeminiGridGenerator:
        def generate(self, **_: object) -> object:
            raise ColorGridGenerationError("color grid must use at least two palette labels so the subject is visible")

    service = WorksheetRunGenerationService(
        artifact_root=Path("/tmp/preset-visual-metrics"),
        gemini_color_grid_generator=FailingGeminiGridGenerator(),
    )
    palette = service._palette_entries_for_picture(source="gemini", preset_name="smile", color_count=8)
    labels = [label for label, _ in palette]

    definition = service._generate_color_picture_definition(  # noqa: SLF001
        source="gemini",
        preset_name="smile",
        theme="kindness",
        solution_phrase="HEART",
        prompt_text="What shape shows you care?",
        difficulty_maximum=3,
        color_labels=labels,
    )

    assert definition.source == "preset"
    assert definition.name == "heart"
    assert definition.grid_size == difficulty_to_grid_size(3)


def test_gemini_color_picture_falls_back_after_service_error() -> None:
    class UnavailableGeminiGridGenerator:
        def generate(self, **_: object) -> object:
            raise RuntimeError("503 UNAVAILABLE")

    service = WorksheetRunGenerationService(
        artifact_root=Path("/tmp/preset-visual-metrics"),
        gemini_color_grid_generator=UnavailableGeminiGridGenerator(),
    )
    palette = service._palette_entries_for_picture(source="gemini", preset_name="smile", color_count=8)
    labels = [label for label, _ in palette]

    definition = service._generate_color_picture_definition(  # noqa: SLF001
        source="gemini",
        preset_name="smile",
        theme="ocean",
        solution_phrase="CLOWN FISH",
        prompt_text="What bright fish hides in a sea anemone?",
        difficulty_maximum=3,
        color_labels=labels,
    )

    assert definition.source == "preset"
    assert definition.name == "clown_fish"
