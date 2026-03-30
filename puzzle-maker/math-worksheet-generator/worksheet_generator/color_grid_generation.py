from __future__ import annotations

from dataclasses import dataclass
from math import atan2, cos, pi, sin, sqrt
import logging
from typing import Sequence

from google import genai
from pydantic import BaseModel, Field, ValidationError

from .logging_utils import log_event


PRESET_PICTURE_OPTIONS = [
    {"value": "smile", "label": "Smile"},
    {"value": "heart", "label": "Heart"},
    {"value": "star", "label": "Star"},
    {"value": "moon", "label": "Moon"},
    {"value": "sun", "label": "Sun"},
    {"value": "flower", "label": "Flower"},
    {"value": "apple", "label": "Apple"},
    {"value": "tree", "label": "Deciduous Tree"},
    {"value": "christmas_tree", "label": "Evergreen"},
    {"value": "cat", "label": "Cat"},
    {"value": "clown_fish", "label": "Clown Fish"},
    {"value": "blue_tang", "label": "Blue Tang"},
    {"value": "butterfly", "label": "Butterfly"},
    {"value": "rocket", "label": "Rocket"},
]


def difficulty_to_grid_size(difficulty: int) -> int:
    difficulty = max(1, min(5, difficulty))
    return 16 + (difficulty - 1) * 6


def difficulty_to_color_count(difficulty: int) -> int:
    difficulty = max(1, min(5, difficulty))
    return {
        1: 4,
        2: 6,
        3: 8,
        4: 16,
        5: 32,
    }[difficulty]


@dataclass(frozen=True)
class ColorGridDefinition:
    source: str
    name: str
    grid_size: int
    cells: list[list[str]]


@dataclass(frozen=True)
class PresetBitmap:
    rows: tuple[str, ...]
    token_roles: dict[str, tuple[int, ...]]


class ColorGridGenerationError(ValueError):
    pass


class GeminiColorGridPayload(BaseModel):
    grid_size: int = Field(description="The width and height of the square grid.")
    grid: list[list[str]] = Field(description="A square 2D array of palette labels.")

BUTTERFLY_BITMAP_ROWS = (
    ".........B..B.........",
    "..........BB..........",
    ".......WWWBBWWW.......",
    ".....WWWWWBBWWWWW.....",
    "....WWWWWWBBWWWWWW....",
    "...WWWWWWWBBWWWWWWW...",
    "..WWWWWWWWBBWWWWWWWW..",
    "..WWWWWWWWBBWWWWWWWW..",
    "...WWWWWWWBBWWWWWWW...",
    "....WWWWWWBBWWWWWW....",
    ".....WWWWWBBWWWWW.....",
    "......WWWWBBWWWW......",
    ".......WWWBBWWW.......",
    "........WWBBWW........",
    ".........WBBBBW.......",
    "..........BBBB........",
    "...........BB.........",
    "......................",
    "......................",
    "......................",
    "......................",
    "......................",
    "......................",
    "......................",
)

CAT_BITMAP_ROWS = (
    "........................",
    ".......OO......OO.......",
    "......OOOO....OOOO......",
    ".....OOFFOOOOOOFFOO.....",
    "....OOFFFFFFFFFFFFOO....",
    "...OOFFFFFFFFFFFFFFOO...",
    "..OOFFFFFEEFFEEFFFFFOO..",
    "..OOFFFFFFFFFFFFFFFFOO..",
    "..OOFFFF.W.NN.W.FFFFOO..",
    ".OOFFFFW.CCCCCC.WFFFFOO.",
    "..OOFFFF.W.CC.W.FFFFOO..",
    "..OOFFFFWWWWWWWWFFFFOO..",
    "..OOFFFFFFFFFFFFFFFFOO..",
    "...OOFFFFFFFFFFFFFFOO...",
    "....OOFFFFFFFFFFFFOO....",
    ".....OOFFFFFFFFFFOO.....",
    "......OOOFFFFFFOOO......",
    "........OOFFFFOO........",
    ".........OOOOOO.........",
    "........................",
    "........................",
    "........................",
    "........................",
    "........................",
    "........................",
    "........................",
)

CLOWN_FISH_BITMAP_ROWS = (
    "........................",
    "........................",
    "........................",
    ".............OO.........",
    ".........OOOOAAOO.......",
    "......OOOOAAWWAAOO......",
    "....OOOAAWWAAAAAAOO.....",
    "...OOAEEWWAAAAAAAAOO....",
    "..OOOAEEWWAAAAAAAAOOO...",
    ".OOOMAAAAAAAAAAAAAAAOO..",
    ".OOAMAAAAAAAAAAAAWWAOO..",
    ".OOAAWAAAAAAAAAAAWWAOO..",
    ".OOOAAAAAAAAAAAAAAAAOO..",
    "..OOOAAWWAAAAAAAAAOOO...",
    "...OOAAWWAAAAAAAAAOO....",
    "....OOOAAAAAAWWAAOO.....",
    "......OOOOAAWWAAOO......",
    ".........OOOOAAOO.......",
    ".............OO.........",
    "........................",
    "........................",
    "........................",
    "........................",
    "........................",
    "........................",
)

