# transducin/api.py
# SPDX-License-Identifier: Apache-2.0
#
# FastAPI REST — consulta clínica OCT y almacenamiento de resúmenes con pgvector.
#
# Endpoints:
#   GET  /health
#   GET  /patients/{noel_id}/oct-latest   — último SR DICOM, parsed TID 1500
#   GET  /patients/{noel_id}/oct-trend    — últimos 5 estudios con CMT+vendor
#   POST /store-summary                   — embed + INSERT episode_embeddings
#
# Variables de entorno:
#   ORTHANC_HTTP_HOST          (default localhost)
#   ORTHANC_HTTP_PORT          (default 8042)
#   ORTHANC_HTTP_USER          (default orthanc)
#   ORTHANC_HTTP_PASS          (default "")
#   TRANSDUCIN_API_PORT        (default 8004)
#   TRANSDUCIN_PGVECTOR_DSN    (requerida para /store-summary)
#   TRANSDUCIN_EMBEDDING_URL   (opcional; si ausente embedding → NULL)
#   TRANSDUCIN_EMBEDDING_DIM   (default 768)
#   TRANSDUCIN_SQI_MIN_WARN    (default 6, escala 0-10)
#
# Uso:
#   python -m transducin.api
#   uvicorn transducin.api:app --port 8004

from __future__ import annotations

import asyncio
import io
import logging
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Optional

import httpx
import pydicom
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

logger = logging.getLogger("transducin.api")

# ── Configuración ─────────────────────────────────────────────────────────────

_ORTHANC_HOST = os.environ.get("ORTHANC_HTTP_HOST", os.environ.get("ORTHANC_HOST", "localhost"))
_ORTHANC_PORT = int(os.environ.get("ORTHANC_HTTP_PORT", "8042"))
_ORTHANC_USER = os.environ.get("ORTHANC_HTTP_USER", "orthanc")
_ORTHANC_PASS = os.environ.get("ORTHANC_HTTP_PASS", "")
_ORTHANC_BASE = f"http://{_ORTHANC_HOST}:{_ORTHANC_PORT}"

_PGVECTOR_DSN    = os.environ.get("TRANSDUCIN_PGVECTOR_DSN", "")
_EMBEDDING_URL   = os.environ.get("TRANSDUCIN_EMBEDDING_URL", "")
_EMBEDDING_DIM   = int(os.environ.get("TRANSDUCIN_EMBEDDING_DIM", "768"))
_API_PORT        = int(os.environ.get("TRANSDUCIN_API_PORT", "8004"))
_SQI_WARN_THRESH = float(os.environ.get("TRANSDUCIN_SQI_MIN_WARN", "6")) / 10.0

# SR TID 1500 measurement codes — usados para identificar mediciones en ContentSequence
_CODE_CMT       = "422453003"   # SCT Foveal retinal thickness
_CODE_RNFL      = "422995006"   # SCT Retinal nerve fiber layer thickness
_CODE_SQI       = "113061"      # DCM Signal to Noise Ratio
_CODE_CDR       = "363932005"   # SCT Cup to disc ratio
_CODE_VCDR      = "363930007"   # SCT Vertical cup to disc ratio
# Topographic modifier codes — presencia de estos → sector, no global
_SECTOR_MODS    = {"264217000", "261089000", "255454004", "255352004"}


# ── Lifespan: httpx client + asyncpg pool ─────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # httpx async client para llamadas a Orthanc
    auth = (_ORTHANC_USER, _ORTHANC_PASS)
    app.state.http = httpx.AsyncClient(
        auth=auth,
        timeout=httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0),
    )

    # asyncpg pool (opcional — solo si PGVECTOR_DSN está configurado)
    app.state.pg = None
    if _PGVECTOR_DSN:
        try:
            import asyncpg  # type: ignore
            pool = await asyncpg.create_pool(
                dsn=_PGVECTOR_DSN,
                min_size=2,
                max_size=10,
            )
            app.state.pg = pool
            await _ensure_table(pool)
            logger.info("asyncpg pool listo → %s", _PGVECTOR_DSN.split("@")[-1])
        except Exception as exc:
            logger.warning("asyncpg no disponible: %s — /store-summary deshabilitado", exc)
    else:
        logger.info("TRANSDUCIN_PGVECTOR_DSN no configurado — /store-summary deshabilitado")

    yield

    await app.state.http.aclose()
    if app.state.pg:
        await app.state.pg.close()


