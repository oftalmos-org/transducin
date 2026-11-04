"""Regresión con el .OCT Bioptigen REAL de MATLAB Central (corpus local, fuera de Git)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.real_data

_ROOT = os.environ.get("TRANSDUCIN_REAL_DATA_DIR")
OCT = Path(_ROOT, "public/bioptigen/native_oct/matlabcentral_bioptigen_Z506_OS_V_1x1.OCT") if _ROOT else None

needs_oct = pytest.mark.skipif(not (OCT and OCT.exists()), reason="falta el .OCT real (TRANSDUCIN_REAL_DATA_DIR)")


@needs_oct
def test_real_bioptigen_conversion_flags_uncalibrated_geometry(tmp_path):
    pytest.importorskip("oct_converter")
    import pydicom

    from transducin.boct_converter import convert_directory

    ok, fail = convert_directory(str(OCT.parent), str(tmp_path))
    assert (ok, fail) == (1, 0)
    ds = pydicom.dcmread(next(tmp_path.glob("*.dcm")), stop_before_pixels=True)
    assert (ds.Rows, ds.Columns, int(ds.NumberOfFrames)) == (1000, 1024, 100)
    assert ds.ImageLaterality == "L"  # token OS aislado en el nombre; se declara como deducida
    assert "deducida del nombre" in ds.ImageComments and "SIN CALIBRAR" in ds.ImageComments
    assert str(ds.PatientID) == ""  # no se filtra el nombre del archivo como PatientID