BLUE_TANG_BITMAP_ROWS = (
    "........................",
    "........................",
    "........................",
    "...............YY.......",
    "..........OOOOBBYYY.....",
    ".......OOOOBBBBBBYYY....",
    ".....OOOBBBBBBBBBBYY....",
    "....OOBBBBBBBBBBBBBYY...",
    "...OOBBBBBBBBBBBBBBBYY..",
    "..OOBBEEOYYYYBBBBBBBYY..",
    "..OOMMMBBBBBBBBBBBBBYY..",
    "..OOBBBBBBOBBBBBBBBBYY..",
    "..OOBBBBBBBBBBBBBBBBYY..",
    "...OOBBBBBBBBBBBBBBBYY..",
    "....OOBBBBBBBBBBBBBYY...",
    ".....OOOBBBBBBBBBBYY....",
    ".......OOOOBBBBBBYYY....",
    "..........OOOOBBYYY.....",
    "...............YY.......",
    "........................",
    "........................",
    "........................",
    "........................",
    "........................",
    "........................",
)

SMILE_BITMAP_ROWS = (
    "........................",
    "........................",
    "........OOOOOOOO........",
    "......OOYYYYYYYYOO......",
    ".....OYYYYYYYYYYYYO.....",
    "....OYYYYYYYYYYYYYYO....",
    "...OYYYYYYEEYYEEYYYYO...",
    "...OYYYYYYEEYYEEYYYYO...",
    "..OYYYYYYYYYYYYYYYYYYO..",
    "..OYYYYYYYYYYYYYYYYYYO..",
    "..OYYYYYYYYYYYYYYYYYYO..",
    "..OYYYYYYYYYYYYYYYYYYO..",
    "..OYYYYMYYYYYYYYMYYYYO..",
    "..OYYYYYMYYYYYYMYYYYYO..",
    "...OYYYYYMYYYYMYYYYYO...",
    "...OYYYYYYMMMMMMYYYYYO...",
    "....OYYYYYYYYYYYYYYO....",
    ".....OYYYYYYYYYYYYO.....",
    "......OOYYYYYYYYOO......",
    "........OOOOOOOO........",
    "........................",
    "........................",
    "........................",
    "........................",
)