app = FastAPI(
    title="Transducin API",
    version="1.3.0",
    description="Consulta clínica OCT desde Orthanc + almacenamiento con pgvector.",
    lifespan=lifespan,
)


# ── DB: tabla episode_embeddings ──────────────────────────────────────────────

_CREATE_TABLE_SQL = """
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS episode_embeddings (
    id         BIGSERIAL PRIMARY KEY,
    noel_id    TEXT        NOT NULL,
    episode_id TEXT        NOT NULL DEFAULT '',
    study_date TEXT        NOT NULL,
    vendor     TEXT        NOT NULL DEFAULT '',
    content    TEXT        NOT NULL,
    embedding  vector({dim}),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_episode_embeddings_noel_id
    ON episode_embeddings (noel_id);
""".format(dim=_EMBEDDING_DIM)


async def _ensure_table(pool: Any) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_CREATE_TABLE_SQL)
    logger.info("episode_embeddings table ready (dim=%d)", _EMBEDDING_DIM)


# ── SR parsing: recorre ContentSequence recursivamente ───────────────────────

def _has_sector_modifier(item: pydicom.Dataset) -> bool:
    """Retorna True si el ítem tiene un modificador topográfico de sector."""
    for mod in getattr(item, "ContentSequence", []):
        cn = getattr(mod, "ConceptNameCodeSequence", [])
        if not cn:
            continue
        # RelationshipType HAS CONCEPT MOD y código de sector
        if getattr(mod, "RelationshipType", "") in ("HAS CONCEPT MOD", "HAS OBS CONTEXT"):
            cv = getattr(cn[0], "CodeValue", "")
            if cv in _SECTOR_MODS:
                return True
    return False


def _traverse_content(
    seq: list[pydicom.Dataset],
    results: dict[str, Any],
    depth: int = 0,
) -> None:
    """Extrae mediciones numéricas de ContentSequence (máx. profundidad 8)."""
    if depth > 8:
        return
    for item in seq:
        vtype = getattr(item, "ValueType", "")
        if vtype == "NUM":
            cn = getattr(item, "ConceptNameCodeSequence", [])
            mv = getattr(item, "MeasuredValueSequence", [])
            if cn and mv:
                code = getattr(cn[0], "CodeValue", "")
                try:
                    value = float(mv[0].NumericValue)
                except (AttributeError, TypeError, ValueError):
                    value = None
                if value is not None:
                    if code == _CODE_CMT and "cmt_um" not in results:
                        results["cmt_um"] = value
                    elif code == _CODE_RNFL and "rnfl_global" not in results:
                        # Solo el primer RNFL sin modificador de sector = global
                        if not _has_sector_modifier(item):
                            results["rnfl_global"] = value
                    elif code == _CODE_SQI and "sqi_mean" not in results:
                        results["sqi_mean"] = value
                    elif code == _CODE_CDR and "cdr" not in results:
                        results["cdr"] = value
                    elif code == _CODE_VCDR and "vcdr" not in results:
                        results["vcdr"] = value

        # Recursión en sub-secuencias
        child_seq = getattr(item, "ContentSequence", None)
        if child_seq:
            _traverse_content(list(child_seq), results, depth + 1)


def _parse_sr_content(ds: pydicom.Dataset) -> dict[str, Any]:
    """Extrae mediciones clínicas del dataset SR DICOM.

    Returns dict con keys: cmt_um, rnfl_global, sqi_mean, cdr, vcdr (todos opcionales).
    """
    results: dict[str, Any] = {}
    content_seq = getattr(ds, "ContentSequence", None)
    if content_seq:
        _traverse_content(list(content_seq), results)
    return results


# ── Orthanc: búsqueda y descarga de SR ───────────────────────────────────────

async def _orthanc_find_studies(http: httpx.AsyncClient, noel_id: str) -> list[str]:
    """Retorna lista de orthanc_study_ids para el paciente."""
    resp = await http.post(
        f"{_ORTHANC_BASE}/tools/find",
        json={"Level": "Study", "Query": {"PatientID": noel_id}},
    )
    resp.raise_for_status()
    return resp.json()


