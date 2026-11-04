"""Regresión: PatientID, ProtocolName y frames placeholder en la ruta .opt → DICOM.

Datos sintéticos únicamente (tests/fixtures/synthetic_macular.opt); sin PHI.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pydicom
import pytest

from transducin import hot_folder_watcher as hfw
from transducin import revo_opt_reader
from transducin.clinical_data import OCTClinicalData

FIXTURE = Path(__file__).parent / "fixtures" / "synthetic_macular.opt"


def _capture_opt_to_dicom(monkeypatch):
    calls: list[dict] = []

    def fake(path, out_dir, **kw):
        calls.append(kw)
        return []

    monkeypatch.setattr(hfw, "opt_to_dicom", fake)
    return calls


def test_watcher_never_writes_unknown_patient_id(monkeypatch, tmp_path):
    """Sin NOEL ID, PatientID debe ser "" (no el literal "UNKNOWN")."""
    calls = _capture_opt_to_dicom(monkeypatch)
    hfw._convert_opt_to_dcm(FIXTURE, tmp_path, OCTClinicalData(noel_id="", study_type="macular"))
    assert calls[0]["noel_id"] == ""


def test_watcher_passes_noel_id_and_study_type(monkeypatch, tmp_path):
    calls = _capture_opt_to_dicom(monkeypatch)
    cd = OCTClinicalData(
        noel_id="GHSI20000101", study_type="optic_nerve", study_date="20260101", study_time="101010", laterality="L"
    )
    hfw._convert_opt_to_dcm(FIXTURE, tmp_path, cd)
    kw = calls[0]
    assert kw["noel_id"] == "GHSI20000101"
    assert kw["study_type"] == "optic_nerve"
    assert kw["study_time"] == "101010"


def test_dicom_has_patient_id_and_protocol_name(tmp_path):
    cd = OCTClinicalData(noel_id="GHSI20000101", study_type="macular", study_date="20260101", laterality="R")
    paths = hfw._convert_opt_to_dcm(FIXTURE, tmp_path, cd)
    assert paths
    for p in paths:
        ds = pydicom.dcmread(p)
        assert ds.PatientID == "GHSI20000101"
        assert ds.ProtocolName == "macular"


def test_dicom_without_noel_has_empty_patient_id(tmp_path):
    cd = OCTClinicalData(noel_id="", study_type="macular", study_date="20260101", laterality="R")
    ds = pydicom.dcmread(hfw._convert_opt_to_dcm(FIXTURE, tmp_path, cd)[0])
    assert ds.PatientID == ""  # nunca "UNKNOWN" ni derivado del nombre
    assert ds.ProtocolName == "macular"


def test_placeholder_1x1_frame_is_not_written(monkeypatch, tmp_path):
    """Un fundus .opt trae un 'B-scan' de 1×1 px: no debe generar *_OCT.dcm."""
    monkeypatch.setattr(
        revo_opt_reader,
        "read_opt",
        lambda p: {
            "bscans": [np.zeros((1, 1), dtype=np.uint8)],
            "study_uid": "1.2.826.0.1.3680043.8.498.1",
        },
    )
    src = tmp_path / "fundus.opt"
    src.write_bytes(b"x")
    with pytest.raises(ValueError, match="No se generó ningún DICOM"):
        revo_opt_reader.opt_to_dicom(
            src,
            tmp_path / "out",
            noel_id="",
            study_date="20260101",
            laterality="R",
            study_time="101010",
            study_type="fundus",
        )
    assert not list((tmp_path / "out").glob("*_OCT.dcm"))
    # y el watcher lo trata como "sin imagen", sin excepción
    assert (
        hfw._convert_opt_to_dcm(
            src, tmp_path / "out2", OCTClinicalData(study_type="fundus", study_date="20260101", laterality="R")
        )
        == []
    )
