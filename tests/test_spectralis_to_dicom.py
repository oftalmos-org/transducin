"""SpectralisE2EAdapter.to_dicom con .e2e SINTÉTICO (sin PHI). Ver ADR-027."""

from __future__ import annotations

import numpy as np
import pydicom

from tests._synthetic import build_e2e
from transducin.adapters.spectralis import SpectralisE2EAdapter
from transducin.clinical_data import OCTClinicalData


def _cd(noel_id: str = "", laterality: str = "R") -> OCTClinicalData:
    return OCTClinicalData(
        noel_id=noel_id,
        vendor="heidelberg_spectralis",
        laterality=laterality,
        study_type="macular",
        study_date="20261111",
        study_time="101500",
    )


def _e2e(tmp_path, name="JAHJ19870831_secreto.e2e"):
    raw = np.random.default_rng(3).integers(0, 65536, size=(3, 8, 16), dtype=np.uint32).astype(np.uint16)
    return build_e2e(tmp_path / name, n_b=3, image_raw=raw)


def test_to_dicom_writes_oct_with_images_inside_output_dir(tmp_path):
    src = _e2e(tmp_path)
    out_dir = tmp_path / "out"
    files = SpectralisE2EAdapter().to_dicom(src, _cd("JAHJ19870831"), out_dir)
    assert len(files) == 1 and files[0].parent == out_dir
    ds = pydicom.dcmread(files[0])
    assert ds.Modality == "OPT"
    assert ds.Manufacturer == "Heidelberg Engineering" and ds.ManufacturerModelName == "Spectralis"
    assert int(ds.NumberOfFrames) == 3 and (ds.Rows, ds.Columns) == (8, 16)
    assert ds.pixel_array.shape == (3, 8, 16) and ds.pixel_array.dtype == np.uint8
    assert ds.PatientID == "JAHJ19870831"
    assert ds.Laterality == "R"
    assert ds.ProtocolName == "macular"  # volumen principal


def test_to_dicom_does_not_invent_geometry_or_embed_source_name(tmp_path):
    src = _e2e(tmp_path, "APELLIDO_NOMBRE_secreto.e2e")
    files = SpectralisE2EAdapter().to_dicom(src, _cd(), tmp_path / "o")
    ds = pydicom.dcmread(files[0])
    assert "PixelSpacing" not in ds and "SliceThickness" not in ds
    assert ds.PatientID == ""
    blob = ds.to_json()
    assert "secreto" not in blob and "APELLIDO" not in blob
    assert "ADR-027" in ds.ImageComments


def test_to_dicom_empty_without_images(tmp_path):
    src = build_e2e(tmp_path / "n.e2e", n_b=3)
    assert SpectralisE2EAdapter().to_dicom(src, _cd(), tmp_path / "o") == []


def test_to_dicom_laterality_comes_from_file(tmp_path):
    src = _e2e(tmp_path)
    cd = _cd(laterality="")
    files = SpectralisE2EAdapter().to_dicom(src, cd, tmp_path / "o")
    ds = pydicom.dcmread(files[0])
    assert ds.Laterality == "R"  # la lateralidad del volumen sale del archivo, no de clinical_data