async def _orthanc_study_info(http: httpx.AsyncClient, study_oid: str) -> dict:
    resp = await http.get(f"{_ORTHANC_BASE}/studies/{study_oid}")
    resp.raise_for_status()
    return resp.json()


async def _orthanc_series_list(http: httpx.AsyncClient, study_oid: str) -> list[dict]:
    resp = await http.get(f"{_ORTHANC_BASE}/studies/{study_oid}/series")
    resp.raise_for_status()
    return resp.json()


async def _orthanc_instances(http: httpx.AsyncClient, series_oid: str) -> list[dict]:
    resp = await http.get(f"{_ORTHANC_BASE}/series/{series_oid}/instances")
    resp.raise_for_status()
    return resp.json()


async def _download_instance_bytes(http: httpx.AsyncClient, instance_oid: str) -> bytes:
    resp = await http.get(f"{_ORTHANC_BASE}/instances/{instance_oid}/file")
    resp.raise_for_status()
    return resp.content


async def _get_latest_sr_instance(
    http: httpx.AsyncClient,
    noel_id: str,
) -> Optional[tuple[pydicom.Dataset, str, str, str]]:
    """Obtiene el SR más reciente del paciente.

    Returns:
        Tuple (dataset, study_date, vendor, laterality) o None si no hay SR.
    """
    study_oids = await _orthanc_find_studies(http, noel_id)
    if not study_oids:
        return None

    # Recopilar estudios con SR ordenados por fecha (más reciente primero)
    study_infos: list[dict] = []
    for oid in study_oids:
        info = await _orthanc_study_info(http, oid)
        modalities = info.get("ModalitiesInStudy", [])
        if "SR" in modalities:
            tags = info.get("MainDicomTags", {})
            study_infos.append({
                "oid":   oid,
                "date":  tags.get("StudyDate", ""),
                "info":  info,
            })

    if not study_infos:
        return None

    study_infos.sort(key=lambda s: s["date"], reverse=True)
    latest = study_infos[0]

    # Encontrar la serie SR
    series_list = await _orthanc_series_list(http, latest["oid"])
    sr_series = [s for s in series_list if s.get("MainDicomTags", {}).get("Modality") == "SR"]
    if not sr_series:
        return None

    # Tomar primera instancia de la primera serie SR
    instances = await _orthanc_instances(http, sr_series[0]["ID"])
    if not instances:
        return None

    raw = await _download_instance_bytes(http, instances[0]["ID"])
    ds = pydicom.dcmread(io.BytesIO(raw))

    # Extraer metadata del estudio
    study_date = latest["date"]
    vendor = str(getattr(ds, "Manufacturer", "") or "unknown").lower().replace(" ", "_")
    lat_raw = str(getattr(ds, "ImageLaterality", "") or getattr(ds, "Laterality", "") or "")

    return ds, study_date, vendor, lat_raw


# ── Modelos Pydantic ──────────────────────────────────────────────────────────

class OCTLatestResponse(BaseModel):
    noel_id:    str
    study_date: str
    vendor:     str
    laterality: str
    cmt_um:     Optional[float] = None
    rnfl_global_um: Optional[float] = None
    sqi_mean:   Optional[float] = None
    sqi_10:     Optional[float] = None   # sqi_mean * 10 (escala 0-10)
    sqi_warn:   bool = False
    cdr:        Optional[float] = None
    vcdr:       Optional[float] = None


class OCTTrendPoint(BaseModel):
    study_date: str
    cmt_um:     Optional[float] = None
    rnfl_global_um: Optional[float] = None
    sqi_10:     Optional[float] = None
    vendor:     str
    laterality: str


class StoreSummaryRequest(BaseModel):
    noel_id:    str
    study_date: str
    vendor:     str = ""
    episode_id: str = ""
    content:    str   # texto del resumen clínico


class StoreSummaryResponse(BaseModel):
    id:       int
    noel_id:  str
    embedded: bool   # True si se generó embedding, False si se almacenó sin embedding


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "orthanc": _ORTHANC_BASE,
        "pgvector": bool(_PGVECTOR_DSN),
        "embedding": bool(_EMBEDDING_URL),
    }


