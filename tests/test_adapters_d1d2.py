"""Tests para adapters D1 (Spectralis .e2e) y D2 (Topcon .fda/.fds).

Sin archivos propietarios de muestra — se usan mocks de oct-converter.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from transducin.adapter_registry import ALL_WATCHED_EXTENSIONS, REGISTRY, find_adapter
from transducin.adapters.spectralis import SpectralisE2EAdapter
from transducin.adapters.topcon import TopconFdaAdapter
from transducin.clinical_data import OCTClinicalData

# ── Propiedades de los adapters ───────────────────────────────────────────────


def test_spectralis_adapter_id():
    assert SpectralisE2EAdapter().adapter_id == "spectralis"


def test_spectralis_vendor():
    assert "Spectralis" in SpectralisE2EAdapter().vendor


def test_spectralis_extensions():
    assert ".e2e" in SpectralisE2EAdapter().extensions


def test_spectralis_can_handle():
    a = SpectralisE2EAdapter()
    assert a.can_handle(Path("scan.e2e"))
    assert a.can_handle(Path("scan.E2E"))
    assert not a.can_handle(Path("scan.opt"))
    assert not a.can_handle(Path("scan.fda"))


def test_topcon_adapter_id():
    assert TopconFdaAdapter().adapter_id == "topcon"


def test_topcon_vendor():
    assert "Topcon" in TopconFdaAdapter().vendor


def test_topcon_extensions():
    a = TopconFdaAdapter()
    assert ".fda" in a.extensions
    assert ".fds" in a.extensions


def test_topcon_can_handle():
    a = TopconFdaAdapter()
    assert a.can_handle(Path("scan.fda"))
    assert a.can_handle(Path("scan.fds"))
    assert a.can_handle(Path("scan.FDA"))
    assert not a.can_handle(Path("scan.e2e"))
    assert not a.can_handle(Path("scan.opt"))


# ── Registro global ──────────────────────────────────────────────────────────


def test_registry_includes_spectralis():
    assert any(a.adapter_id == "spectralis" for a in REGISTRY)


def test_registry_includes_topcon():
    assert any(a.adapter_id == "topcon" for a in REGISTRY)


def test_watched_extensions_includes_e2e():
    assert ".e2e" in ALL_WATCHED_EXTENSIONS


def test_watched_extensions_includes_fda():
    assert ".fda" in ALL_WATCHED_EXTENSIONS


def test_watched_extensions_includes_fds():
    assert ".fds" in ALL_WATCHED_EXTENSIONS


def test_find_adapter_e2e():
    a = find_adapter(Path("scan.e2e"))
    assert a is not None
    assert a.adapter_id == "spectralis"


def test_find_adapter_fda():
    a = find_adapter(Path("JAHJ19870831_OS_scan.fda"))
    assert a is not None
    assert a.adapter_id == "topcon"


def test_find_adapter_fds():
    a = find_adapter(Path("scan.fds"))
    assert a is not None
    assert a.adapter_id == "topcon"


# ── extract() con mock oct-converter ─────────────────────────────────────────


def test_spectralis_extract_with_thickness_map():
    """extract() usa read_thickness_map() cuando está disponible."""
    from unittest.mock import patch as _patch

    mock_map = MagicMock()
    mock_map.laterality = "R"
    mock_map.cmt_um = 265
    mock_map.etdrs_sectors = {
        "C": 265,
        "S1": 340,
        "N1": 320,
        "I1": 330,
        "T1": 310,
        "S2": 290,
        "N2": 270,
        "I2": 280,
        "T2": 260,
    }
    mock_map.rnfl_sectors = {"G": 95, "S": 125, "I": 120, "N": 75, "T": 60}
    mock_map.scan_date = None

    mock_reader = MagicMock()
    mock_reader.read_thickness_map.return_value = [mock_map]
    mock_reader.read_oct_volume.return_value = []

    with _patch("transducin.adapters.spectralis.E2EReader", return_value=mock_reader):
        data = SpectralisE2EAdapter().extract(Path("scan.e2e"))

    assert isinstance(data, OCTClinicalData)
    assert data.cmt_um == 265
    assert data.laterality == "R"
    assert data.etdrs_grid is not None
    assert data.etdrs_grid.C == 265
    assert data.rnfl is not None
    assert data.rnfl.global_avg == 95
    assert data.extraction_confidence == "confirmed"


@patch("transducin.adapters.topcon._topcon_reader")
def test_topcon_extract_with_thickness_map(mock_reader_fn):
    """extract() usa read_thickness_map() cuando está disponible (fork)."""
    mock_map = MagicMock()
    mock_map.laterality = "L"
    mock_map.cmt_um = 280
    mock_map.etdrs_sectors = None
    mock_map.rnfl_sectors = None
    mock_map.scan_date = None

    mock_reader = MagicMock()
    mock_reader.read_thickness_map.return_value = [mock_map]
    mock_reader.read_oct_volume.return_value = []
    mock_reader_fn.return_value = mock_reader

    data = TopconFdaAdapter().extract(Path("scan.fda"))
    assert isinstance(data, OCTClinicalData)
    assert data.cmt_um == 280
    assert data.laterality == "L"


@patch("transducin.adapters.topcon._topcon_reader")
def test_topcon_extract_graceful_without_fork(mock_reader_fn):
    """extract() degrada elegantemente si read_thickness_map() no existe (upstream)."""
    mock_reader = MagicMock(spec=["read_oct_volume"])  # sin read_thickness_map
    vol = MagicMock()
    vol.laterality = "R"
    mock_reader.read_oct_volume.return_value = [vol]
    mock_reader_fn.return_value = mock_reader

    data = TopconFdaAdapter().extract(Path("scan.fda"))
    assert isinstance(data, OCTClinicalData)
    assert data.cmt_um is None  # sin fork no hay medidas
    assert data.laterality == "R"
    assert data.extraction_confidence == "unknown"


# ── to_dicom() ────────────────────────────────────────────────────────────────


def test_spectralis_to_dicom_returns_list(tmp_path):
    """to_dicom() retorna lista (vacía en implementación inicial)."""
    result = SpectralisE2EAdapter().to_dicom(Path("scan.e2e"), OCTClinicalData(), tmp_path)
    assert isinstance(result, list)


def test_topcon_to_dicom_returns_list(tmp_path):
    result = TopconFdaAdapter().to_dicom(Path("scan.fda"), OCTClinicalData(), tmp_path)
    assert isinstance(result, list)
