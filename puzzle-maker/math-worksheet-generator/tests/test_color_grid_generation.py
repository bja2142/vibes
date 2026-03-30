from __future__ import annotations

from types import SimpleNamespace

from worksheet_generator.color_grid_generation import (
    ColorGridGenerationError,
    GeminiColorGridGenerator,
    PresetColorGridGenerator,
    difficulty_to_grid_size,
)


def test_preset_color_grid_generator_builds_square_smile_grid() -> None:
    generator = PresetColorGridGenerator()

    definition = generator.generate(
        preset_name="smile",
        grid_size=difficulty_to_grid_size(1),
        palette_labels=["Sun Yellow", "Coral"],
    )

    assert definition.grid_size == 16
    assert len(definition.cells) == 16
    assert all(len(row) == 16 for row in definition.cells)
    used = {cell for row in definition.cells for cell in row}
    assert used.issubset({"Sun Yellow", "Coral"})
    assert len(used) == 2
    assert definition.cells[0][0] == "Sun Yellow"
    lower_half = definition.cells[8:]
    dark_mouth_pixels = sum(cell == "Coral" for row in lower_half for cell in row)
    assert dark_mouth_pixels >= 4


def test_preset_rocket_uses_flame_palette_labels_near_base() -> None:
    generator = PresetColorGridGenerator()

    definition = generator.generate(
        preset_name="rocket",
        grid_size=difficulty_to_grid_size(2),
        palette_labels=["Sky Blue", "Jet Black", "Silver", "Flame Gold", "Flame Orange", "Hot Red"],
    )

    lower_rows = definition.cells[-6:]
    flame_pixels = sum(cell in {"Flame Gold", "Flame Orange", "Hot Red"} for row in lower_rows for cell in row)
    assert flame_pixels >= 3
    assert any("Flame Gold" in row for row in lower_rows)


def test_gemini_color_grid_generator_retries_once_on_invalid_payload() -> None:
    valid_grid = [["A"] * 16 for _ in range(16)]
    for row in range(4, 12):
        for column in range(4, 12):
            valid_grid[row][column] = "B" if (row + column) % 2 == 0 else "C"
    responses = [
        SimpleNamespace(parsed={"grid_size": 16, "grid": [["BAD"] * 16 for _ in range(16)]}),
        SimpleNamespace(parsed={"grid_size": 16, "grid": valid_grid}),
    ]

    class FakeModels:
        def __init__(self, payloads):
            self._payloads = payloads
            self.calls = 0

        def generate_content(self, **_: object) -> object:
            response = self._payloads[self.calls]
            self.calls += 1
            return response

    generator = GeminiColorGridGenerator(api_key="test")
    fake_models = FakeModels(responses)
    generator._client = SimpleNamespace(models=fake_models)  # type: ignore[attr-defined]

    definition = generator.generate(
        theme="stars",
        solution_phrase="Star",
        prompt_text="What shape shines in the night sky?",
        grid_size=16,
        palette_labels=["A", "B", "C"],
    )

    assert fake_models.calls == 2
    assert definition.grid_size == 16
    assert definition.cells[0][0] == "A"
    assert definition.cells[6][6] in {"B", "C"}


def test_gemini_color_grid_generator_normalizes_near_miss_grid_shape() -> None:
    almost_square = [["A"] * 15 for _ in range(15)]
    for row in range(4, 11):
        for column in range(4, 11):
            almost_square[row][column] = "B" if (row + column) % 2 == 0 else "C"

    class FakeModels:
        def __init__(self, payload):
            self._payload = payload
            self.calls = 0

        def generate_content(self, **_: object) -> object:
            self.calls += 1
            return self._payload

    generator = GeminiColorGridGenerator(api_key="test")
    fake_models = FakeModels(SimpleNamespace(parsed={"grid_size": 16, "grid": almost_square}))
    generator._client = SimpleNamespace(models=fake_models)  # type: ignore[attr-defined]

    definition = generator.generate(
        theme="ocean",
        solution_phrase="Shell",
        prompt_text="What has a shell and lives in the sea?",
        grid_size=16,
        palette_labels=["A", "B", "C"],
    )

    assert fake_models.calls == 1
    assert definition.grid_size == 16
    assert len(definition.cells) == 16
    assert all(len(row) == 16 for row in definition.cells)
    assert definition.cells[0][0] == "A"
    assert definition.cells[6][6] in {"B", "C"}


def test_gemini_color_grid_generator_raises_after_second_invalid_payload() -> None:
    responses = [
        SimpleNamespace(parsed={"grid_size": 16, "grid": [["BAD"] * 16 for _ in range(16)]}),
        SimpleNamespace(parsed={"grid_size": 16, "grid": [["WORSE"] * 16 for _ in range(16)]}),
    ]

    class FakeModels:
        def __init__(self, payloads):
            self._payloads = payloads
            self.calls = 0

        def generate_content(self, **_: object) -> object:
            response = self._payloads[self.calls]
            self.calls += 1
            return response

    generator = GeminiColorGridGenerator(api_key="test")
    generator._client = SimpleNamespace(models=FakeModels(responses))  # type: ignore[attr-defined]

    try:
        generator.generate(
            theme="stars",
            solution_phrase="Star",
            prompt_text="What shape shines in the night sky?",
            grid_size=16,
            palette_labels=["A", "B", "C"],
        )
    except ColorGridGenerationError as exc:
        assert "after 2 attempts" in str(exc)
    else:
        raise AssertionError("expected color-grid generation to fail after two invalid payloads")


def test_gemini_color_grid_generator_rejects_blank_grid() -> None:
    responses = [SimpleNamespace(parsed={"grid_size": 16, "grid": [["A"] * 16 for _ in range(16)]})]

    class FakeModels:
        def __init__(self, payloads):
            self._payloads = payloads
            self.calls = 0

        def generate_content(self, **_: object) -> object:
            response = self._payloads[min(self.calls, len(self._payloads) - 1)]
            self.calls += 1
            return response

    generator = GeminiColorGridGenerator(api_key="test")
    generator._client = SimpleNamespace(models=FakeModels(responses))  # type: ignore[attr-defined]

    try:
        generator.generate(
            theme="stars",
            solution_phrase="Star",
            prompt_text="What shape shines in the night sky?",
            grid_size=16,
            palette_labels=["A", "B", "C"],
        )
    except ColorGridGenerationError as exc:
        assert "blank" in str(exc) or "at least two palette labels" in str(exc)
    else:
        raise AssertionError("expected blank color-grid generation to fail")
