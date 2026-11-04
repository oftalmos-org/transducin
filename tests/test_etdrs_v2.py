"""ETDRS v2: geometría, áreas, cuadrantes de 90°, orientación, escala física, centro y cobertura por sector."""

from __future__ import annotations

import numpy as np
import pytest

from transducin.etdrs import (
    LEGACY_ASSUMED,
    REGIONS,
    Center,
    Orientation,
    compute_etdrs_v2,
    find_foveal_candidate,
    geometric_center,
    region_masks,
)
from transducin.etdrs_policy import EtdrsCoveragePolicy

# Orientaciones de prueba explícitas (no dependen de las presets de los equipos)
SUP_TOP_NASAL_RIGHT = Orientation("superior", True, True, True, "prueba: B-scan 0 superior, A-scan crece hacia nasal")
SUP_BOTTOM_NASAL_RIGHT = Orientation("inferior", True, True, True, "prueba: B-scan 0 inferior")
SUP_TOP_NASAL_LEFT = Orientation("superior", False, False, True, "prueba: A-scan crece hacia temporal")

PX = 10.0  # µm por píxel en los fixtures sintéticos
N = 801  # ±4 mm alrededor del centro (400, 400)


def _center():
    return Center(400.0, 400.0, "geometric")


def _masks(orient=SUP_TOP_NASAL_RIGHT, lat="R", n=N, px=PX, center=None):
    return region_masks((n, n), px, px, lat, orient, center or Center((n - 1) / 2, (n - 1) / 2, "geometric"))


def test_nine_regions_are_disjoint_and_cover_the_disc_exactly():
    m = _masks()
    stack = np.stack([m[r] for r in REGIONS])
    assert stack.sum(axis=0).max() == 1  # ningún píxel en dos regiones (el legacy solapa)
    rows, cols = np.arange(N)[:, None], np.arange(N)[None, :]
    r = np.hypot((rows - 400) * PX, (cols - 400) * PX)
    assert np.array_equal(stack.any(axis=0), r < 3000.0)  # unión = disco de 6 mm


def test_region_areas_match_the_analytic_geometry():
    m = _masks()
    px_mm2 = (PX / 1000.0) ** 2
    expected = {"C": np.pi * 0.5**2}
    for q in "SNIT":
        expected[q + "1"] = np.pi * (1.5**2 - 0.5**2) / 4
        expected[q + "2"] = np.pi * (3.0**2 - 1.5**2) / 4
    for name, area in expected.items():
        assert m[name].sum() * px_mm2 == pytest.approx(area, rel=0.01), name


def test_each_quadrant_spans_90_degrees_not_a_half_ring():
    m = _masks()
    for ring in "12":
        areas = [m[q + ring].sum() for q in "SNIT"]
        assert max(areas) / min(areas) < 1.02  # cuatro cuñas iguales; con medios anillos serían 2 : 1
    # y cada cuña queda dentro de su semiplano diagonal (S: arriba, N: derecha con A-scan hacia nasal)
    rows, cols = np.arange(N)[:, None], np.arange(N)[None, :]
    up = np.broadcast_to((400 - rows) * PX, (N, N))
    right = np.broadcast_to((cols - 400) * PX, (N, N))
    assert (np.abs(right)[m["S1"]] <= up[m["S1"]] + PX).all()
    assert (np.abs(up)[m["N1"]] <= right[m["N1"]] + PX).all()


def _field(top_high=False, right_high=False, n=N):
    t = np.full((n, n), 300.0)
    c = (n - 1) / 2
    if top_high:
        t[np.arange(n) < c, :] += 100.0
    if right_high:
        t[:, np.arange(n) > c] += 100.0
    return t


def _res(t, lat, orient, **kw):
    return compute_etdrs_v2(t, lateral_um=PX, bscan_um=PX, laterality=lat, orientation=orient, **kw)


