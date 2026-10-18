"""Core executable equality cannot hide a missing dependency resource."""

import importlib
import os
from pathlib import Path
import shutil

import pytest


@pytest.fixture
def program(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    module = importlib.import_module("program_inventory")
    original = tmp_path / "package"
    original.mkdir()
    (original / "CompanionStudio.exe").write_bytes(b"synthetic executable")
    resource = original / "resources" / "format-annotation"
    resource.parent.mkdir()
    resource.write_bytes(b"synthetic dependency resource")
    copy = tmp_path / "installed"
    shutil.copytree(original, copy)
    return module, original, copy


@pytest.mark.parametrize("mutation", ["missing", "changed", "extra"])
def test_missing_changed_or_extra_resource_rejects_partial_copy(program, mutation):
    module, original, copy = program
    target = copy / "resources/format-annotation"
    if mutation == "missing":
        target.unlink()
    elif mutation == "changed":
        target.write_bytes(b"modified resource")
    else:
        (copy / "private.db").write_bytes(b"must not be redistributed")
    assert (original / "CompanionStudio.exe").read_bytes() == (
        copy / "CompanionStudio.exe"
    ).read_bytes()
    with pytest.raises(ValueError, match="Program resource mismatch"):
        module.compare(module.inventory(original), module.inventory(copy))


def test_only_exact_root_uninstaller_is_permitted(program):
    module, original, copy = program
    (copy / "Uninstall CompanionStudio.exe").write_bytes(b"synthetic uninstaller")
    module.compare(module.inventory(original), module.inventory(copy), allow_uninstaller=True)
    (copy / "resources/Uninstall CompanionStudio.exe").write_bytes(b"unexpected nested file")
    with pytest.raises(ValueError, match="Program resource mismatch"):
        module.compare(module.inventory(original), module.inventory(copy), allow_uninstaller=True)


@pytest.mark.skipif(os.name != "nt", reason="Windows path boundary")
def test_resource_beyond_classic_max_path_is_included(program):
    module, original, _ = program
    relative = (
        Path("resources") / ("segment" * 12) / ("nested" * 12) / ("vocab" * 8) / "format-annotation"
    )
    target = original / relative
    Path(module.native(target.parent)).mkdir(parents=True)
    Path(module.native(target)).write_bytes(b"long path resource")
    assert len(str(target)) > 260
    assert relative.as_posix() in module.inventory(original)
