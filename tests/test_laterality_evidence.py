"""Pruebas del modelo de evidencia de lateralidad. Datos sintéticos únicamente."""

from __future__ import annotations

import pydicom
import pytest
from pydicom.dataset import Dataset
from pydicom.sequence import Sequence

import transducin.laterality_evidence as le
from transducin.clinical_data import OCTClinicalData
from transducin.laterality_evidence import Evidence, evaluate, from_dicom_dataset, from_filename, normalize


def _ds(**kw) -> Dataset:
    ds = Dataset()
    for k, v in kw.items():
        setattr(ds, k, v)
    return ds


def test_normalize():
    assert [normalize(x) for x in ("OD", "r", "RIGHT", "OS", "l", "Left", "OU", "B", "", None, "xx")] == [
        "R",
        "R",
        "R",
        "L",
        "L",
        "L",
        "B",
        "B",
        None,
        None,
        None,
    ]


# ── Nombre de archivo (fuente débil) ──────────────────────────────────────────


def test_filename_token_is_weak_and_strict():
    e = from_filename("sujeto01_OD_20260101.dcm")
    assert len(e) == 1 and e[0].value == "R" and e[0].strength == le.WEAK
    assert from_filename("sujeto_ODONTO_20260101.dcm") == []  # fragmento ambiguo: no es token aislado
    assert from_filename("sujeto_ROD_20260101.dcm") == []
    assert from_filename("x_OD_OS_y.dcm")[0].value is None  # ambos tokens → no interpretable
    assert from_filename("sin_lateralidad.dcm") == []


# ── DICOM: mismo origen no es evidencia independiente ─────────────────────────


def test_dicom_standard_attributes_are_one_origin_group():
    ev = from_dicom_dataset(_ds(Laterality="R", ImageLaterality="R"))
    assert {e.group for e in ev} == {"device_dicom_header"} and len(ev) == 2
    r = evaluate(ev, "R")
    assert r["state"] == "declared" and r["n_groups"] == 1
    assert r["concordant_multi_group"] is False and r["concordant_independent"] is False  # no suman como pruebas


def test_dicom_internal_disagreement_is_intra_group_conflict():
    r = evaluate(from_dicom_dataset(_ds(Laterality="R", ImageLaterality="L")), "R")
    assert r["state"] == "conflict" and r["conflict_kind"] == "intra_group"
    assert r["conflict_groups"] == ["device_dicom_header"]


def test_frame_laterality_in_functional_groups():
    fa = Dataset()
    fa.FrameLaterality = "L"
    item = Dataset()
    item.FrameAnatomySequence = Sequence([fa])
    ds = Dataset()
    ds.PerFrameFunctionalGroupsSequence = Sequence([item])
    ev = from_dicom_dataset(ds)
    assert [(e.source, e.value) for e in ev] == [("dicom:0020,9072", "L")]


def test_cirrus_private_tags():
    ds = Dataset()
    ds.add_new((0x0063, 0x1005), "FL", 3.0)
    ds.add_new((0x0057, 0x1015), "LO", "right eye")
    ev = from_dicom_dataset(ds)
    by = {e.source: e.value for e in ev}
    assert by["dicom:0063,1005"] == "R"
    assert by["dicom:0057,1015"] is None  # texto libre no interpretable → no se adivina


# ── Evaluación de estados ─────────────────────────────────────────────────────


def _e(group, value, source="s"):
    return Evidence(source, group, value, "")


def test_states():
    assert evaluate([], "")["state"] == "unknown"
    assert evaluate([_e("filename", "R")], "R")["state"] == "inferred"
    assert evaluate([_e("vendor_native", "L")], "L")["state"] == "declared"
    assert evaluate([_e("physician_confirmed", "L"), _e("vendor_native", "L")], "L")["state"] == "confirmed"


def test_concordance_between_independent_groups():
    r = evaluate([_e("filename", "R"), _e("acquisition_geometry", "R")], "R")
    assert r["state"] == "declared" and r["concordant_multi_group"] is True
    assert r["concordant_independent"] is False  # el nombre de archivo es débil
    r2 = evaluate([_e("vendor_native", "R"), _e("acquisition_geometry", "R")], "R")
    assert r2["concordant_independent"] is True and r2["confirmed_by_reference"] is False


def test_conflict_is_not_called_wrong_eye():
    r = evaluate([_e("filename", "R"), _e("acquisition_geometry", "L")], "R")
    assert r["state"] == "conflict" and r["conflict_kind"] == "inter_group"
    assert r["emitted_vs_evidence"] == "no_consensus"
    assert "wrong" not in " ".join(r) and "equivocad" not in str(r)  # nunca se rotula como error real


def test_physician_contradiction_stays_a_conflict():
    r = evaluate([_e("physician_confirmed", "L"), _e("vendor_native", "R")], "R")
    assert r["state"] == "conflict" and r["confirmed_by_reference"] is False


def test_emitted_vs_evidence():
    assert evaluate([_e("vendor_native", "L")], "R")["emitted_vs_evidence"] == "mismatch"
    assert evaluate([_e("vendor_native", "L")], "L")["emitted_vs_evidence"] == "match"
    assert evaluate([_e("vendor_native", "L")], "")["emitted_vs_evidence"] == "emitted_empty"


