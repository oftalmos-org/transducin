# transducin/orthanc_client.py
# SPDX-License-Identifier: Apache-2.0
#
# Cliente REST para Orthanc — utilidades de consulta y resolución de UIDs.
#
# Propósito principal:
#   resolve_study_uid() — devuelve el StudyInstanceUID de un estudio ya existente
#   en Orthanc para un paciente (NOEL ID) y fecha dada, o el UID de fallback si
#   no hay ninguno. Esto permite que el SR Transducin quede dentro del mismo
#   estudio que las imágenes OCT originales en lugar de crear un estudio huérfano.
#
# Uso:
#   from transducin.orthanc_client import resolve_study_uid
#   uid = resolve_study_uid("http://localhost:8042", "JAHJ19870831",
#                            "20240315", fallback_uid=generate_uid(),
#                            auth=("orthanc",""))

from __future__ import annotations

import logging
import os
from typing import Optional
from urllib.request import Request, urlopen
from urllib.error import URLError
import json
import base64

logger = logging.getLogger("transducin.orthanc")

# ── Configuración por defecto (override via env o parámetros) ─────────────────

ORTHANC_HTTP_HOST = os.environ.get("ORTHANC_HTTP_HOST", os.environ.get("ORTHANC_HOST", "localhost"))
ORTHANC_HTTP_PORT = int(os.environ.get("ORTHANC_HTTP_PORT", "8042"))
ORTHANC_HTTP_USER = os.environ.get("ORTHANC_HTTP_USER", "orthanc")
ORTHANC_HTTP_PASS = os.environ.get("ORTHANC_HTTP_PASS", "")

CONNECT_TIMEOUT = 5   # segundos — no bloquear el pipeline si Orthanc no responde


def _orthanc_url() -> str:
    return f"http://{ORTHANC_HTTP_HOST}:{ORTHANC_HTTP_PORT}"


def _auth_header(user: str, password: str) -> str:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return f"Basic {token}"


def _post_json(url: str, payload: dict,
               auth: Optional[tuple[str, str]] = None,
               timeout: int = CONNECT_TIMEOUT) -> Optional[list | dict]:
    """POST JSON a Orthanc y retorna la respuesta deserializada, o None si falla."""
    body = json.dumps(payload).encode()
    req = Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    if auth:
        req.add_header("Authorization", _auth_header(auth[0], auth[1]))
    try:
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except URLError as e:
        logger.debug("Orthanc POST %s falló: %s", url, e)
        return None
    except Exception as e:
        logger.debug("Orthanc POST error inesperado: %s", e)
        return None


def _get_json(url: str,
              auth: Optional[tuple[str, str]] = None,
              timeout: int = CONNECT_TIMEOUT) -> Optional[dict]:
    """GET JSON desde Orthanc, retorna None si falla."""
    req = Request(url)
    if auth:
        req.add_header("Authorization", _auth_header(auth[0], auth[1]))
    try:
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as e:
        logger.debug("Orthanc GET %s error: %s", url, e)
        return None


