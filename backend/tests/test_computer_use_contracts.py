from uuid import uuid4

from pydantic import ValidationError
import pytest

from app.services.computer_use.contracts import ExplicitAction, Rectangle, ComputerUseError
from app.services.computer_use.geometry import image_to_physical, intersect, physical_to_dip


def payload(operation="observe", **arguments):
    return {
        "conversation_id": str(uuid4()),
        "character_id": "assistant",
        "operation": operation,
        "arguments": {"target_id": str(uuid4()), **arguments},
    }


def test_no_raw_coordinate_or_script_interface_and_side_effect_needs_fresh_evidence():
    for unsafe in (
        {"x": 10, "y": 20},
        {"script": "ignored"},
        {"handle": 123},
        {"executable": "example.exe"},
    ):
        with pytest.raises(ValidationError):
            ExplicitAction.model_validate(payload(**unsafe))
    for operation in ("click", "type_text", "scroll"):
        with pytest.raises(ValidationError):
            ExplicitAction.model_validate(payload(operation))
    with pytest.raises(ValidationError):
        ExplicitAction.model_validate(
            payload("click", observation_id=str(uuid4()), candidate_id=str(uuid4()))
        )
    valid = payload(
        "click",
        observation_id=str(uuid4()),
        candidate_id=str(uuid4()),
        verification={"kind": "element_present", "automation_id": "result"},
    )
    assert ExplicitAction.model_validate(valid).operation == "click"


def test_negative_monitor_origin_and_scaled_image_are_not_primary_screen_coordinates():
    capture = Rectangle(x=-1920, y=-300, width=1200, height=600)
    mapped = image_to_physical(Rectangle(x=100, y=20, width=50, height=30), capture, 600, 300)
    assert mapped.model_dump() == {"x": -1720, "y": -260, "width": 100, "height": 60}
    for dpi in (96, 120, 144):
        dip = physical_to_dip(mapped, dpi)
        assert dip.width * dpi / 96 == pytest.approx(mapped.width)
    assert intersect(capture, Rectangle(x=-1000, y=-500, width=500, height=700)).width == 280


def test_invalid_mapping_rejects_infinite_nan_outside_and_disconnected_display():
    box = Rectangle(x=0, y=0, width=10, height=10)
    for dpi in (float("nan"), float("inf"), 0, True):
        with pytest.raises(ComputerUseError):
            physical_to_dip(box, dpi)
    with pytest.raises(ComputerUseError):
        image_to_physical(Rectangle(x=95, y=0, width=10, height=10), box, 100, 100)
    with pytest.raises(ComputerUseError):
        intersect(box, Rectangle(x=100, y=100, width=10, height=10))