@pytest.mark.parametrize(
    "orient,expect_superior_high",
    [(SUP_TOP_NASAL_RIGHT, True), (SUP_BOTTOM_NASAL_RIGHT, False)],
)
def test_orientation_row0_side_decides_superior_vs_inferior(orient, expect_superior_high):
    r = _res(_field(top_high=True), "R", orient)  # las filas con índice bajo son más gruesas
    s, i = r.value("S1"), r.value("I1")
    assert (s > i) == expect_superior_high
    assert r.value("N1") == pytest.approx(r.value("T1"), abs=3.0)  # el gradiente vertical no separa N de T


@pytest.mark.parametrize(
    "orient,lat,nasal_high",
    [
        (SUP_TOP_NASAL_RIGHT, "R", True),  # A-scan crece hacia nasal: derecha = nasal
        (SUP_TOP_NASAL_RIGHT, "L", True),
        (SUP_TOP_NASAL_LEFT, "R", False),  # A-scan crece hacia temporal: derecha = temporal
        (SUP_TOP_NASAL_LEFT, "L", False),
        (LEGACY_ASSUMED, "R", False),  # legacy: OD con nasal a la izquierda
        (LEGACY_ASSUMED, "L", True),  # legacy: OS con nasal a la derecha
    ],
)
def test_orientation_and_laterality_decide_nasal_vs_temporal(orient, lat, nasal_high):
    r = _res(_field(right_high=True), lat, orient)
    assert (r.value("N1") > r.value("T1")) == nasal_high
    assert r.value("S1") == pytest.approx(r.value("I1"), abs=3.0)


def test_laterality_flips_nasal_temporal_when_orientation_differs_by_eye():
    od = Orientation("superior", True, False, True, "prueba: OD nasal a la derecha, OS nasal a la izquierda")
    t = _field(right_high=True)
    assert _res(t, "OD", od).value("N1") > _res(t, "OD", od).value("T1")
    assert _res(t, "OS", od).value("N1") < _res(t, "OS", od).value("T1")


def test_physical_scale_gives_the_same_means_at_different_pixel_sizes():
    """Campo radial continuo t(r) = 300 + 0.1·r (µm): las medias por región no dependen de la resolución."""

    def field(px, n):
        c = (n - 1) / 2
        rr, cc = np.arange(n)[:, None], np.arange(n)[None, :]
        return 300.0 + 0.1 * np.hypot((rr - c) * px, (cc - c) * px)

    coarse = compute_etdrs_v2(
        field(20.0, 401), lateral_um=20.0, bscan_um=20.0, laterality="R", orientation=SUP_TOP_NASAL_RIGHT
    )
    fine = compute_etdrs_v2(
        field(5.0, 1601), lateral_um=5.0, bscan_um=5.0, laterality="R", orientation=SUP_TOP_NASAL_RIGHT
    )
    for name in REGIONS:
        assert coarse.value(name) == pytest.approx(fine.value(name), abs=1.0), name
    # Valor analítico del círculo central: media de 300 + 0.1 r sobre un disco de radio 500 = 300 + 0.1 · (2/3) · 500
    assert fine.value("C") == pytest.approx(300 + 0.1 * (2 / 3) * 500, abs=0.6)


def test_anisotropic_pixels_use_each_axis_scale():
    n_b, n_a = 128, 512  # como un cubo Topcon: 46.9 µm entre B-scans y 11.7 µm entre A-scans
    t = np.full((n_b, n_a), 300.0)
    r = compute_etdrs_v2(t, lateral_um=11.71875, bscan_um=46.875, laterality="L", orientation=SUP_TOP_NASAL_RIGHT)
    assert all(r.regions[n].available for n in REGIONS)  # 6 × 6 mm: cobertura completa
    assert all(r.regions[n].coverage > 0.97 for n in REGIONS)
    assert r.regions["C"].expected_area_mm2 == pytest.approx(np.pi * 0.25, rel=1e-6)