@app.get("/patients/{noel_id}/oct-latest", response_model=OCTLatestResponse)
async def oct_latest(noel_id: str) -> OCTLatestResponse:
    """Retorna mediciones del SR DICOM más reciente del paciente (< 800 ms)."""
    http: httpx.AsyncClient = app.state.http

    result = await _get_latest_sr_instance(http, noel_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"No se encontró SR para {noel_id}")

    ds, study_date, vendor, lat = result
    measurements = _parse_sr_content(ds)

    sqi = measurements.get("sqi_mean")
    return OCTLatestResponse(
        noel_id    = noel_id,
        study_date = study_date,
        vendor     = vendor,
        laterality = lat,
        cmt_um     = measurements.get("cmt_um"),
        rnfl_global_um = measurements.get("rnfl_global"),
        sqi_mean   = sqi,
        sqi_10     = round(sqi * 10, 1) if sqi is not None else None,
        sqi_warn   = sqi is not None and sqi < _SQI_WARN_THRESH,
        cdr        = measurements.get("cdr"),
        vcdr       = measurements.get("vcdr"),
    )


@app.get("/patients/{noel_id}/oct-trend", response_model=list[OCTTrendPoint])
async def oct_trend(noel_id: str) -> list[OCTTrendPoint]:
    """Retorna los últimos 5 estudios con SR — CMT + vendor para graficar tendencia."""
    http: httpx.AsyncClient = app.state.http

    study_oids = await _orthanc_find_studies(http, noel_id)
    if not study_oids:
        raise HTTPException(status_code=404, detail=f"Paciente {noel_id} no encontrado")

    # Filtrar estudios con SR y ordenar por fecha
    study_metas: list[dict] = []
    for oid in study_oids:
        info = await _orthanc_study_info(http, oid)
        if "SR" in info.get("ModalitiesInStudy", []):
            tags = info.get("MainDicomTags", {})
            study_metas.append({"oid": oid, "date": tags.get("StudyDate", "")})

    study_metas.sort(key=lambda s: s["date"], reverse=True)
    selected = study_metas[:5]

    if not selected:
        raise HTTPException(status_code=404, detail=f"Sin estudios SR para {noel_id}")

    # Descargar SR en paralelo (máx. 5 concurrentes)
    sem = asyncio.Semaphore(5)

    async def _fetch_study_point(meta: dict) -> Optional[OCTTrendPoint]:
        async with sem:
            try:
                series_list = await _orthanc_series_list(http, meta["oid"])
                sr_series = [
                    s for s in series_list
                    if s.get("MainDicomTags", {}).get("Modality") == "SR"
                ]
                if not sr_series:
                    return None
                instances = await _orthanc_instances(http, sr_series[0]["ID"])
                if not instances:
                    return None
                raw = await _download_instance_bytes(http, instances[0]["ID"])
                ds  = pydicom.dcmread(io.BytesIO(raw))
                m   = _parse_sr_content(ds)
                vendor = str(getattr(ds, "Manufacturer", "") or "unknown")
                lat    = str(
                    getattr(ds, "ImageLaterality", "")
                    or getattr(ds, "Laterality", "")
                    or ""
                )
                sqi = m.get("sqi_mean")
                return OCTTrendPoint(
                    study_date     = meta["date"],
                    cmt_um         = m.get("cmt_um"),
                    rnfl_global_um = m.get("rnfl_global"),
                    sqi_10         = round(sqi * 10, 1) if sqi is not None else None,
                    vendor         = vendor,
                    laterality     = lat,
                )
            except Exception as exc:
                logger.warning("oct-trend: error en estudio %s: %s", meta["oid"][:8], exc)
                return None

    results = await asyncio.gather(*[_fetch_study_point(m) for m in selected])
    points  = [p for p in results if p is not None]
    # Ordenar cronológicamente para la vista de tendencia
    points.sort(key=lambda p: p.study_date)
    return points


