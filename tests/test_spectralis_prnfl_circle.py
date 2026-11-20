"""Spectralis: el pRNFL de un circle scan se reparte en cuadrantes TSNIT de 90° que no se solapan."""

from __future__ import annotations

import numpy as np

from transducin.spectralis_extractor import _compute_prnfl_circle


def _circle(n_a: int, vals: dict[str, float]) -> tuple[np.ndarray, np.ndarray]:
    ang = (np.arange(n_a) + 0.5) * 360.0 / n_a
    th = np.empty(n_a)
    th[(ang < 45) | (ang >= 315)] = vals["T"]
    th[(ang >= 45) & (ang < 135)] = vals["S"]
    th[(ang >= 135) & (ang < 225)] = vals["N"]
    th[(ang >= 225) & (ang < 315)] = vals["I"]
    ilm = np.zeros((1, n_a))
    return ilm, (th / 3.87)[None, :]


def test_quadrants_are_exact_and_inferior_is_reported():
    ilm, nfl = _circle(768, {"T": 83.0, "S": 130.0, "N": 90.0, "I": 151.0})
    r = _compute_prnfl_circle(ilm, nfl, 3.87, "R")
    assert (r.temporal, r.superior, r.nasal, r.inferior) == (83.0, 130.0, 90.0, 151.0)
    assert abs(r.global_avg - 113.5) < 0.2  # media de los cuatro cuadrantes
    assert r.inferior is not None


def test_same_sectors_for_both_eyes():
    ilm, nfl = _circle(768, {"T": 82.0, "S": 143.0, "N": 83.0, "I": 151.0})
    assert _compute_prnfl_circle(ilm, nfl, 3.87, "L").nasal == _compute_prnfl_circle(ilm, nfl, 3.87, "R").nasal == 83.0


def test_circle_bscan_is_found_inside_an_onh_volume():
    from transducin.spectralis_extractor import _find_circle_bscan

    n_b, n_a = 27, 768
    ilm = np.full((n_b, n_a), 100.0)
    nfl = np.full((n_b, n_a), np.nan)
    for i in (24, 25, 26):
        nfl[i] = 130.0
    data = [{"posX1": float(x)} for x in [0.0] * 24 + [3.8, 4.8, 5.8]]
    assert _find_circle_bscan(ilm, nfl, data) == 24  # el de menor |posX|: el círculo interno


def test_macular_volume_has_no_circle_bscan():
    from transducin.spectralis_extractor import _find_circle_bscan

    ilm = np.full((61, 768), 100.0)
    nfl = np.full((61, 768), 130.0)  # RNFL/GCL en todos los cortes → es un cubo, no círculos
    assert _find_circle_bscan(ilm, nfl, None) is None
