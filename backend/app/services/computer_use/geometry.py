"""Coordinate-space arithmetic only; these functions do not prove a real DPI test."""

import math

from app.services.computer_use.contracts import ComputerUseError, Rectangle


def intersect(first: Rectangle, second: Rectangle):
    left, top = max(first.x, second.x), max(first.y, second.y)
    right = min(first.x + first.width, second.x + second.width)
    bottom = min(first.y + first.height, second.y + second.height)
    if right <= left or bottom <= top:
        raise ComputerUseError("DPI_MAPPING_INVALID")
    return Rectangle(x=left, y=top, width=right - left, height=bottom - top)


def image_to_physical(box: Rectangle, capture: Rectangle, width: int, height: int):
    if (
        type(width) is not int
        or type(height) is not int
        or not (1 <= width <= 4096 and 1 <= height <= 4096)
    ):
        raise ComputerUseError("DPI_MAPPING_INVALID")
    if box.x < 0 or box.y < 0 or box.x + box.width > width or box.y + box.height > height:
        raise ComputerUseError("DPI_MAPPING_INVALID")
    return Rectangle(
        x=capture.x + box.x * capture.width / width,
        y=capture.y + box.y * capture.height / height,
        width=box.width * capture.width / width,
        height=box.height * capture.height / height,
    )


def physical_to_dip(box: Rectangle, dpi: float):
    if isinstance(dpi, bool) or not math.isfinite(dpi) or not 48 <= dpi <= 768:
        raise ComputerUseError("DPI_MAPPING_INVALID")
    # Each monitor/window has its own scale. There is no global primary origin.
    return Rectangle(**{key: value * 96 / dpi for key, value in box.model_dump().items()})