@app.post("/store-summary", response_model=StoreSummaryResponse)
async def store_summary(req: StoreSummaryRequest) -> StoreSummaryResponse:
    """Almacena resumen clínico con embedding vectorial en episode_embeddings.

    Si TRANSDUCIN_EMBEDDING_URL no está configurado, almacena el texto sin embedding.
    Si TRANSDUCIN_PGVECTOR_DSN no está configurado, retorna 503.
    """
    pool = app.state.pg
    if pool is None:
        raise HTTPException(
            status_code=503,
            detail="TRANSDUCIN_PGVECTOR_DSN no configurado — almacenamiento deshabilitado",
        )

    # Obtener embedding (opcional)
    embedding: Optional[list[float]] = None
    embedded = False
    if _EMBEDDING_URL:
        try:
            http: httpx.AsyncClient = app.state.http
            emb_resp = await http.post(
                _EMBEDDING_URL,
                json={"input": req.content, "model": "text-embedding-ada-002"},
                timeout=15.0,
            )
            emb_resp.raise_for_status()
            data = emb_resp.json()
            embedding = data["data"][0]["embedding"]
            embedded  = True
        except Exception as exc:
            logger.warning("Embedding HTTP error: %s — almacenando sin embedding", exc)

    # INSERT en episode_embeddings
    async with pool.acquire() as conn:
        if embedding is not None:
            # asyncpg necesita el cast explícito ::vector para pgvector
            vec_str = "[" + ",".join(str(x) for x in embedding) + "]"
            row = await conn.fetchrow(
                """
                INSERT INTO episode_embeddings
                    (noel_id, episode_id, study_date, vendor, content, embedding)
                VALUES ($1, $2, $3, $4, $5, $6::vector)
                RETURNING id
                """,
                req.noel_id,
                req.episode_id,
                req.study_date,
                req.vendor,
                req.content,
                vec_str,
            )
        else:
            row = await conn.fetchrow(
                """
                INSERT INTO episode_embeddings
                    (noel_id, episode_id, study_date, vendor, content)
                VALUES ($1, $2, $3, $4, $5)
                RETURNING id
                """,
                req.noel_id,
                req.episode_id,
                req.study_date,
                req.vendor,
                req.content,
            )

    return StoreSummaryResponse(
        id=row["id"],
        noel_id=req.noel_id,
        embedded=embedded,
    )


# ── POST /refraction — genera SR DICOM desde lecturas de instrumento ──────────

class RefractionEyeIn(BaseModel):
    eye:       str
    sph:       Optional[float] = None
    cyl:       Optional[float] = None
    ax:        Optional[int]   = None
    add_power: Optional[float] = None
    pd:        Optional[float] = None
    va:        Optional[str]   = None


class RefractionRequest(BaseModel):
    noel_id:       str
    device_source: str          # "autorefractor" | "lensometer"
    device_model:  str          # "URK-800" | "CCQ-800"
    study_date:    str          # YYYYMMDD
    patient_name:  str = ""
    patient_dob:   str = ""
    readings:      list[RefractionEyeIn]


class RefractionResponse(BaseModel):
    ok:       bool
    dcm_path: Optional[str] = None
    detail:   str = ""


@app.post("/refraction", response_model=RefractionResponse)
async def build_refraction(req: RefractionRequest) -> RefractionResponse:
    """Genera un DICOM ComprehensiveSR TID 1500 con mediciones refractivas
    y lo sube a Orthanc. Llamado como BackgroundTask desde antiscribe tras
    guardar una lectura de autorefractómetro o lensómetro.
    """
    from transducin.refraction_dicom_builder import (
        RefractionEye,
        build_refraction_sr,
    )
    from pathlib import Path as _Path

    readings = [
        RefractionEye(
            eye=r.eye,
            sph=r.sph,
            cyl=r.cyl,
            ax=r.ax,
            add_power=r.add_power,
            pd=r.pd,
            va=r.va,
        )
        for r in req.readings
    ]

    out_dir = _Path(os.environ.get("TRANSDUCIN_REFRACTION_OUTPUT", "/tmp/transducin/refraction"))
    try:
        dcm_path = build_refraction_sr(
            readings=readings,
            noel_id=req.noel_id,
            device_source=req.device_source,
            device_model=req.device_model,
            study_date=req.study_date,
            patient_name=req.patient_name,
            patient_dob=req.patient_dob,
            output_dir=out_dir,
            orthanc_base_url=_ORTHANC_BASE,
            auth=(_ORTHANC_USER, _ORTHANC_PASS),
        )
        if dcm_path:
            return RefractionResponse(ok=True, dcm_path=str(dcm_path))
        return RefractionResponse(ok=False, detail="build_refraction_sr retornó None")
    except Exception as exc:
        logger.error("POST /refraction error: %s", exc)
        return RefractionResponse(ok=False, detail=str(exc))


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    uvicorn.run(
        "transducin.api:app",
        host="0.0.0.0",
        port=_API_PORT,
        reload=False,
        log_level="info",
    )
