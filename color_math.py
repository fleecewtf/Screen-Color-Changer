"""Small, dependency-free color-matrix calculations for the Windows desktop effect."""

from dataclasses import dataclass
import math


SATURATION_LIMITS = (0, 300)
CONTRAST_LIMITS = (50, 200)
BRIGHTNESS_LIMITS = (-20, 20)
HUE_LIMITS = (-180, 180)
GAMMA_LIMITS = (50, 200)
LUMA = (0.2126, 0.7152, 0.0722)


@dataclass(frozen=True)
class ColorValues:
    saturation: int = 100
    contrast: int = 100
    brightness: int = 0
    hue: int = 0
    gamma: int = 100

    def __post_init__(self):
        for name, limits in (
            ("saturation", SATURATION_LIMITS),
            ("contrast", CONTRAST_LIMITS),
            ("brightness", BRIGHTNESS_LIMITS),
            ("hue", HUE_LIMITS),
            ("gamma", GAMMA_LIMITS),
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
    contrast and brightness. Hue rotates the luminance-free chroma plane;
    the neutral gray axis and Rec.709 luminance stay fixed. Gamma is nonlinear
    and is deliberately not represented by this affine matrix.
    """
    saturation = values.saturation / 100.0
    contrast = values.contrast / 100.0
    bias = (1.0 - contrast) * 0.5 + values.brightness / 100.0
    matrix = [0.0] * 25
    angle = math.radians(values.hue)
    cosine, sine = math.cos(angle), math.sin(angle)
    length = math.sqrt(sum(weight * weight for weight in LUMA))
    normal = tuple(weight / length for weight in LUMA)
    cross = ((0.0, -normal[2], normal[1]),
             (normal[2], 0.0, -normal[0]),
             (-normal[1], normal[0], 0.0))
    cross_neutral = tuple(sum(row) for row in cross)
    for source in range(3):
        for output in range(3):
            hue_chroma = cosine * (1.0 if source == output else 0.0) + sine * (
                cross[output][source] - cross_neutral[output] * LUMA[source]
            )
            matrix[source * 5 + output] = contrast * (
                saturation * hue_chroma + (1.0 - saturation * cosine) * LUMA[source]
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


def gamma_ramp(original, gamma: int) -> tuple[int, ...]:
    """Compose a real power-law gamma curve with each saved calibration LUT.

    Sampling the original ramp at x**(1/gamma) preserves each channel's saved
    calibration and endpoints rather than replacing it with a linear ramp.
    Work from the same original on every update to avoid cumulative changes.
    """
    if type(gamma) is not int or not GAMMA_LIMITS[0] <= gamma <= GAMMA_LIMITS[1]:
        raise ValueError("Gamma must be a whole hundredth from 50 to 200")
    if len(original) != 768 or any(type(value) is not int or not 0 <= value <= 65535
                                   for value in original):
        raise ValueError("A gamma ramp must contain 768 unsigned 16-bit values")
    if gamma == 100:
        return tuple(original)
    result = []
    exponent = 100.0 / gamma
    for channel in range(3):
        offset = channel * 256
        for index in range(256):
            position = (index / 255.0) ** exponent * 255.0
            lower = min(255, int(position))
            upper = min(255, lower + 1)
            fraction = position - lower
            result.append(round(original[offset + lower] * (1.0 - fraction)
                                + original[offset + upper] * fraction))
    return tuple(result)
