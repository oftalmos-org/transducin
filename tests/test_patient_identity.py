"""ADR-026 fase 1: identidad de paciente por alias (NOEL ID opcional).

Datos sintéticos únicamente (tests/fixtures/synthetic_macular.opt y archivos
.e2e/.fda generados por tests/_synthetic.py); sin PHI.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pydicom
import pytest

from tests._synthetic import build_e2e, build_fda
from transducin.adapters.revo import RevoOptAdapter
from transducin.adapters.spectralis import SpectralisE2EAdapter
from transducin.adapters.topcon import TopconFdaAdapter
from transducin.clinical_data import OCTClinicalData
from transducin.readers._layers import clean_source_patient_id
from transducin.readers.e2e_reader import E2EReader
from transducin.readers.fda_reader import FDAReader
from transducin.sr_builder import build_sr

FIXTURE = Path(__file__).parent / "fixtures" / "synthetic_macular.opt"
NOEL = "SYNT19000101"
NOEL_NAME = f"{NOEL}_SINTETICO_PRUEBA_20260101_120000_OCT_OD.opt"
DEVICE_ID = "MRN-0042/b"


def _pipeline(src: Path, out: Path, mutate=None):
    """extract → to_dicom → build_sr, como lo hace el conversor de Gass (sin watcher)."""
    adapter = RevoOptAdapter()
    cd = adapter.extract(src)
    if mutate:
        mutate(cd)
    images = [pydicom.dcmread(p) for p in adapter.to_dicom(src, cd, out / "images")]
    assert images, "la fixture sintética debe producir al menos una imagen"
    assert cd.has_measurements(), "la fixture sintética debe traer mediciones"
    ref = images[0]
    sr_path = out / "sr" / "SR.dcm"
    build_sr(cd, reference_dataset=ref, output_path=sr_path, study_instance_uid=str(ref.StudyInstanceUID))
    return cd, images, pydicom.dcmread(sr_path)


# ── Modelo ────────────────────────────────────────────────────────────────────


def test_patient_id_resolution_order():
    assert OCTClinicalData().patient_id == ""
    assert OCTClinicalData().patient_id_source == "none"

    dev = OCTClinicalData(device_patient_id=DEVICE_ID)
    assert (dev.patient_id, dev.patient_id_source) == (DEVICE_ID, "device")

    fn = OCTClinicalData(device_patient_id="A-17", device_patient_id_source="filename")
    assert (fn.patient_id, fn.patient_id_source) == ("A-17", "filename")

    both = OCTClinicalData(noel_id=NOEL, device_patient_id=DEVICE_ID)
    assert (both.patient_id, both.patient_id_source) == (NOEL, "noel")


def test_patient_id_follows_noel_assigned_after_extract():
    """Watchers y conversores asignan noel_id después de extract(): patient_id no queda desfasado."""
    cd = OCTClinicalData(device_patient_id=DEVICE_ID)
    cd.noel_id = NOEL
    assert (cd.patient_id, cd.patient_id_source) == (NOEL, "noel")
    cd.noel_id = ""
    assert (cd.patient_id, cd.patient_id_source) == (DEVICE_ID, "device")


def test_patient_id_is_never_derived_from_name():
    cd = OCTClinicalData(patient_name="SYNTHETIC_PATIENT", patient_dob="19000101")
    assert cd.patient_id == ""
    assert cd.patient_id_source == "none"


# ── (a) Con NOEL ID: mismo resultado que antes ────────────────────────────────


def test_noel_file_same_result_as_before(tmp_path):
    src = tmp_path / NOEL_NAME
    shutil.copy(FIXTURE, src)
    cd, images, sr = _pipeline(src, tmp_path)

    assert cd.noel_id == NOEL
    assert (cd.patient_id, cd.patient_id_source) == (NOEL, "noel")
    for ds in [*images, sr]:
        assert str(ds.PatientID) == NOEL
        assert ds.PatientBirthDate == "19000101"
    assert sr.Modality == "SR"
    assert sr.StudyInstanceUID == images[0].StudyInstanceUID


def test_build_sr_with_noel_and_no_name_falls_back_to_noel(tmp_path):
    """Comportamiento previo conservado: sin nombre, PatientName cae al alias NOEL."""
    cd = OCTClinicalData(noel_id=NOEL, laterality="R", study_type="macular", study_date="20260101", cmt_um=250.0)
    sr = build_sr(cd)
    assert str(sr.PatientID) == NOEL
    assert str(sr.PatientName) == NOEL
    assert sr.PatientBirthDate == "19000101"


# ── (b) Sin identificador, con mediciones: imagen + SR, PatientID vacío ───────


def test_no_identifier_yields_image_and_sr_in_same_study_with_empty_patient_id(tmp_path):
    src = tmp_path / "synthetic_macular.opt"
    shutil.copy(FIXTURE, src)
    cd, images, sr = _pipeline(src, tmp_path)

    assert cd.noel_id == ""
    assert (cd.patient_id, cd.patient_id_source) == ("", "none")
    for ds in [*images, sr]:
        assert "PatientID" in ds  # Tipo 2: presente aunque vacío
        assert str(ds.PatientID) == ""
        assert not ds.PatientBirthDate
    assert sr.Modality == "SR"
    assert sr.StudyInstanceUID == images[0].StudyInstanceUID


def test_build_sr_without_identifier_does_not_raise_or_invent(tmp_path):
    cd = OCTClinicalData(
        laterality="L",
        study_type="macular",
        study_date="20260101",
        cmt_um=250.0,
        patient_name="SYNTHETIC_PATIENT",
        patient_dob="19000101",
    )
    sr = build_sr(cd, output_path=tmp_path / "SR.dcm")
    assert str(sr.PatientID) == ""
    assert sr.PatientBirthDate == "19000101"  # dato de origen, no derivado


# ── (c) Identificador de equipo no-NOEL: se conserva tal cual ─────────────────


def test_device_identifier_is_kept_verbatim_in_image_and_sr(tmp_path):
    src = tmp_path / "synthetic_macular.opt"
    shutil.copy(FIXTURE, src)

    def set_device_id(cd):
        cd.device_patient_id = DEVICE_ID

    cd, images, sr = _pipeline(src, tmp_path, mutate=set_device_id)

    assert (cd.patient_id, cd.patient_id_source) == (DEVICE_ID, "device")
    for ds in [*images, sr]:
        assert str(ds.PatientID) == DEVICE_ID
        assert not ds.PatientBirthDate
        assert str(ds.PatientName) == ""  # el identificador de la fuente no se usa como nombre
    assert sr.StudyInstanceUID == images[0].StudyInstanceUID


def test_revo_adapter_keeps_image_and_sr_consistent_when_filename_parser_misses_noel_prefix(tmp_path):
    src = tmp_path / f"{NOEL}_x.opt"
    shutil.copy(FIXTURE, src)
    adapter = RevoOptAdapter()
    cd = adapter.extract(src)
    images = [pydicom.dcmread(p) for p in adapter.to_dicom(src, cd, tmp_path / "images")]
    assert (cd.patient_id, cd.patient_id_source) == (NOEL, "noel")
    assert images and all(str(ds.PatientID) == NOEL for ds in images)


def test_birth_date_not_derived_from_noel_shaped_device_identifier():
    """Un identificador de equipo con forma de NOEL no es un NOEL: no aporta fecha ni nombre."""
    cd = OCTClinicalData(device_patient_id="ABCD19991231", laterality="R", study_type="macular", cmt_um=250.0)
    sr = build_sr(cd)
    assert str(sr.PatientID) == "ABCD19991231"
    assert not sr.PatientBirthDate
    assert str(sr.PatientName) == ""


@pytest.mark.parametrize(
    ("builder", "reader_cls", "adapter_cls", "name"),
    [
        (build_e2e, E2EReader, SpectralisE2EAdapter, "s.e2e"),
        (build_fda, FDAReader, TopconFdaAdapter, "t.fda"),
    ],
)
def test_spectralis_topcon_keep_non_noel_device_identifier(tmp_path, builder, reader_cls, adapter_cls, name):
    path = builder(tmp_path / name, patient_id=DEVICE_ID)
    assert reader_cls(path).read_patient_id() == DEVICE_ID

    cd = adapter_cls().extract(path)
    assert cd.noel_id == ""
    assert (cd.patient_id, cd.patient_id_source) == (DEVICE_ID, "device")
    assert cd.patient_name == ""  # nombre y fecha de nacimiento del archivo no se leen
    assert cd.patient_dob == ""
    assert cd.has_measurements()

    sr = build_sr(cd)
    assert str(sr.PatientID) == DEVICE_ID
    assert not sr.PatientBirthDate


@pytest.mark.parametrize(
    ("builder", "adapter_cls", "name"),
    [(build_e2e, SpectralisE2EAdapter, "s.e2e"), (build_fda, TopconFdaAdapter, "t.fda")],
)
def test_spectralis_topcon_without_patient_chunk_stay_unidentified(tmp_path, builder, adapter_cls, name):
    cd = adapter_cls().extract(builder(tmp_path / name))
    assert (cd.patient_id, cd.patient_id_source) == ("", "none")
    assert str(build_sr(cd).PatientID) == ""


def test_explicit_noel_wins_over_device_identifier(tmp_path):
    """El llamador (watcher, conversor) asigna el NOEL del prefijo del archivo: tiene prioridad."""
    cd = SpectralisE2EAdapter().extract(build_e2e(tmp_path / "s.e2e", patient_id=DEVICE_ID))
    cd.noel_id = NOEL
    sr = build_sr(cd)
    assert str(sr.PatientID) == NOEL
    assert sr.PatientBirthDate == "19000101"


def test_clean_source_patient_id():
    assert clean_source_patient_id(b"MRN-0042\x00\x00garbage") == "MRN-0042"
    assert clean_source_patient_id(b"  00123  \x00") == "00123"  # sin relleno; ceros a la izquierda intactos
    assert clean_source_patient_id(b"\x00" * 25) == ""
    assert clean_source_patient_id(b"   ") == ""
    assert clean_source_patient_id(b"AB\x01CD") == ""  # no imprimible
    assert clean_source_patient_id(b"\xff\xfe12") == ""  # no ASCII
    assert clean_source_patient_id(b"A\\B") == ""  # la barra invertida separa valores en DICOM
