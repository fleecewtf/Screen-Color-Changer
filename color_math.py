"""Small, dependency-free color-matrix calculations for the Windows desktop effect."""

from dataclasses import dataclass


SATURATION_LIMITS = (0, 300)
CONTRAST_LIMITS = (0, 200)
BRIGHTNESS_LIMITS = (-50, 50)
LUMA = (0.2126, 0.7152, 0.0722)


@dataclass(frozen=True)
class ColorValues:
    saturation: int = 100
    contrast: int = 100
    brightness: int = 0

    def __post_init__(self):
        for name, limits in (
            ("saturation", SATURATION_LIMITS),
            ("contrast", CONTRAST_LIMITS),
            ("brightness", BRIGHTNESS_LIMITS),
        ):
            value = getattr(self, name)
            if type(value) is not int or not limits[0] <= value <= limits[1]:
                raise ValueError(f"{name} must be a whole number from {limits[0]} to {limits[1]}")


def identity_matrix() -> tuple[float, ...]:
    return tuple(1.0 if row == column else 0.0 for row in range(5) for column in range(5))


def color_matrix(values: ColorValues) -> tuple[float, ...]:
    """Return a row-major MAGCOLOREFFECT (input rows, output columns).

    Microsoft documents its grayscale example as three identical output
    columns with the input RGB luminance weights down the first three rows.
    This uses those same matrix semantics for saturation, then adds uniform
    contrast and brightness. It is not perceptual, selective "vibrance".
    """
    saturation = values.saturation / 100.0
    contrast = values.contrast / 100.0
    bias = (1.0 - contrast) * 0.5 + values.brightness / 100.0
    matrix = [0.0] * 25
    for source in range(3):
        for output in range(3):
            matrix[source * 5 + output] = contrast * (
                saturation * (1.0 if source == output else 0.0)
                + (1.0 - saturation) * LUMA[source]
            )
    matrix[3 * 5 + 3] = 1.0  # Alpha is unchanged.
    for output in range(3):
        matrix[4 * 5 + output] = bias
    matrix[4 * 5 + 4] = 1.0
    return tuple(matrix)


def matrices_match(left, right, tolerance: float = 1e-5) -> bool:
    return len(left) == len(right) == 25 and all(
        abs(float(a) - float(b)) <= tolerance for a, b in zip(left, right)
    )