def resolve_study_uid(
    noel_id: str,
    study_date: str,
    fallback_uid: str,
    orthanc_base_url: Optional[str] = None,
    auth: Optional[tuple[str, str]] = None,
) -> str:
    """Devuelve el StudyInstanceUID de un estudio existente en Orthanc.

    Busca en Orthanc un estudio con PatientID == noel_id y StudyDate == study_date.
    Si hay uno o más, devuelve el StudyInstanceUID del primero encontrado — así el
    SR Transducin queda dentro del mismo estudio DICOM que las imágenes OCT.
    Si no hay ninguno (o Orthanc no responde), devuelve fallback_uid.

    Args:
        noel_id:          PatientID NOEL (p.ej. "JAHJ19870831").
        study_date:       Fecha YYYYMMDD del estudio a buscar.
        fallback_uid:     UID a devolver si no se encuentra ningún estudio existente.
        orthanc_base_url: URL base REST de Orthanc (default: env/constante ORTHANC_HTTP_*).
        auth:             (user, password) para Basic Auth (default: ORTHANC_HTTP_USER/PASS).

    Returns:
        str — StudyInstanceUID (existente o fallback).
    """
    base_url = orthanc_base_url or _orthanc_url()
    if auth is None:
        auth = (ORTHANC_HTTP_USER, ORTHANC_HTTP_PASS)

    find_url = f"{base_url}/tools/find"
    payload = {
        "Level": "Study",
        "Query": {
            "PatientID": noel_id,
            "StudyDate": study_date,
        },
    }

    result = _post_json(find_url, payload, auth=auth)
    if not result:
        logger.debug("resolve_study_uid: sin respuesta de Orthanc — usando fallback UID")
        return fallback_uid

    if not isinstance(result, list) or len(result) == 0:
        logger.debug("resolve_study_uid: %s/%s no encontrado en Orthanc — nuevo estudio", noel_id, study_date)
        return fallback_uid

    # Hay al menos un estudio — obtener su StudyInstanceUID
    # Si hay varios (raro pero posible), preferir el primero
    orthanc_study_id = result[0]
    study_info = _get_json(f"{base_url}/studies/{orthanc_study_id}", auth=auth)
    if not study_info:
        logger.warning("resolve_study_uid: no se pudo leer el estudio %s", orthanc_study_id)
        return fallback_uid

    uid = study_info.get("MainDicomTags", {}).get("StudyInstanceUID")
    if not uid:
        logger.warning("resolve_study_uid: estudio %s sin StudyInstanceUID en MainDicomTags", orthanc_study_id)
        return fallback_uid

    n_studies = len(result)
    if n_studies > 1:
        logger.info(
            "resolve_study_uid: %d estudios para %s/%s — usando el primero (%s)",
            n_studies, noel_id, study_date, uid[:20] + "...",
        )
    else:
        logger.info(
            "resolve_study_uid: estudio existente para %s/%s → %s",
            noel_id, study_date, uid[:20] + "...",
        )

    return uid


def list_patient_studies(
    noel_id: str,
    orthanc_base_url: Optional[str] = None,
    auth: Optional[tuple[str, str]] = None,
) -> list[dict]:
    """Lista todos los estudios de un paciente en Orthanc.

    Returns:
        Lista de dicts con keys: orthanc_id, study_uid, study_date, modalities.
        Lista vacía si el paciente no existe o Orthanc no responde.
    """
    base_url = orthanc_base_url or _orthanc_url()
    if auth is None:
        auth = (ORTHANC_HTTP_USER, ORTHANC_HTTP_PASS)

    result = _post_json(f"{base_url}/tools/find",
                        {"Level": "Study", "Query": {"PatientID": noel_id}},
                        auth=auth)
    if not result:
        return []

    studies = []
    for oid in result:
        info = _get_json(f"{base_url}/studies/{oid}", auth=auth)
        if info:
            tags = info.get("MainDicomTags", {})
            studies.append({
                "orthanc_id": oid,
                "study_uid": tags.get("StudyInstanceUID", ""),
                "study_date": tags.get("StudyDate", ""),
                "modalities": info.get("ModalitiesInStudy", []),
            })
    return sorted(studies, key=lambda s: s["study_date"])


# ── Tests internos ────────────────────────────────────────────────────────────

def _run_tests() -> None:
    errs = 0

    def check(label, cond, detail=""):
        nonlocal errs
        status = "OK" if cond else "FAIL"
        if not cond:
            errs += 1
        print(f"  [{status}] {label}" + (f" — {detail}" if detail else ""))

    print("═══ orthanc_client tests ═══")

    # Test 1: fallback cuando Orthanc no responde
    uid = resolve_study_uid(
        noel_id="GHOST00000000",
        study_date="20000101",
        fallback_uid="1.2.3.4.5",
        orthanc_base_url="http://127.0.0.1:19999",  # puerto inexistente
        auth=("x", "x"),
    )
    check("fallback cuando Orthanc no responde", uid == "1.2.3.4.5", f"uid={uid}")

    # Test 2: resolve con Orthanc real (opcional — solo si disponible)
    test_uid = resolve_study_uid(
        noel_id="SILT19800101",
        study_date="20250107",
        fallback_uid="FALLBACK_UID",
    )
    if test_uid == "FALLBACK_UID":
        print("  [SKIP] resolve contra Orthanc real — no disponible desde este entorno")
    else:
        check("resolve SILT19800101/20250107 devuelve UID real",
              test_uid.startswith("1.") or test_uid.startswith("2."),
              f"uid={test_uid[:40]}")

    print(f"{'PASS' if errs == 0 else 'FAIL'} — {errs} error(es)\n")
    if errs:
        raise SystemExit(1)


if __name__ == "__main__":
    import logging as _log
    _log.basicConfig(level=_log.DEBUG)
    _run_tests()
