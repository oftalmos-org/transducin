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
    return build_e2e(tmp_path / name, n_b=3, image_raw=raw, scan_mm=6.0)  # 6.0 mm = 20°: volumen macular


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


def test_watcher_pipeline_emits_image_and_sr_with_device_patient_id(tmp_path):
    """Hot folder: .e2e → DICOM de imagen + SR en el mismo estudio, sin NOEL ID (ADR-026)."""
    import numpy as np

    from tests._synthetic import thickness_profile
    from transducin.hot_folder_watcher import process_spectralis_file

    (tmp_path / "in").mkdir()
    raw = np.random.default_rng(5).integers(0, 65536, size=(25, 8, 256), dtype=np.uint32).astype(np.uint16)
    src = build_e2e(
        tmp_path / "in" / "estudio.e2e",
        laterality="L",
        thick_um=thickness_profile(25, 256, 250.0, grad_um=120.0),
        image_raw=raw,
        patient_id="DEV-0042",
    )
    out = tmp_path / "out"
    assert process_spectralis_file(src, out, do_cstore=False) is True
    imgs = sorted((out / "images").glob("*.dcm"))
    srs = sorted((out / "sr").glob("*.dcm"))
    assert len(imgs) == 1 and len(srs) == 1
    img, sr = pydicom.dcmread(imgs[0]), pydicom.dcmread(srs[0])
    assert img.PatientID == sr.PatientID == "DEV-0042"
    assert img.StudyInstanceUID == sr.StudyInstanceUID
    assert sr.Manufacturer == "Heidelberg Engineering" and sr.ManufacturerModelName == "Spectralis"


def test_infer_study_type_by_angular_extent():
    from transducin.adapters.spectralis import infer_study_type

    onh = [{"posX1": 0.0, "posX2": 14.97}] * 3  # 15° x 15°: nervio óptico
    macula = [{"posX1": 0.0, "posX2": 20.0}] * 3
    wide = [{"posX1": 0.0, "posX2": 29.7}] * 3
    assert infer_study_type(onh) == "optic_nerve"
    assert infer_study_type(macula) == "macular"
    assert infer_study_type(wide) == "macular"
    assert infer_study_type([]) == ""


def test_to_dicom_labels_15_degree_volume_as_optic_nerve(tmp_path):
    raw = np.random.default_rng(4).integers(0, 65536, size=(3, 8, 16), dtype=np.uint32).astype(np.uint16)
    src = build_e2e(tmp_path / "onh.e2e", n_b=3, image_raw=raw, scan_mm=4.5)  # 4.5 mm = 15°
    ds = pydicom.dcmread(SpectralisE2EAdapter().to_dicom(src, _cd(), tmp_path / "o")[0])
    assert ds.ProtocolName == "optic_nerve"
    assert "Optic Nerve" in ds.StudyDescription
    assert ds.AnatomicRegionSequence[0].CodeValue == "T-AA630"


def test_extract_all_one_measurement_per_eye_and_reference_matches_eye(tmp_path):
    """extract_all da una medición por ojo; pick_reference elige la imagen de ese ojo."""
    from tests._synthetic import thickness_profile
    from transducin.sr_reference import pick_reference

    raw = np.random.default_rng(6).integers(0, 65536, size=(25, 8, 256), dtype=np.uint32).astype(np.uint16)
    src = build_e2e(
        tmp_path / "e.e2e",
        laterality="L",
        thick_um=thickness_profile(25, 256, 250.0, grad_um=120.0),
        image_raw=raw,
        patient_id="DEV-1",
        scan_mm=6.0,
    )
    adapter = SpectralisE2EAdapter()
    cds = adapter.extract_all(src)
    assert [c.laterality for c in cds] == ["L"]
    assert cds[0].patient_id == "DEV-1" and cds[0].extraction_confidence == "confirmed"  # mapa contiguo documentado
    files = adapter.to_dicom(src, cds[0], tmp_path / "o")
    ref = pick_reference(files, "L", "macular")
    assert ref is not None and ref.Laterality == "L"
    assert pick_reference([], "L") is None


def test_layer_contours_roundtrip_and_attached_to_image(tmp_path):
    """Los contornos embebidos viajan en el DICOM de imagen (ADR-028) y se decodifican igual."""
    from tests._synthetic import thickness_profile
    from transducin.layer_contours import decode_layers, encode_layers

    raw = np.random.default_rng(7).integers(0, 65536, size=(25, 8, 256), dtype=np.uint32).astype(np.uint16)
    src = build_e2e(
        tmp_path / "e.e2e",
        laterality="R",
        thick_um=thickness_profile(25, 256, 250.0, grad_um=120.0),
        image_raw=raw,
        scan_mm=6.0,
    )
    ds = pydicom.dcmread(SpectralisE2EAdapter().to_dicom(src, _cd(), tmp_path / "o")[0])
    meta, arr = decode_layers(ds)
    assert meta["schema"] == 1 and meta["n_frames"] == 25 and arr.shape[:2] == (meta["n_layers"], 25)
    keys = [layer["key"] for layer in meta["layers"]]
    assert "contour0" in keys and "contour9" in keys  # ILM y BM del mapa contiguo documentado
    by = {layer["key"]: layer for layer in meta["layers"]}
    assert by["contour0"]["label"] == "ILM" and by["contour0"]["status"] == "documented"
    assert np.isfinite(arr).any()

    # roundtrip directo, con NaN y estado desconocido
    enc = encode_layers({"contour3": [np.array([1.0, np.nan, 3.0], dtype=np.float32), None]}, 2)
    from pydicom.dataset import Dataset

    from transducin.layer_contours import attach_layers

    d = Dataset()
    assert attach_layers(d, enc) is True
    m2, a2 = decode_layers(d)
    assert a2.shape == (1, 2, 3) and np.isnan(a2[0, 0, 1]) and np.isnan(a2[0, 1]).all()
    assert m2["layers"][0]["status"] == "unknown"
    assert attach_layers(Dataset(), None) is False
