# Transducin 1.0.0

Pipeline multivendor OCT → DICOM para [RetinaOS](https://github.com/oftalmos-org). Convierte archivos propietarios de tomógrafos oftalmológicos a DICOM estándar, extrae mediciones clínicas (CMT, ETDRS 9 sectores, mRNFL/pRNFL, mGCIPL, biometría), genera Structured Reports TID 1500 con contexto anatómico y los envía a Orthanc PACS vía C-STORE.

## Demo

Transducin generando un SR TID 1500 desde un `.OPT` Revo FC130 y visualizándolo en OHIF Viewer:

https://github.com/oftalmos-org/transducin/raw/main/docs/demo/Transducin_in_OHIF_example_video.mp4

## Roadmap

### v1.0.0 — Disponible

- [x] Pipeline Revo FC130 completo: B-scans, SLO, ENFACE, ANGPRV, OCTA_MIP → `OphthalmicTomographyImageStorage`
- [x] Todos los tipos de scan Revo FC130: macular, optic_nerve, angio, hd_line, ultra_wide, wide_field, biometry, fundus (detección por chunks y dimensiones)
- [x] Extracción clínica: CMT, ETDRS 9 sectores, pRNFL (peripapillar), mRNFL, mGCIPL, biometría (AL, CCT), C/D ratio
- [x] SR TID 1500/1501 con anatomic context completo (FindingSite, lateralidad, modificadores S/N/I/T)
- [x] Protocolo NOEL como PatientID
- [x] Hot folder watcher multivendor
- [x] C-STORE → Orthanc PACS
- [x] Cirrus HD-OCT `.EX.DCM`: CMT/RNFL/GCL desde tags privados `(0073,xxxx)` + SR TID 1500
- [x] Cirrus PDF export: Macular Thickness OU, ONH+RNFL OU, Ganglion Cell OU (mGCIPL mínimo)
- [x] PTS 925Wi Optopol (perimetría): DICOM Secondary Capture (Modality OPV) con PDF embebido

### v1.2 — Pendiente

- [ ] Heidelberg Spectralis `.e2e`, Topcon `.fds`/`.fda`, Bioptigen `.OCT` via oct-converter
- [ ] SR campo visual TID 6002 (Supplement 247 — esperando highdicom [#406](https://github.com/ImagingDataCommons/highdicom/issues/406))
- [ ] Migración SR OCT a DICOM Supplement 247 TID 6001–6007
- [ ] Desencriptado PATIENT.DAT Revo FC130 (requiere key del vendor o ingeniería inversa)

## Arquitectura

```
input/REVO/*.opt                input/CIRRUS/*.EX.DCM
        │                               │
 revo_opt_reader ←── segmentación    cirrus_extractor
 (B-scans + SLO/ENFACE/ANGPRV        (tags privados 0073,xxxx)
  + ETDRS + mRNFL + mGCIPL                │
  + biometría + SQI + TRAJ)               │
        │                               │
 opt_extractor                          ─┴─────────────┐
 (filename + PARAMS.DAT                             │
  + MYOPI JSON + tipo de scan)               OCTClinicalData
        │                                           │
        └───────────────────────────────────────────┘
                            │
                       sr_builder  (TID 1500: anatomic context + FindingSite + lateralidad)
                            │         grupos: macular · peripapillar · biometría
                  hot_folder_watcher
                            │
                  C-STORE → Orthanc PACS
```

## Módulos (`transducin/`)

| Módulo | Función |
|---|---|
| `noel_id.py` | Protocolo NOEL — PatientID: `paterno[:2]+materno[0]+nombre[0]+YYYYMMDD` |
| `clinical_data.py` | Dataclasses: `OCTClinicalData`, `ETDRSGrid`, `RNFLSectors`, `VisualFieldData` (PTS) |
| `opt_extractor.py` | Metadatos de `.opt` Revo FC130: filename, PARAMS.DAT zlib, MYOPI JSON (biometría), detección de tipo de scan por chunks y dimensiones |
| `revo_opt_reader.py` | B-scans `.opt` → `OphthalmicTomographyImageStorage` + SLO/ENFACE/ANGPRV/OCTA_MIP → `OphthalmicPhotography8Bit` + CMT/ETDRS/pRNFL/mRNFL/mGCIPL desde segmentación |
| `sr_builder.py` | DICOM SR TID 1500 — grupos macular, peripapillar, biometría; vendor-aware (Optopol / Zeiss) |
| `cirrus_extractor.py` | Extrae CMT, ETDRS, RNFL, C/D desde tags privados CZM `(0073,xxxx)` de `.EX.DCM` |
| `cirrus_pdf_extractor.py` | OCR de PDFs Cirrus: Macular Thickness OU, ONH+RNFL OU, Ganglion Cell OU → `OCTClinicalData` |
| `pts925_extractor.py` | PTS 925 Optopol perimetría → `VisualFieldData` → SOP `1.2.840.10008.5.1.4.1.1.80` |
| `hot_folder_watcher.py` | Watcher multivendor: `.opt` `.dcm` `.EX.DCM` `.pdf` `.OCT` `.e2e` `.fds` `.fda` → pipeline → C-STORE |
| `verify_sr.py` | Valida SR: TID 1500, NOEL, SNOMED-CT (9 códigos), consulta Orthanc REST |

## Dispositivos soportados

| Fabricante | Equipo | Formato | Estado |
|---|---|---|---|
| Optopol | Revo FC130 | `.opt` | B-scans + SLO/ENFACE/ANGPRV + ETDRS + mRNFL + mGCIPL + biometría MYOPI |
| Carl Zeiss Meditec | Cirrus HD-OCT | `.ex.dcm` | CMT/RNFL/GCL desde tags privados `(0073,xxxx)` + SR TID 1500 |
| Bioptigen / Leica | varios | `.OCT` | Planned v1.2 via oct-converter |
| Topcon | DRI OCT Triton/Atlantis | `.fds`, `.fda` | Planned v1.2 via oct-converter |
| Heidelberg Engineering | Spectralis | `.e2e` | Planned v1.2 via oct-converter |

## Tipos de scan soportados (Revo FC130)

Detección por chunks presentes (ANGPRV, DMARKERS, EYE) y dimensiones `n_bscans × n_ascans`.

| Tipo | Dimensiones (B-scans × A-scans) | Imágenes de salida |
|---|---|---|
| `macular` | 168 × 1024 (cubo 6×6 mm) | `_OCT.dcm`, `_SLO.dcm`, `_ENFACE.dcm` |
| `optic_nerve` | 192 × 640 (cubo 6 mm ONH) | `_OCT.dcm`, `_SLO.dcm` |
| `angio` | 320 × 320 (OCTA 3 mm) | `_OCT.dcm`, `_SLO.dcm`, `_ENFACE.dcm`, `_ANGPRV.dcm`, `_OCTA_MIP.dcm` |
| `hd_line` | 18–25 × 1024 (raster HD) | `_OCT.dcm`, `_SLO.dcm` |
| `ultra_wide` | 1 × 10240 ó 6 × 8192 (campo 14–16 mm) | `_OCT.dcm`, `_SLO.dcm` |
| `wide_field` | 5 × 1536 (campo 12 mm) | `_OCT.dcm`, `_SLO.dcm` |
| `biometry` | BMETR + MYOPI JSON (zlib) | solo datos → SR biometría |
| `fundus` | Color_fundus (desde filename) | skipped (sin mediciones) |

## Características técnicas del formato .OPT

- **Lateralidad**: inferida del signo aritmético de OCTPARAMS tag 23 (posición foveal horizontal en mm); validada al 100% en 18 archivos de dos modelos de equipo y tres versiones de software.
- **PixelSpacing calibrado**: derivado de los parámetros de escaneo del chunk PARAMS.DAT; todas las instancias `OphthalmicTomographyImageStorage` incluyen `PixelSpacing` con escala µm/px real.
- **AnatomicRegionSequence**: presente en todas las instancias de imagen y SR; codificado con SNOMED-CT SRT (T-AA610 segmento posterior, T-AA700 segmento anterior) según DICOM CP-1676.

## Mediciones clínicas y SR

Los Structured Reports siguen TID 1500/1501 con contexto anatómico completo. Cada medición incluye `FindingSite` (sitio anatómico), lateralidad SNOMED-CT y modificador topográfico donde aplica. `AlgorithmIdentification` referencia `Transducin/<__version__>` en cada grupo.

### Grupo macular

| Medición | Código SCT | Unidad | Sitio |
|---|---|---|---|
| CMT — Central Macular Thickness | `422453003` | µm | Fovea centralis |
| ETDRS C (subfield central) | `422453003` | µm | Fovea centralis |
| ETDRS S1/N1/I1/T1 (anillo 1–3 mm) | `422399008` | µm | Retina + mod. S/N/I/T |
| ETDRS S2/N2/I2/T2 (anillo 3–6 mm) | `422399008` | µm | Retina + mod. S/N/I/T |
| mRNFL global y sectores S/I | `422995006` | µm | Retina |
| mGCIPL global y sectores S/I | `422455005` | µm | Retina |

### Grupo peripapillar (scan disco óptico)

| Medición | Código SCT | Unidad | Sitio |
|---|---|---|---|
| pRNFL global | `422995006` | µm | Optic nerve head |
| pRNFL S/N/I/T (sectores) | `422995006` | µm | Optic nerve head + mod. |
| C/D ratio | `363932005` | — | Optic nerve head |

### Grupo biometría (scan BMETR)

| Medición | Código SCT | Unidad |
|---|---|---|
| Longitud axial (AL) | `252017007` | mm |
| CCT — grosor corneal central | `397545004` | mm |
| K1 — queratometría meridiano plano | `252014009` | mm |
| K2 — queratometría meridiano curvo | `252016006` | mm |

## Protocolo NOEL

PatientID estándar para DICOM en RetinaOS:

```
apellido_paterno[:2] + apellido_materno[0] + nombre[0] + YYYYMMDD
```

Ejemplo: JESUS NOEL JAURRIETA HINOJOS, 1987-08-31 → **`JAHJ19870831`**

## Aviso regulatorio

Este software se proporciona para fines de investigación e integración técnica únicamente. No es un dispositivo médico certificado. El implementador es responsable de cualquier validación clínica requerida por su jurisdicción.

> *This software is provided for research and technical integration purposes only. It is not a certified medical device. The implementer is responsible for any clinical validation required by their jurisdiction.*

## Instalación

### Dependencias del sistema

`cirrus_pdf_extractor.py` requiere **poppler** para renderizado de PDF:

```bash
# macOS
brew install poppler

# Windows
conda install -c conda-forge poppler
```

### Desarrollo (macOS / Linux)

```bash
# Requisito: Python >=3.11
git clone https://github.com/oftalmos-org/transducin.git
cd transducin
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### Producción — Oracle Linux 9 / RHEL 9

```bash
# Como root:
git clone https://github.com/oftalmos-org/transducin.git /opt/transducin
bash /opt/transducin/deploy/install.sh
```

El script `deploy/install.sh`:
1. Instala Python 3.11 vía `dnf`
2. Crea usuario de sistema `transducin`
3. Crea directorios `/data/input/REVO`, `/data/output`, `/var/log/transducin`
4. Instala el paquete en `/opt/transducin/venv/`
5. Registra e inicia el servicio systemd `transducin.service`

Configurar Orthanc en `/etc/systemd/system/transducin.service` y recargar:

```bash
# Editar host/port según infraestructura
systemctl edit transducin.service
# Agregar en [Service]:
#   ExecStart=... --orthanc-host <IP_ORTHANC> --orthanc-port 4242

systemctl daemon-reload
systemctl start transducin
journalctl -fu transducin
```

## Uso

```bash
# Watcher producción
python -m transducin.hot_folder_watcher \
    --watch input/REVO \
    --output Output \
    --orthanc-host <ORTHANC_HOST> \
    --orthanc-port 4242

# Prueba local sin C-STORE (procesar existentes)
python -m transducin.hot_folder_watcher \
    --watch input/REVO \
    --output Output \
    --no-cstore \
    --process-existing

# Convertir .opt Revo a DICOM directamente (B-scans + imágenes en-face)
python -m transducin.revo_opt_reader input/REVO/archivo.opt -o Output/

# Verificar SR generado
python transducin/verify_sr.py Output/sr/archivo_SR.dcm

# Self-tests de módulos
python -m transducin.noel_id
python -m transducin.opt_extractor
python -m transducin.sr_builder
python -m transducin.verify_sr
```

## Variables de entorno / CLI

El watcher acepta configuración vía variables de entorno o argumentos CLI:

| Variable env | Argumento CLI | Default |
|---|---|---|
| `ORTHANC_HOST` | `--orthanc-host` | `<ORTHANC_HOST>` |
| `ORTHANC_PORT` | `--orthanc-port` | `4242` |
| `ORTHANC_AET` | `--orthanc-aet` | `ORTHANC` |
| `TRANSDUCIN_AET` | — | `TRANSDUCIN` |

## Infraestructura de referencia

| Componente | Dirección |
|---|---|
| Orthanc PACS | `<ORTHANC_HOST>:4242` (DICOM) / `:8042` (REST) |
| OHIF Viewer | `http://<ORTHANC_HOST>:3000` |
| AET local | `TRANSDUCIN` |
| Input Revo | `input/REVO/` |
| Logs | `logs/watcher_YYYYMMDD.log` |

## Stack tecnológico

| Librería | Versión | Rol |
|---|---|---|
| **Python** | ≥3.11 | Runtime (`.venv/bin/python` — producción Oracle Linux) |
| [pydicom](https://pydicom.github.io/) | ≥3.0.1 | Lectura/escritura DICOM, VR, UIDs |
| [highdicom](https://highdicom.readthedocs.io/) | ≥0.27.0 | SR TID 1500/1501 — `ComprehensiveSR`, `MeasurementReport`, `FindingSite` |
| [pynetdicom](https://pynetdicom.readthedocs.io/) | latest | C-STORE SCU hacia Orthanc |
| [watchdog](https://python-watchdog.readthedocs.io/) | latest | Hot folder — `FileSystemEventHandler` |
| [numpy](https://numpy.org/) | latest | Arrays pixel data y segmentación OCT |
| [pdfplumber](https://github.com/jsvine/pdfplumber) | latest | Extracción de texto e imágenes de PDFs Cirrus |
| [pdf2image](https://github.com/Belval/pdf2image) | ≥1.17.0 | Renderizado PDF → PIL Image (requiere poppler) |
| zlib | stdlib | Descompresión chunks `.opt` Revo FC130 |

**Estándares DICOM:**
- `OphthalmicTomographyImageStorage` `1.2.840.10008.5.1.4.1.1.77.1.5.4`
- `OphthalmicPhotography8BitImageStorage` `1.2.840.10008.5.1.4.1.1.77.1.5.1` (SLO, ENFACE, ANGPRV)
- SR TID 1500/1501 Measurement Report con anatomic context

## Historial de versiones

| Versión | Descripción |
|---|---|
| **1.0.0** | Primera versión pública. Pipeline Revo FC130 completo (B-scans, SLO, ENFACE, ANGPRV, OCTA_MIP). Extracción clínica: CMT, ETDRS 9 sectores, pRNFL, mRNFL, mGCIPL, biometría, C/D ratio. SR TID 1500/1501 con anatomic context. Cirrus HD-OCT `.EX.DCM` tags privados + SR vendor-aware. Cirrus PDF OCR. PTS 925Wi perimetría. Protocolo NOEL PatientID. |

## Estructura del repositorio

```
Transducin/
├── transducin/              # Módulos propios RetinaOS
│   ├── noel_id.py
│   ├── clinical_data.py
│   ├── opt_extractor.py
│   ├── revo_opt_reader.py
│   ├── sr_builder.py
│   ├── cirrus_extractor.py
│   ├── cirrus_pdf_extractor.py
│   ├── pts925_extractor.py
│   ├── hot_folder_watcher.py
│   └── verify_sr.py
├── deploy/                  # Despliegue producción
│   ├── transducin.service   # Systemd unit (Oracle Linux 9 / RHEL 9)
│   └── install.sh           # Script de instalación automatizada
├── input/REVO/              # Archivos .opt entrada (no versionado)
├── Output/                  # DICOM generados (no versionado)
└── logs/                    # Logs watcher (no versionado)
```

## Scripts utilitarios

Herramientas de mantenimiento PACS en la raíz del repositorio (requieren conexión a Orthanc vía `.env`):

| Script | Función |
|---|---|
| `backfill_cirrus_studydesc.py` | Retroactive StudyDescription fix for Cirrus studies already in Orthanc |
| `backfill_revo_studydesc.py` | Retroactive StudyDescription fix for Revo studies already in Orthanc |
| `fix_cirrus_merges.py` | Repair merged Cirrus study UIDs (split incorrectly merged studies) |
| `fix_cirrus_pids.py` | Repair Cirrus PatientID mismatches against NOEL protocol |
| `reprocess_cirrus.py` | Reprocess Cirrus `.EX.DCM` batch (re-extract + re-upload SR) |
| `reprocess_cooked_opts.py` | Reprocess already-converted `.opt` files (SR only, skip image re-export) |
| `reprocess_cirrus_transpose.py` | Fix transposed Cirrus series (B-scan orientation correction) |
| `retag_cirrus_studies.py` | Retag Cirrus study metadata in Orthanc |
| `scan_type_counter.py` | Count scan types in corpus (source data for paper Table 3) |
| `scripts/batch_opt_to_dicom.py` | Batch `.opt` → DICOM locally without C-STORE (set `--input`/`--output`) |

## Licencia

Apache 2.0 — Copyright (c) 2026 Jesús Noel Jaurrieta Hinojos. Ver [LICENSE](LICENSE).

> Multi-vendor support (Heidelberg, Topcon, Bioptigen) planned for v1.2 via
> [oct-converter](https://github.com/marksgraham/OCT-Converter) as an optional dependency.
>
> SOCT documentation available from Optopol Technology upon request.