class PresetColorGridGenerator:
    def generate(self, *, preset_name: str, grid_size: int, palette_labels: Sequence[str]) -> ColorGridDefinition:
        if not palette_labels:
            raise ColorGridGenerationError("color grid generation requires at least one palette label")
        normalized_name = preset_name if any(option["value"] == preset_name for option in PRESET_PICTURE_OPTIONS) else "smile"
        if normalized_name == "cat":
            return self._generate_bitmap(
                name="cat",
                bitmap=PresetBitmap(
                    rows=CAT_BITMAP_ROWS,
                    token_roles={
                        "O": (1,),
                        "W": (1, 7),
                        "E": (7, 1),
                        "N": (6,),
                        "C": (5,),
                        "F": (2, 3, 4, 8, 9),
                    },
                ),
                grid_size=grid_size,
                palette_labels=palette_labels,
            )
        if normalized_name == "clown_fish":
            return self._generate_bitmap(
                name="clown_fish",
                bitmap=PresetBitmap(
                    rows=CLOWN_FISH_BITMAP_ROWS,
                    token_roles={
                        "O": (1,),
                        "M": (1,),
                        "E": (1,),
                        "A": (2, 4, 5, 6),
                        "W": (3, 7),
                    },
                ),
                grid_size=grid_size,
                palette_labels=palette_labels,
            )
        if normalized_name == "blue_tang":
            return self._generate_bitmap(
                name="blue_tang",
                bitmap=PresetBitmap(
                    rows=BLUE_TANG_BITMAP_ROWS,
                    token_roles={
                        "O": (1,),
                        "M": (1,),
                        "E": (1,),
                        "B": (2, 4, 5, 7),
                        "Y": (3, 6),
                    },
                ),
                grid_size=grid_size,
                palette_labels=palette_labels,
            )
        if normalized_name == "smile":
            return self._generate_bitmap(
                name="smile",
                bitmap=PresetBitmap(
                    rows=SMILE_BITMAP_ROWS,
                    token_roles={
                        "O": (1,),
                        "E": (1,),
                        "M": (1,),
                        "Y": (2, 3, 4, 5, 6, 7),
                    },
                ),
                grid_size=grid_size,
                palette_labels=palette_labels,
            )
        if normalized_name == "flower":
            return self._generate_flower(grid_size=grid_size, palette_labels=palette_labels)
        if normalized_name == "apple":
            return self._generate_apple(grid_size=grid_size, palette_labels=palette_labels)
        if normalized_name == "tree":
            return self._generate_deciduous_tree(grid_size=grid_size, palette_labels=palette_labels)
        if normalized_name == "christmas_tree":
            return self._generate_christmas_tree(grid_size=grid_size, palette_labels=palette_labels)
        if normalized_name == "butterfly":
            return self._generate_butterfly_bitmap(grid_size=grid_size, palette_labels=palette_labels)
        if normalized_name == "rocket":
            return self._generate_rocket(grid_size=grid_size, palette_labels=palette_labels)
        background_label, border_label, feature_label = self._color_roles(palette_labels)
        shape_mask = [[False for _ in range(grid_size)] for _ in range(grid_size)]
        feature_mask = [[False for _ in range(grid_size)] for _ in range(grid_size)]

        for row in range(grid_size):
            for column in range(grid_size):
                x = ((column + 0.5) / grid_size) * 2 - 1
                y = 1 - ((row + 0.5) / grid_size) * 2
                shape_mask[row][column] = self._shape_mask(normalized_name, x, y)
                feature_mask[row][column] = self._feature_mask(normalized_name, x, y)

        outline_mask = self._outline_mask(shape_mask)
        cells = [[background_label for _ in range(grid_size)] for _ in range(grid_size)]
        for row in range(grid_size):
            for column in range(grid_size):
                if not shape_mask[row][column]:
                    continue
                if outline_mask[row][column]:
                    cells[row][column] = border_label
                elif feature_mask[row][column]:
                    if normalized_name in {"fish"}:
                        cells[row][column] = background_label
                    else:
                        cells[row][column] = feature_label
                else:
                    cells[row][column] = self._fill_label(row, column, grid_size, palette_labels)

        return validate_color_grid(
            source="preset",
            name=normalized_name,
            grid_size=grid_size,
            cells=cells,
            allowed_labels=palette_labels,
        )

    def _generate_deciduous_tree(self, *, grid_size: int, palette_labels: Sequence[str]) -> ColorGridDefinition:
        background = palette_labels[0]
        trunk = palette_labels[min(1, len(palette_labels) - 1)]
        leaf_labels = list(palette_labels[2:]) or list(palette_labels[1:]) or [background]
        cells = [[background for _ in range(grid_size)] for _ in range(grid_size)]
        for row in range(grid_size):
            for column in range(grid_size):
                x = ((column + 0.5) / grid_size) * 2 - 1
                y = 1 - ((row + 0.5) / grid_size) * 2
                canopy = (
                    x * x + (y - 0.34) ** 2 <= 0.31 ** 2
                    or (x + 0.28) ** 2 + (y - 0.08) ** 2 <= 0.28 ** 2
                    or (x - 0.28) ** 2 + (y - 0.08) ** 2 <= 0.28 ** 2
                    or x * x + (y + 0.04) ** 2 <= 0.36 ** 2
                )
                trunk_region = abs(x) < 0.12 and -0.84 < y < -0.08
                if trunk_region:
                    cells[row][column] = trunk
                elif canopy:
                    cells[row][column] = leaf_labels[(row * 2 + column * 3) % len(leaf_labels)]
        return validate_color_grid(
            source="preset",
            name="tree",
            grid_size=grid_size,
            cells=cells,
            allowed_labels=palette_labels,
        )

    def _generate_christmas_tree(self, *, grid_size: int, palette_labels: Sequence[str]) -> ColorGridDefinition:
        background = palette_labels[0]
        trunk = palette_labels[min(1, len(palette_labels) - 1)]
        tree_labels = list(palette_labels[2:]) or list(palette_labels[1:]) or [background]
        cells = [[background for _ in range(grid_size)] for _ in range(grid_size)]
        for row in range(grid_size):
            for column in range(grid_size):
                x = ((column + 0.5) / grid_size) * 2 - 1
                y = 1 - ((row + 0.5) / grid_size) * 2
                tier_top = y > 0.28 and y <= 0.8 and abs(x) < (0.12 + (0.8 - y) * 0.28)
                tier_mid = y > -0.02 and y <= 0.46 and abs(x) < (0.18 + (0.46 - y) * 0.45)
                tier_low = y > -0.34 and y <= 0.16 and abs(x) < (0.24 + (0.16 - y) * 0.52)
                trunk_region = abs(x) < 0.1 and -0.74 < y <= -0.34
                tree_region = tier_top or tier_mid or tier_low
                if trunk_region:
                    cells[row][column] = trunk
                elif tree_region:
                    cells[row][column] = tree_labels[(row * 2 + column * 3) % len(tree_labels)]
        return validate_color_grid(
            source="preset",
            name="evergreen",
            grid_size=grid_size,
            cells=cells,
            allowed_labels=palette_labels,
        )

    def _generate_flower(self, *, grid_size: int, palette_labels: Sequence[str]) -> ColorGridDefinition:
        background = palette_labels[0]
        petal_labels = [
            palette_labels[1],
            palette_labels[min(4, len(palette_labels) - 1)],
            palette_labels[min(5, len(palette_labels) - 1)],
            palette_labels[min(6, len(palette_labels) - 1)],
            palette_labels[min(7, len(palette_labels) - 1)],
        ]
        center_label = palette_labels[min(2, len(palette_labels) - 1)]
        stem_label = palette_labels[min(3, len(palette_labels) - 1)]
        cells = [[background for _ in range(grid_size)] for _ in range(grid_size)]
        for row in range(grid_size):
            for column in range(grid_size):
                x = ((column + 0.5) / grid_size) * 2 - 1
                y = 1 - ((row + 0.5) / grid_size) * 2
                bloom_cy = 0.18
                center = x * x + (y - bloom_cy) ** 2 <= 0.12 ** 2
                petal = False
                for petal_index in range(6):
                    angle = (pi / 3.0) * petal_index
                    petal_cx = 0.34 * cos(angle)
                    petal_cy = bloom_cy + 0.3 * sin(angle)
                    local_x = x - petal_cx
                    local_y = y - petal_cy
                    major = 0.2 if petal_index % 3 == 0 else 0.18
                    minor = 0.125
                    if (local_x * local_x) / (major * major) + (local_y * local_y) / (minor * minor) <= 1.0:
                        petal = True
                        break
                stem = abs(x) < 0.055 and -0.82 < y < -0.12
                leaf = (
                    ((x + 0.16) ** 2) / 0.12 ** 2 + ((y + 0.46) ** 2) / 0.07 ** 2 <= 1.0
                    or ((x - 0.18) ** 2) / 0.13 ** 2 + ((y + 0.3) ** 2) / 0.07 ** 2 <= 1.0
                )
                if center:
                    cells[row][column] = center_label
                elif stem or leaf:
                    cells[row][column] = stem_label
                elif petal:
                    cells[row][column] = petal_labels[(row + column) % len(petal_labels)]
        return validate_color_grid(
            source="preset",
            name="flower",
            grid_size=grid_size,
            cells=cells,
            allowed_labels=palette_labels,
        )

    def _generate_apple(self, *, grid_size: int, palette_labels: Sequence[str]) -> ColorGridDefinition:
        background = palette_labels[0]
        stem_labels = [palette_labels[1], palette_labels[min(9, len(palette_labels) - 1)]]
        body_labels = [
            palette_labels[min(2, len(palette_labels) - 1)],
            palette_labels[min(3, len(palette_labels) - 1)],
            palette_labels[min(4, len(palette_labels) - 1)],
            palette_labels[min(5, len(palette_labels) - 1)],
            palette_labels[min(7, len(palette_labels) - 1)],
        ]
        leaf_labels = [palette_labels[min(6, len(palette_labels) - 1)], palette_labels[min(11, len(palette_labels) - 1)]]
        cells = [[background for _ in range(grid_size)] for _ in range(grid_size)]
        for row in range(grid_size):
            for column in range(grid_size):
                x = ((column + 0.5) / grid_size) * 2 - 1
                y = 1 - ((row + 0.5) / grid_size) * 2
                left = (x + 0.2) ** 2 + (y + 0.03) ** 2 <= 0.34 ** 2
                right = (x - 0.2) ** 2 + (y + 0.03) ** 2 <= 0.34 ** 2
                bottom = x * x + (y + 0.26) ** 2 <= 0.42 ** 2
                stem = abs(x) < 0.045 and 0.42 < y < 0.74
                leaf = (((x - 0.16) ** 2) / 0.11 ** 2) + (((y - 0.5) ** 2) / 0.07 ** 2) <= 1.0 and x > 0.02
                if stem:
                    cells[row][column] = stem_labels[(row + column) % len(stem_labels)]
                elif leaf:
                    cells[row][column] = leaf_labels[(row + column) % len(leaf_labels)]
                elif left or right or bottom:
                    cells[row][column] = body_labels[(row * 2 + column) % len(body_labels)]
        return validate_color_grid(
            source="preset",
            name="apple",
            grid_size=grid_size,
            cells=cells,
            allowed_labels=palette_labels,
        )

    def _generate_rocket(self, *, grid_size: int, palette_labels: Sequence[str]) -> ColorGridDefinition:
        background = palette_labels[0]
        border = palette_labels[min(1, len(palette_labels) - 1)]
        flame_labels = [
            label
            for label in palette_labels
            if "flame" in label.lower() or label.lower() == "hot red"
        ]
        if not flame_labels:
            flame_labels = list(palette_labels[-1:])
        cloud_label = next(
            (label for label in palette_labels if "cloud" in label.lower()),
            background,
        )
        reserved_labels = {background, border, cloud_label, *flame_labels}
        body_labels = [label for label in palette_labels[2:] if label not in reserved_labels] or [border]
        window_label = palette_labels[min(3, len(palette_labels) - 1)]
        cells = [[background for _ in range(grid_size)] for _ in range(grid_size)]
        for row in range(grid_size):
            for column in range(grid_size):
                x = ((column + 0.5) / grid_size) * 2 - 1
                y = 1 - ((row + 0.5) / grid_size) * 2
                body = abs(x) < 0.18 and -0.48 < y < 0.44
                nose = y > 0.44 and y < 0.84 and abs(x) < (-0.42 * (y - 0.44)) + 0.18
                left_fin = y < -0.12 and y > -0.62 and x < -0.18 and x > -0.44 and y < (2.1 * (x + 0.44)) - 0.12
                right_fin = y < -0.12 and y > -0.62 and x > 0.18 and x < 0.44 and y < (-2.1 * (x - 0.44)) - 0.12
                flame = abs(x) < 0.12 and -0.96 < y < -0.46 and abs(x) < (0.13 - (y + 0.96) * 0.12)
                window = x * x + (y - 0.14) ** 2 <= 0.08 ** 2
                cloud = (
                    ((x + 0.48) ** 2) / 0.16 ** 2 + ((y - 0.72) ** 2) / 0.08 ** 2 <= 1.0
                    or ((x + 0.34) ** 2) / 0.12 ** 2 + ((y - 0.68) ** 2) / 0.07 ** 2 <= 1.0
                    or ((x + 0.2) ** 2) / 0.14 ** 2 + ((y - 0.72) ** 2) / 0.08 ** 2 <= 1.0
                )
                if cloud:
                    cells[row][column] = cloud_label
                    continue
                if not (body or nose or left_fin or right_fin or flame):
                    continue
                if flame:
                    cells[row][column] = flame_labels[(row + column) % len(flame_labels)]
                elif window:
                    cells[row][column] = window_label
                elif abs(x) > 0.14 or left_fin or right_fin:
                    cells[row][column] = border
                else:
                    cells[row][column] = body_labels[(row * 2 + column * 3) % len(body_labels)]
        return validate_color_grid(
            source="preset",
            name="rocket",
            grid_size=grid_size,
            cells=cells,
            allowed_labels=palette_labels,
        )

    def _generate_cat(self, *, grid_size: int, palette_labels: Sequence[str]) -> ColorGridDefinition:
        background = palette_labels[0]
        outline = palette_labels[min(1, len(palette_labels) - 1)]
        face_labels = list(palette_labels[2:]) or [outline]
        feature = outline
        cells = [[background for _ in range(grid_size)] for _ in range(grid_size)]
        for row in range(grid_size):
            for column in range(grid_size):
                x = ((column + 0.5) / grid_size) * 2 - 1
                y = 1 - ((row + 0.5) / grid_size) * 2
                head = x * x + (y + 0.02) ** 2 <= 0.44 ** 2
                left_ear = y > 0.1 and x < -0.06 and y < (-3.1 * (x + 0.44)) + 1.0 and y < (2.1 * (x + 0.02)) + 1.0
                right_ear = y > 0.1 and x > 0.06 and y < (3.1 * (x - 0.44)) + 1.0 and y < (-2.1 * (x - 0.02)) + 1.0
                if not (head or left_ear or right_ear):
                    continue
                edge = (
                    abs((x * x + (y + 0.02) ** 2) - 0.44 ** 2) < 0.06
                    or (left_ear and (abs(y - ((-3.1 * (x + 0.44)) + 1.0)) < 0.05 or abs(y - ((2.1 * (x + 0.02)) + 1.0)) < 0.05))
                    or (right_ear and (abs(y - ((3.1 * (x - 0.44)) + 1.0)) < 0.05 or abs(y - ((-2.1 * (x - 0.02)) + 1.0)) < 0.05))
                )
                left_eye = ((x + 0.18) ** 2 + (y - 0.02) ** 2) <= 0.07 ** 2
                right_eye = ((x - 0.18) ** 2 + (y - 0.02) ** 2) <= 0.07 ** 2
                nose = abs(x) < 0.032 and -0.14 < y < -0.1
                smile = abs(y + 0.18) < 0.02 and abs(x) < 0.06
                whiskers = (
                    abs(y + 0.06) < 0.015 and ((-0.42 < x < -0.04) or (0.04 < x < 0.42))
                ) or (
                    abs(y + 0.12) < 0.015 and ((-0.4 < x < -0.05) or (0.05 < x < 0.4))
                ) or (
                    abs(y + 0.18) < 0.015 and ((-0.34 < x < -0.02) or (0.02 < x < 0.34))
                )
                if left_eye or right_eye or nose or smile or whiskers or edge:
                    cells[row][column] = feature if (left_eye or right_eye or nose or smile or whiskers) else outline
                else:
                    cells[row][column] = face_labels[(row + column * 2) % len(face_labels)]
        return validate_color_grid(
            source="preset",
            name="cat",
            grid_size=grid_size,
            cells=cells,
            allowed_labels=palette_labels,
        )

    def _generate_bitmap(
        self,
        *,
        name: str,
        bitmap: PresetBitmap,
        grid_size: int,
        palette_labels: Sequence[str],
    ) -> ColorGridDefinition:
        background = palette_labels[0]
        source_rows = len(bitmap.rows)
        source_columns = len(bitmap.rows[0])
        cells = [[background for _ in range(grid_size)] for _ in range(grid_size)]
        for row in range(grid_size):
            source_row = min(source_rows - 1, int(row * source_rows / grid_size))
            for column in range(grid_size):
                source_column = min(source_columns - 1, int(column * source_columns / grid_size))
                token = bitmap.rows[source_row][source_column]
                if token == ".":
                    continue
                palette_indices = bitmap.token_roles.get(token)
                if not palette_indices:
                    continue
                valid_indices = [index for index in palette_indices if index < len(palette_labels)]
                if not valid_indices:
                    valid_indices = [min(len(palette_labels) - 1, palette_indices[0])]
                cells[row][column] = palette_labels[valid_indices[(source_row + source_column * 2) % len(valid_indices)]]
        return validate_color_grid(
            source="preset",
            name=name,
            grid_size=grid_size,
            cells=cells,
            allowed_labels=palette_labels,
        )

    def _generate_butterfly_bitmap(self, *, grid_size: int, palette_labels: Sequence[str]) -> ColorGridDefinition:
        background = palette_labels[0]
        body = palette_labels[min(1, len(palette_labels) - 1)]
        wing_labels = list(palette_labels[2:]) or [body]
        source_rows = len(BUTTERFLY_BITMAP_ROWS)
        source_columns = len(BUTTERFLY_BITMAP_ROWS[0])
        cells = [[background for _ in range(grid_size)] for _ in range(grid_size)]
        for row in range(grid_size):
            source_row = min(source_rows - 1, int(row * source_rows / grid_size))
            for column in range(grid_size):
                source_column = min(source_columns - 1, int(column * source_columns / grid_size))
                token = BUTTERFLY_BITMAP_ROWS[source_row][source_column]
                if token == ".":
                    continue
                if token == "B":
                    cells[row][column] = body
                else:
                    cells[row][column] = wing_labels[(source_row + source_column * 2) % len(wing_labels)]
        return validate_color_grid(
            source="preset",
            name="butterfly",
            grid_size=grid_size,
            cells=cells,
            allowed_labels=palette_labels,
        )

    def _color_roles(self, palette_labels: Sequence[str]) -> tuple[str, str, str]:
        background = palette_labels[0]
        border = palette_labels[1] if len(palette_labels) > 1 else background
        feature = palette_labels[-1] if len(palette_labels) > 2 else border
        return background, border, feature

    def _fill_label(self, row: int, column: int, grid_size: int, palette_labels: Sequence[str]) -> str:
        interior_labels = list(palette_labels[2:]) or list(palette_labels)
        return interior_labels[(row * 3 + column * 5) % len(interior_labels)]

    def _heart_mask(self, x: float, y: float) -> bool:
        x = x * 0.94
        scaled_y = (y + 0.08) * 1.12
        return ((x * x + scaled_y * scaled_y - 0.62) ** 3) - (x * x * scaled_y * scaled_y * scaled_y) <= 0

    def _star_mask(self, x: float, y: float) -> bool:
        angle = atan2(y, x)
        radius = sqrt(x * x + y * y)
        point_sector = (angle + (pi / 2)) % ((2 * pi) / 5)
        distance_from_point = min(point_sector, ((2 * pi) / 5) - point_sector)
        outer_radius = 0.86
        inner_radius = 0.24
        transition = distance_from_point / (pi / 5)
        boundary = outer_radius - (outer_radius - inner_radius) * transition
        return radius <= boundary

    def _moon_mask(self, x: float, y: float) -> bool:
        outer = (x + 0.02) ** 2 + y * y <= 0.56 ** 2
        inner = (x + 0.22) ** 2 + y * y < 0.43 ** 2
        return outer and not inner

    def _sun_mask(self, x: float, y: float) -> bool:
        radius = sqrt(x * x + y * y)
        cross_rays = (abs(x) < 0.11 and abs(y) < 0.82) or (abs(y) < 0.11 and abs(x) < 0.82)
        diagonal_rays = abs(abs(x) - abs(y)) < 0.11 and max(abs(x), abs(y)) < 0.74
        return radius <= 0.5 or cross_rays or diagonal_rays

    def _flower_mask(self, x: float, y: float) -> bool:
        center = x * x + y * y <= 0.14 ** 2
        petals = (
            ((x - 0.28) ** 2 + y * y <= 0.2 ** 2)
            or ((x + 0.28) ** 2 + y * y <= 0.2 ** 2)
            or (x * x + (y - 0.28) ** 2 <= 0.2 ** 2)
            or (x * x + (y + 0.28) ** 2 <= 0.2 ** 2)
            or ((x - 0.2) ** 2 + (y - 0.2) ** 2 <= 0.18 ** 2)
            or ((x + 0.2) ** 2 + (y - 0.2) ** 2 <= 0.18 ** 2)
        )
        stem = abs(x) < 0.08 and -0.82 < y < -0.18
        leaf = ((x + 0.16) ** 2 + (y + 0.46) ** 2 <= 0.12 ** 2)
        return center or petals or stem or leaf

    def _apple_mask(self, x: float, y: float) -> bool:
        left = (x + 0.2) ** 2 + (y + 0.03) ** 2 <= 0.34 ** 2
        right = (x - 0.2) ** 2 + (y + 0.03) ** 2 <= 0.34 ** 2
        bottom = x * x + (y + 0.26) ** 2 <= 0.42 ** 2
        stem = abs(x) < 0.06 and 0.38 < y < 0.7
        leaf = ((x + 0.18) ** 2 + (y - 0.5) ** 2 <= 0.11 ** 2)
        return left or right or bottom or stem or leaf

    def _tree_mask(self, x: float, y: float) -> bool:
        canopy = (
            (x * x + (y - 0.38) ** 2 <= 0.18 ** 2)
            or ((x - 0.22) ** 2 + (y - 0.16) ** 2 <= 0.2 ** 2)
            or ((x + 0.22) ** 2 + (y - 0.16) ** 2 <= 0.2 ** 2)
            or (x * x + (y + 0.02) ** 2 <= 0.26 ** 2)
        )
        trunk = abs(x) < 0.12 and -0.74 < y < -0.1
        return canopy or trunk

    def _cat_mask(self, x: float, y: float) -> bool:
        head = x * x + (y + 0.02) ** 2 <= 0.36 ** 2
        left_cheek = (x + 0.22) ** 2 + (y + 0.12) ** 2 <= 0.2 ** 2
        right_cheek = (x - 0.22) ** 2 + (y + 0.12) ** 2 <= 0.2 ** 2
        left_ear_outer = y > 0.08 and x < -0.08 and y < (-3.2 * (x + 0.46)) + 0.92 and y < (2.2 * (x + 0.1)) + 0.96
        right_ear_outer = y > 0.08 and x > 0.08 and y < (3.2 * (x - 0.46)) + 0.92 and y < (-2.2 * (x - 0.1)) + 0.96
        forehead = abs(x) < 0.18 and 0.16 < y < 0.38
        return head or left_cheek or right_cheek or left_ear_outer or right_ear_outer or forehead

    def _fish_mask(self, x: float, y: float) -> bool:
        body = ((x + 0.02) ** 2) / 0.46 ** 2 + (y * y) / 0.22 ** 2 <= 1
        tail = x > 0.22 and x < 0.72 and abs(y) < (-1.45 * (x - 0.22)) + 0.22
        fin = x > -0.12 and x < 0.14 and y > 0.01 and y < (-1.35 * (x + 0.12)) + 0.24
        return body or tail or fin

    def _butterfly_mask(self, x: float, y: float) -> bool:
        left_wing = ((x + 0.33) ** 2) / 0.28 ** 2 + ((y - 0.02) ** 2) / 0.42 ** 2 <= 1
        right_wing = ((x - 0.33) ** 2) / 0.28 ** 2 + ((y - 0.02) ** 2) / 0.42 ** 2 <= 1
        inner_cutout = ((x + 0.08) ** 2) / 0.12 ** 2 + ((y + 0.04) ** 2) / 0.26 ** 2 <= 1
        mirrored_cutout = ((x - 0.08) ** 2) / 0.12 ** 2 + ((y + 0.04) ** 2) / 0.26 ** 2 <= 1
        body = abs(x) < 0.1 and -0.62 < y < 0.64
        return ((left_wing or right_wing) and not (inner_cutout or mirrored_cutout)) or body

    def _rocket_mask(self, x: float, y: float) -> bool:
        body = abs(x) < 0.18 and -0.58 < y < 0.34
        nose = y > 0.34 and y < 0.76 and abs(x) < (-0.42 * (y - 0.34)) + 0.18
        left_fin = y < -0.22 and y > -0.72 and x < -0.18 and x > -0.44 and y < (2.1 * (x + 0.44)) - 0.22
        right_fin = y < -0.22 and y > -0.72 and x > 0.18 and x < 0.44 and y < (-2.1 * (x - 0.44)) - 0.22
        flame = abs(x) < 0.1 and -0.88 < y < -0.58
        return body or nose or left_fin or right_fin or flame

    def _smile_face_mask(self, x: float, y: float) -> bool:
        return sqrt(x * x + y * y) <= 0.68

    def _smile_feature_mask(self, x: float, y: float) -> bool:
        left_eye = ((x + 0.24) ** 2 + (y - 0.18) ** 2) <= 0.095 ** 2
        right_eye = ((x - 0.24) ** 2 + (y - 0.18) ** 2) <= 0.095 ** 2
        mouth_curve = abs((((x / 0.44) ** 2) + (((y + 0.16) / 0.24) ** 2)) - 1.0) <= 0.22 and y < -0.04
        mouth_cutoff = y > -0.38
        mouth_bridge = abs(y + 0.28) < 0.045 and abs(x) < 0.16
        return left_eye or right_eye or ((mouth_curve and mouth_cutoff) or mouth_bridge)

    def _shape_mask(self, preset_name: str, x: float, y: float) -> bool:
        match preset_name:
            case "heart":
                return self._heart_mask(x, y)
            case "star":
                return self._star_mask(x, y)
            case "moon":
                return self._moon_mask(x, y)
            case "sun":
                return self._sun_mask(x, y)
            case "flower":
                return self._flower_mask(x, y)
            case "apple":
                return self._apple_mask(x, y)
            case "tree":
                return self._tree_mask(x, y)
            case "cat":
                return self._cat_mask(x, y)
            case "fish":
                return self._fish_mask(x, y)
            case "butterfly":
                return self._butterfly_mask(x, y)
            case "rocket":
                return self._rocket_mask(x, y)
            case _:
                return self._smile_face_mask(x, y)

    def _feature_mask(self, preset_name: str, x: float, y: float) -> bool:
        if preset_name == "smile":
            return self._smile_feature_mask(x, y)
        if preset_name == "apple":
            return abs(x) < 0.05 and 0.42 < y < 0.68
        if preset_name == "tree":
            return abs(x) < 0.1 and -0.7 < y < -0.08
        if preset_name == "cat":
            left_eye = ((x + 0.14) ** 2 + (y - 0.02) ** 2) <= 0.055 ** 2
            right_eye = ((x - 0.14) ** 2 + (y - 0.02) ** 2) <= 0.055 ** 2
            nose = abs(x) < 0.045 and -0.16 < y < -0.08
            smile = abs((((x / 0.16) ** 2) + (((y + 0.14) / 0.08) ** 2)) - 1.0) <= 0.28 and y < -0.1 and y > -0.2
            whiskers = (
                abs(y + 0.1) < 0.018 and ((-0.34 < x < -0.08) or (0.08 < x < 0.34))
            ) or (
                abs(y + 0.16) < 0.018 and ((-0.32 < x < -0.1) or (0.1 < x < 0.32))
            )
            return left_eye or right_eye or nose or smile or whiskers
        if preset_name == "fish":
            return ((x + 0.24) ** 2 + (y - 0.02) ** 2) <= 0.035 ** 2
        if preset_name == "butterfly":
            body = abs(x) < 0.06 and -0.58 < y < 0.58
            antennae = (
                0.54 < y < 0.78
                and (
                    abs(x - (0.28 * (y - 0.54))) < 0.03
                    or abs(x + (0.28 * (y - 0.54))) < 0.03
                )
            )
            return body or antennae
        if preset_name == "rocket":
            return abs(x) < 0.08 and -0.12 < y < 0.12
        return False

    def _outline_mask(self, shape_mask: Sequence[Sequence[bool]]) -> list[list[bool]]:
        rows = len(shape_mask)
        columns = len(shape_mask[0]) if rows else 0
        outline = [[False for _ in range(columns)] for _ in range(rows)]
        for row in range(rows):
            for column in range(columns):
                if not shape_mask[row][column]:
                    continue
                neighbors = (
                    (row - 1, column),
                    (row + 1, column),
                    (row, column - 1),
                    (row, column + 1),
                )
                for neighbor_row, neighbor_column in neighbors:
                    if neighbor_row < 0 or neighbor_row >= rows or neighbor_column < 0 or neighbor_column >= columns:
                        outline[row][column] = True
                        break
                    if not shape_mask[neighbor_row][neighbor_column]:
                        outline[row][column] = True
                        break
        return outline