def test_coverage_is_evaluated_per_sector_when_the_center_is_off_center():
    n = 601  # ±3 mm
    t = np.full((n, n), 300.0)
    off = Center(300.0, 300.0 + 160, "foveal_registered", True)  # centro 1.6 mm a la derecha
    r = _res(t, "R", SUP_TOP_NASAL_RIGHT, center=off)
    assert (
        r.regions["C"].available and r.regions["N1"].available
    )  # el anillo interno hacia la derecha aún cabe parcialmente
    assert r.regions["N2"].coverage < 0.9 and not r.regions["N2"].available and r.value("N2") is None
    assert r.regions["T2"].coverage > r.regions["N2"].coverage  # la cobertura difiere entre sectores
    assert r.regions["N2"].reason == "coverage_below_threshold"


def test_valid_fraction_per_sector():
    t = _field()
    t[:400, :] = np.nan  # mitad superior sin dato
    r = _res(t, "R", SUP_TOP_NASAL_RIGHT)
    assert r.value("S1") is None and r.regions["S1"].reason == "valid_fraction_below_threshold"
    assert r.value("I1") == pytest.approx(300.0)
    assert r.regions["C"].available is False  # el disco central queda 50 % válido (< 80 %)


def test_policy_none_reports_every_region_with_data():
    t = _field()
    t[:400, :] = np.nan
    r = _res(t, "R", SUP_TOP_NASAL_RIGHT, policy=None)
    assert r.value("S1") is None  # sin píxeles válidos, nada que promediar
    assert r.value("I1") == pytest.approx(300.0) and r.value("C") == pytest.approx(300.0, abs=1.0)
    assert EtdrsCoveragePolicy("x", 0.0, 0.0).name == "x"


def test_center_label_never_says_cmt_without_a_validated_foveal_center():
    t = _field()
    geo = _res(t, "R", SUP_TOP_NASAL_RIGHT)
    assert geo.central_label == "central_disc_geometric" and not geo.clinical_central_eligible
    reg = _res(t, "R", SUP_TOP_NASAL_RIGHT, center=Center(400, 400, "foveal_registered", False))
    assert reg.central_label.endswith("unvalidated") and not reg.clinical_central_eligible
    val = _res(t, "R", SUP_TOP_NASAL_RIGHT, center=Center(400, 400, "foveal_registered", True))
    assert val.clinical_central_eligible and val.central_label == "central_subfield_thickness"
    for r in (geo, reg, val):
        assert "cmt" not in r.central_label.lower()
    assert any("centro" in w for w in geo.warnings)


def test_foveal_candidate_finds_the_pit_and_is_not_validated():
    n_b, n_a = 128, 512
    rr, cc = np.arange(n_b)[:, None], np.arange(n_a)[None, :]
    pit_r, pit_c = 70, 290  # desplazado del centro geométrico (63.5, 255.5)
    d = np.hypot((rr - pit_r) * 46.875, (cc - pit_c) * 11.71875)
    t = 340.0 - 90.0 * np.exp(-((d / 350.0) ** 2))
    c = find_foveal_candidate(t, 11.71875, 46.875)
    assert abs(c.row - pit_r) <= 2 and abs(c.col - pit_c) <= 4
    assert c.source == "foveal_candidate_min_thickness" and c.validated is False
    g = geometric_center(t.shape)
    assert (g.row, g.col, g.source) == (63.5, 255.5, "geometric")
    res = compute_etdrs_v2(
        t, lateral_um=11.71875, bscan_um=46.875, laterality="R", orientation=SUP_TOP_NASAL_RIGHT, center=c
    )
    geo = compute_etdrs_v2(t, lateral_um=11.71875, bscan_um=46.875, laterality="R", orientation=SUP_TOP_NASAL_RIGHT)
    assert res.central_subfield_um < geo.central_subfield_um  # centrar en la fóvea da un valor central menor


def test_unvalidated_orientation_is_reported_as_a_warning():
    r = _res(_field(), "R", LEGACY_ASSUMED)
    assert any("orientación NO validada" in w for w in r.warnings)
    assert not any("orientación" in w for w in _res(_field(), "R", SUP_TOP_NASAL_RIGHT).warnings)