def test_both_eyes_is_not_a_lateral_claim():
    r = evaluate([_e("filename", "B"), _e("vendor_native", "R")], "R")
    assert r["state"] == "declared" and r["conflict"] is False


# ── Recolección por ruta ──────────────────────────────────────────────────────


def test_collect_cirrus_dcm_reads_header_only(tmp_path):
    ds = pydicom.Dataset()
    ds.file_meta = pydicom.dataset.FileMetaDataset()
    ds.file_meta.TransferSyntaxUID = pydicom.uid.ExplicitVRLittleEndian
    ds.file_meta.MediaStorageSOPClassUID = pydicom.uid.generate_uid()
    ds.file_meta.MediaStorageSOPInstanceUID = pydicom.uid.generate_uid()
    ds.SOPClassUID = ds.file_meta.MediaStorageSOPClassUID
    ds.SOPInstanceUID = ds.file_meta.MediaStorageSOPInstanceUID
    ds.Laterality = "L"
    ds.ImageLaterality = "L"
    p = tmp_path / "sujeto01_OS_x.dcm"
    pydicom.dcmwrite(str(p), ds, enforce_file_format=True)
    d = OCTClinicalData(vendor="zeiss_cirrus", source_file=str(p), laterality="L")
    ev, un = le.collect(d, "cirrus_dcm")
    r = evaluate(ev, "L", un)
    assert {e.group for e in ev} == {"device_dicom_header", "filename"}
    assert r["state"] == "declared" and r["concordant_multi_group"] and r["emitted_vs_evidence"] == "match"
    assert un == []


def test_collect_missing_file_is_reported_not_guessed():
    d = OCTClinicalData(vendor="zeiss_cirrus", source_file="/no/existe/x_OD.dcm", laterality="R")
    ev, un = le.collect(d, "cirrus_dcm")
    assert un == ["file_unavailable"] and [e.group for e in ev] == ["filename"]


def _revo(tmp_path, name):
    p = tmp_path / name
    p.write_bytes(b"x")
    return OCTClinicalData(vendor="optopol_revo", source_file=str(p), laterality="R")


_REVO_OD = "APELLIDOPRUEBA_NOMBREPRUEBA_20260101_101010_OD_OCT.opt"


def test_revo_filename_and_tag23_concordant(tmp_path, monkeypatch):
    monkeypatch.setattr(le, "_read_revo_tag23", lambda p: -4.0)  # negativo = OD
    ev, un = le.collect(_revo(tmp_path, _REVO_OD), "revo_opt")
    r = evaluate(ev, "R", un)
    assert {e.group for e in ev} == {"filename", "acquisition_geometry"}
    assert r["concordant_multi_group"] and not r["conflict"]
    assert all("APELLIDO" not in str(e) for e in ev)  # sin PHI en la evidencia


def test_revo_filename_vs_tag23_conflict(tmp_path, monkeypatch):
    monkeypatch.setattr(le, "_read_revo_tag23", lambda p: 4.0)  # positivo = OS
    ev, un = le.collect(_revo(tmp_path, _REVO_OD), "revo_opt")
    r = evaluate(ev, "R", un)
    assert r["state"] == "conflict" and r["conflict_kind"] == "inter_group"


def test_revo_tag23_absent_or_zero_adds_no_evidence(tmp_path, monkeypatch):
    for x in (None, 0.0):
        le._cache.clear()
        monkeypatch.setattr(le, "_read_revo_tag23", lambda p, x=x: x)
        ev, _ = le.collect(_revo(tmp_path, _REVO_OD), "revo_opt")
        assert {e.group for e in ev} == {"filename"}


def test_revo_large_file_is_skipped(tmp_path, monkeypatch):
    monkeypatch.setenv("TRANSDUCIN_SHADOW_MAX_READ_MB", "0")
    called = []
    monkeypatch.setattr(le, "_read_revo_tag23", lambda p: called.append(1) or 1.0)
    _, un = le.collect(_revo(tmp_path, _REVO_OD), "revo_opt")
    assert un == ["file_too_large_for_shadow_read"] and not called


def test_revo_read_error_is_not_fatal(tmp_path, monkeypatch):
    le._cache.clear()

    def boom(p):
        raise ValueError("corrupto")

    monkeypatch.setattr(le, "_read_revo_tag23", boom)
    ev, un = le.collect(_revo(tmp_path, _REVO_OD), "revo_opt")
    assert un == ["octparams_unreadable"] and {e.group for e in ev} == {"filename"}


def test_native_routes_record_single_native_origin():
    d = OCTClinicalData(vendor="heidelberg_spectralis", source_file="", laterality="L")
    ev, _ = le.collect(d, "spectralis_e2e")
    assert [(e.group, e.value) for e in ev] == [("vendor_native", "L")]
    assert evaluate(ev, "L")["concordant_independent"] is False  # un solo dato original


@pytest.mark.parametrize("val", ["", None])
def test_no_laterality_means_unknown(val):
    d = OCTClinicalData(vendor="topcon_dri_oct", source_file="", laterality=val or "")
    ev, _ = le.collect(d, "topcon_fda")
    assert evaluate(ev, "")["state"] == "unknown"