class GeminiColorGridGenerator:
    def __init__(self, api_key: str, *, model: str = "gemini-2.5-flash-lite") -> None:
        self._client = genai.Client(api_key=api_key)
        self._model = model
        self._logger = logging.getLogger("worksheet_generator.color_grid_gemini")

    def generate(
        self,
        *,
        theme: str,
        solution_phrase: str,
        prompt_text: str,
        grid_size: int,
        palette_labels: Sequence[str],
    ) -> ColorGridDefinition:
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                prompt = self._build_prompt(
                    theme=theme,
                    solution_phrase=solution_phrase,
                    prompt_text=prompt_text,
                    grid_size=grid_size,
                    palette_labels=palette_labels,
                    prior_error=str(last_error) if last_error is not None else None,
                )
                log_event(
                    self._logger,
                    "gemini_color_grid_request",
                    verbosity="normal",
                    model=self._model,
                    grid_size=grid_size,
                    palette_labels=list(palette_labels),
                    attempt=attempt + 1,
                    prompt=prompt,
                )
                response = self._client.models.generate_content(
                    model=self._model,
                    contents=prompt,
                    config={
                        "response_mime_type": "application/json",
                        "response_schema": GeminiColorGridPayload,
                    },
                )
                payload = self._parse_payload(response)
                normalized_grid = self._normalize_grid_shape(
                    payload.grid,
                    grid_size=grid_size,
                    background_label=str(palette_labels[0]),
                )
                if normalized_grid != payload.grid:
                    log_event(
                        self._logger,
                        "gemini_color_grid_shape_normalized",
                        verbosity="normal",
                        attempt=attempt + 1,
                        requested_grid_size=grid_size,
                        returned_row_count=len(payload.grid),
                    )
                definition = validate_color_grid(
                    source="gemini",
                    name=f"{solution_phrase.strip() or theme.strip() or 'theme'} picture",
                    grid_size=grid_size,
                    cells=normalized_grid,
                    allowed_labels=palette_labels,
                )
                log_event(
                    self._logger,
                    "gemini_color_grid_success",
                    verbosity="normal",
                    attempt=attempt + 1,
                    grid_size=definition.grid_size,
                )
                return definition
            except (ColorGridGenerationError, ValidationError, ValueError) as exc:
                last_error = exc
                log_event(
                    self._logger,
                    "gemini_color_grid_retryable_error",
                    error=str(exc),
                    attempt=attempt + 1,
                )
        raise ColorGridGenerationError(f"Gemini returned an invalid color grid after 2 attempts: {last_error}")

    def _parse_payload(self, response: object) -> GeminiColorGridPayload:
        parsed = getattr(response, "parsed", None)
        if isinstance(parsed, GeminiColorGridPayload):
            return parsed
        if isinstance(parsed, dict):
            return GeminiColorGridPayload.model_validate(parsed)
        text = getattr(response, "text", "")
        if not text:
            raise ColorGridGenerationError("Gemini returned an empty color-grid response")
        return GeminiColorGridPayload.model_validate_json(text)

    def _build_prompt(
        self,
        *,
        theme: str,
        solution_phrase: str,
        prompt_text: str,
        grid_size: int,
        palette_labels: Sequence[str],
        prior_error: str | None = None,
    ) -> str:
        palette_text = ", ".join(palette_labels)
        background_label = palette_labels[0]
        subject = solution_phrase.strip() or theme.strip() or "simple classroom object"
        repair_note = ""
        if prior_error:
            repair_note = (
                "Your previous response was invalid.\n"
                f"Previous validation error: {prior_error}\n"
                "Fix the JSON and return a valid square grid this time.\n"
            )
        return (
            "Create one square color-by-number pixel-art grid.\n"
            f"Theme: {theme or 'general classroom math'}\n"
            f"Picture subject: {subject}\n"
            f"Related clue text: {prompt_text or 'Create a picture that clearly matches the clue.'}\n"
            f"Grid size: {grid_size}x{grid_size}\n"
            f"Allowed palette labels: {palette_text}\n"
            f"{repair_note}"
            "Return only JSON with two fields: grid_size and grid.\n"
            f"Set grid_size to exactly {grid_size}.\n"
            "The grid must be a square 2D array with exactly grid_size rows and grid_size columns.\n"
            "Every cell in the grid must be one of the allowed palette labels exactly as provided.\n"
            f"Use {background_label} as the background for most cells.\n"
            "Use at least one other palette label to draw a centered, recognizable subject.\n"
            "The subject must match the picture subject and also fit the related clue text.\n"
            "Create a meaningful, recognizable picture silhouette rather than abstract noise or a blank grid.\n"
            "Keep the subject centered with clear contrast between the background and the subject edges.\n"
            "Do not include explanations, markdown, comments, or extra keys.\n"
        )

    def _normalize_grid_shape(
        self,
        grid: Sequence[Sequence[str]],
        *,
        grid_size: int,
        background_label: str,
    ) -> list[list[str]]:
        normalized_rows: list[list[str]] = []
        for row in list(grid)[:grid_size]:
            normalized_row = [str(cell) for cell in list(row)[:grid_size]]
            if len(normalized_row) < grid_size:
                normalized_row.extend([background_label] * (grid_size - len(normalized_row)))
            normalized_rows.append(normalized_row)
        while len(normalized_rows) < grid_size:
            normalized_rows.append([background_label] * grid_size)
        return normalized_rows


