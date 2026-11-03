"""Tests for DeviceAdapter protocol and adapter registry."""
from pathlib import Path

import pytest

from transducin.adapter_registry import REGISTRY, ALL_WATCHED_EXTENSIONS, find_adapter
from transducin.base_adapter import DeviceAdapter


def test_registry_not_empty():
    assert len(REGISTRY) >= 3


def test_all_watched_extensions_includes_expected():
    assert ".opt" in ALL_WATCHED_EXTENSIONS
    assert ".dcm" in ALL_WATCHED_EXTENSIONS
    assert ".pdf" in ALL_WATCHED_EXTENSIONS


def test_registry_adapters_are_device_adapter_instances():
    for adapter in REGISTRY:
        assert isinstance(adapter, DeviceAdapter)


def test_find_adapter_opt():
    adapter = find_adapter(Path("scan.opt"))
    assert adapter is not None
    assert adapter.adapter_id == "revo_opt"


def test_find_adapter_dcm():
    adapter = find_adapter(Path("JAHJ19870831_OD_20260901_OCT.EX.DCM"))
    assert adapter is not None
    assert adapter.adapter_id == "cirrus_dcm"


def test_find_adapter_pdf():
    adapter = find_adapter(Path("report.pdf"))
    assert adapter is not None
    assert adapter.adapter_id == "cirrus_pdf"


def test_find_adapter_unknown_returns_none():
    assert find_adapter(Path("image.png")) is None
    assert find_adapter(Path("data.xlsx")) is None


def test_all_adapters_have_required_properties():
    for adapter in REGISTRY:
        assert isinstance(adapter.vendor, str) and adapter.vendor
        assert isinstance(adapter.extensions, frozenset) and adapter.extensions
        assert isinstance(adapter.adapter_id, str) and adapter.adapter_id


def test_adapter_can_handle_default_uses_extensions():
    adapter = find_adapter(Path("scan.opt"))
    assert adapter is not None
    assert adapter.can_handle(Path("scan.opt"))
    assert not adapter.can_handle(Path("scan.dcm"))