def validate_color_grid(
    *,
    source: str,
    name: str,
    grid_size: int,
    cells: Sequence[Sequence[str]],
    allowed_labels: Sequence[str],
) -> ColorGridDefinition:
    if grid_size <= 0:
        raise ColorGridGenerationError("color grid size must be positive")
    if len(cells) != grid_size:
        raise ColorGridGenerationError(f"color grid must have exactly {grid_size} rows")
    allowed = set(allowed_labels)
    normalized: list[list[str]] = []
    label_counts: dict[str, int] = {}
    for row in cells:
        if len(row) != grid_size:
            raise ColorGridGenerationError(f"color grid must have exactly {grid_size} columns in every row")
        normalized_row = [str(cell) for cell in row]
        invalid = [cell for cell in normalized_row if cell not in allowed]
        if invalid:
            raise ColorGridGenerationError(f"color grid used labels outside the allowed palette: {sorted(set(invalid))}")
        for cell in normalized_row:
            label_counts[cell] = label_counts.get(cell, 0) + 1
        normalized.append(normalized_row)
    used_labels = {label for label, count in label_counts.items() if count > 0}
    if len(used_labels) < min(2, len(allowed_labels)):
        raise ColorGridGenerationError("color grid must use at least two palette labels so the subject is visible")
    total_cells = grid_size * grid_size
    background_label = str(allowed_labels[0]) if allowed_labels else ""
    foreground_cells = total_cells - label_counts.get(background_label, 0)
    if foreground_cells <= max(4, total_cells // 25):
        raise ColorGridGenerationError("color grid subject is too small or blank")
    if foreground_cells >= int(total_cells * 0.88):
        raise ColorGridGenerationError("color grid does not preserve enough background contrast")
    return ColorGridDefinition(source=source, name=name, grid_size=grid_size, cells=normalized)
