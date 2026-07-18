# Transducin 1.0.0

[![medRxiv](https://img.shields.io/badge/medRxiv-10.64898%2F2026.07.14.26357256-B31B1B.svg)](https://doi.org/10.64898/2026.07.14.26357256)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.20031717.svg)](https://doi.org/10.5281/zenodo.20031717)

> **⚠️ Research use only.** Transducin is a research pipeline for parsing proprietary OCT formats and generating standard DICOM Structured Reports. It has not received regulatory clearance or approval from any health authority (COFEPRIS, FDA, CE marking, or equivalent) for clinical use. It performs no diagnostic classification or interpretation — it reproduces, in standard DICOM form, quantitative measurements already computed by the source device's own firmware. **This software must not be used for clinical diagnosis, treatment decisions, or patient management.**

Multi-vendor OCT → DICOM pipeline for [RetinaOS](https://github.com/oftalmos-org/transducin). Converts proprietary ophthalmic OCT files to standard DICOM, extracts clinical measurements (CMT, ETDRS 9-sector grid, mRNFL/pRNFL, mGCIPL, biometry), generates TID 1500 Structured Reports with full anatomic context, and sends them to an Orthanc PACS via C-STORE.

## Demo

Transducin generating a TID 1500 SR from a Revo FC130 `.OPT` file and displaying it in OHIF Viewer:

https://github.com/oftalmos-org/transducin/raw/main/docs/demo/Transducin_in_OHIF_example_video.mp4

## Roadmap

### v1.0.0 — Available

- [x] Full Revo FC130 pipeline: B-scans, SLO, ENFACE, ANGPRV, OCTA_MIP → `OphthalmicTomographyImageStorage`
- [x] All Revo FC130 scan types: macular, optic_nerve, angio, hd_line, ultra_wide, wide_field, biometry, fundus (detection by chunk presence and dimensions)
- [x] Clinical extraction: CMT, ETDRS 9-sector grid, pRNFL (peripapillary), mRNFL, mGCIPL, biometry (AL, CCT), C/D ratio
- [x] SR TID 1500/1501 with full anatomic context (FindingSite, laterality, S/N/I/T topographic modifiers)
- [x] Standardized PatientID protocol
- [x] Multi-vendor hot folder watcher
- [x] C-STORE → Orthanc PACS
- [x] Cirrus HD-OCT `.EX.DCM`: CMT/RNFL/GCL from private tags `(0073,xxxx)` + SR TID 1500
- [x] Cirrus PDF export: Macular Thickness OU, ONH+RNFL OU, Ganglion Cell OU (minimum mGCIPL)
- [x] PTS 925Wi Optopol (perimetry): DICOM Secondary Capture (Modality OPV) with embedded PDF

### v1.2 — Planned

- [ ] Heidelberg Spectralis `.e2e`, Topcon `.fds`/`.fda`, Bioptigen `.OCT` via oct-converter
- [ ] Visual field SR TID 6002 (Supplement 247 — pending highdicom [#406](https://github.com/ImagingDataCommons/highdicom/issues/406))
- [ ] OCT SR migration to DICOM Supplement 247 TID 6001–6007
- [ ] Revo FC130 PATIENT.DAT decryption (requires vendor key or reverse engineering)

## Architecture

```
input/REVO/*.opt                input/CIRRUS/*.EX.DCM
        │                               │
 revo_opt_reader ←── segmentation    cirrus_extractor
 (B-scans + SLO/ENFACE/ANGPRV        (private tags 0073,xxxx)
  + ETDRS + mRNFL + mGCIPL                │
  + biometry + SQI + TRAJ)               │
        │                               │
 opt_extractor                          ─┴─────────────┐
 (filename + PARAMS.DAT                             │
  + MYOPI JSON + scan type)               OCTClinicalData
        │                                           │
        └───────────────────────────────────────────┘
                            │
                       sr_builder  (TID 1500: anatomic context + FindingSite + laterality)
                            │         groups: macular · peripapillary · biometry
                  hot_folder_watcher
                            │
                  C-STORE → Orthanc PACS
```

## Modules (`transducin/`)

| Module | Function |
|---|---|
| `clinical_data.py` | Dataclasses: `OCTClinicalData`, `ETDRSGrid`, `RNFLSectors`, `VisualFieldData` (PTS) |
| `opt_extractor.py` | Revo FC130 `.opt` metadata: filename, PARAMS.DAT zlib, MYOPI JSON (biometry), scan type detection by chunk presence and dimensions |
| `revo_opt_reader.py` | `.opt` B-scans → `OphthalmicTomographyImageStorage` + SLO/ENFACE/ANGPRV/OCTA_MIP → `OphthalmicPhotography8Bit` + CMT/ETDRS/pRNFL/mRNFL/mGCIPL from segmentation |
| `sr_builder.py` | DICOM SR TID 1500 — macular, peripapillary, and biometry groups; vendor-aware (Optopol / Zeiss) |
| `cirrus_extractor.py` | Extracts CMT, ETDRS, RNFL, C/D from CZM private tags `(0073,xxxx)` in `.EX.DCM` files |
| `cirrus_pdf_extractor.py` | Cirrus PDF OCR: Macular Thickness OU, ONH+RNFL OU, Ganglion Cell OU → `OCTClinicalData` |
| `pts925_extractor.py` | PTS 925 Optopol perimetry → `VisualFieldData` → SOP `1.2.840.10008.5.1.4.1.1.80` |
| `hot_folder_watcher.py` | Multi-vendor watcher: `.opt` `.dcm` `.EX.DCM` `.pdf` `.OCT` `.e2e` `.fds` `.fda` → pipeline → C-STORE |
| `verify_sr.py` | Validates SR: TID 1500, PatientID, SNOMED-CT (9 codes), Orthanc REST query |

## Supported Devices

| Manufacturer | Device | Format | Status |
|---|---|---|---|
| Optopol | Revo FC130 | `.opt` | B-scans + SLO/ENFACE/ANGPRV + ETDRS + mRNFL + mGCIPL + MYOPI biometry |
| Carl Zeiss Meditec | Cirrus HD-OCT | `.ex.dcm` | CMT/RNFL/GCL from private tags `(0073,xxxx)` + SR TID 1500 |
| Bioptigen / Leica | various | `.OCT` | Planned v1.2 via oct-converter |
| Topcon | DRI OCT Triton/Atlantis | `.fds`, `.fda` | Planned v1.2 via oct-converter |
| Heidelberg Engineering | Spectralis | `.e2e` | Planned v1.2 via oct-converter |

## Supported Scan Types (Revo FC130)

Detection based on chunks present (ANGPRV, DMARKERS, EYE) and `n_bscans × n_ascans` dimensions.

| Type | Dimensions (B-scans × A-scans) | Output images |
|---|---|---|
| `macular` | 168 × 1024 (6×6 mm cube) | `_OCT.dcm`, `_SLO.dcm`, `_ENFACE.dcm` |
| `optic_nerve` | 192 × 640 (6 mm ONH cube) | `_OCT.dcm`, `_SLO.dcm` |
| `angio` | 320 × 320 (3 mm OCTA) | `_OCT.dcm`, `_SLO.dcm`, `_ENFACE.dcm`, `_ANGPRV.dcm`, `_OCTA_MIP.dcm` |
| `hd_line` | 18–25 × 1024 (HD raster) | `_OCT.dcm`, `_SLO.dcm` |
| `ultra_wide` | 1 × 10240 or 6 × 8192 (14–16 mm field) | `_OCT.dcm`, `_SLO.dcm` |
| `wide_field` | 5 × 1536 (12 mm field) | `_OCT.dcm`, `_SLO.dcm` |
| `biometry` | BMETR + MYOPI JSON (zlib) | data only → biometry SR |
| `fundus` | Color_fundus (from filename) | skipped (no measurements) |

## .OPT Format Technical Notes

- **Laterality**: inferred from the arithmetic sign of OCTPARAMS tag 23 (foveal horizontal position in mm); validated at 100% across 18 files from two device models and three software versions.
- **Calibrated PixelSpacing**: derived from scan parameters in the PARAMS.DAT chunk; all `OphthalmicTomographyImageStorage` instances include `PixelSpacing` with real µm/px scale.
- **AnatomicRegionSequence**: present in all image and SR instances; encoded with SNOMED-CT SRT (T-AA610 posterior segment, T-AA700 anterior segment) per DICOM CP-1676.

## Clinical Measurements and SR

Structured Reports follow TID 1500/1501 with full anatomic context. Each measurement includes `FindingSite` (anatomic site), SNOMED-CT laterality, and topographic modifier where applicable. `AlgorithmIdentification` references `Transducin/<__version__>` in each group.

### Macular group

| Measurement | SCT code | Unit | Site |
|---|---|---|---|
| CMT — Central Macular Thickness | `422453003` | µm | Fovea centralis |
| ETDRS C (central subfield) | `422453003` | µm | Fovea centralis |
| ETDRS S1/N1/I1/T1 (1–3 mm ring) | `422399008` | µm | Retina + S/N/I/T mod. |
| ETDRS S2/N2/I2/T2 (3–6 mm ring) | `422399008` | µm | Retina + S/N/I/T mod. |
| mRNFL global and S/I sectors | `422995006` | µm | Retina |
| mGCIPL global and S/I sectors | `422455005` | µm | Retina |

### Peripapillary group (optic disc scan)

| Measurement | SCT code | Unit | Site |
|---|---|---|---|
| pRNFL global | `422995006` | µm | Optic nerve head |
| pRNFL S/N/I/T sectors | `422995006` | µm | Optic nerve head + mod. |
| C/D ratio | `363932005` | — | Optic nerve head |

### Biometry group (BMETR scan)

| Measurement | SCT code | Unit |
|---|---|---|
| Axial length (AL) | `252017007` | mm |
| CCT — central corneal thickness | `397545004` | mm |
| K1 — flat meridian keratometry | `252014009` | mm |
| K2 — steep meridian keratometry | `252016006` | mm |

## Regulatory Notice

This software is provided for research and technical integration purposes only. It is not a certified medical device. The implementer is responsible for any clinical validation required by their jurisdiction.

## Installation

### System dependencies

`cirrus_pdf_extractor.py` requires **poppler** for PDF rendering:

```bash
# macOS
brew install poppler

# Windows
conda install -c conda-forge poppler
```

### Development (macOS / Linux)

```bash
# Requires Python >=3.11
git clone https://github.com/oftalmos-org/transducin.git
cd transducin
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### Production — Oracle Linux 9 / RHEL 9

```bash
# As root:
git clone https://github.com/oftalmos-org/transducin.git /opt/transducin
bash /opt/transducin/deploy/install.sh
```

The `deploy/install.sh` script:
1. Installs Python 3.11 via `dnf`
2. Creates a `transducin` system user
3. Creates directories `/data/input/REVO`, `/data/output`, `/var/log/transducin`
4. Installs the package into `/opt/transducin/venv/`
5. Registers and starts the `transducin.service` systemd unit

Configure Orthanc in `/etc/systemd/system/transducin.service` and reload:

```bash
# Edit host/port for your infrastructure
systemctl edit transducin.service
# Add under [Service]:
#   ExecStart=... --orthanc-host <IP_ORTHANC> --orthanc-port 4242

systemctl daemon-reload
systemctl start transducin
journalctl -fu transducin
```

## Usage

```bash
# Production watcher
python -m transducin.hot_folder_watcher \
    --watch input/REVO \
    --output Output \
    --orthanc-host <ORTHANC_HOST> \
    --orthanc-port 4242

# Local test without C-STORE (process existing files)
python -m transducin.hot_folder_watcher \
    --watch input/REVO \
    --output Output \
    --no-cstore \
    --process-existing

# Convert Revo .opt directly to DICOM (B-scans + en-face images)
python -m transducin.revo_opt_reader input/REVO/file.opt -o Output/

# Verify generated SR
python transducin/verify_sr.py Output/sr/file_SR.dcm

# Module self-tests
python -m transducin.opt_extractor
python -m transducin.sr_builder
python -m transducin.verify_sr
```

## Environment Variables / CLI

The watcher accepts configuration via environment variables or CLI arguments:

| Env variable | CLI argument | Default |
|---|---|---|
| `ORTHANC_HOST` | `--orthanc-host` | `<ORTHANC_HOST>` |
| `ORTHANC_PORT` | `--orthanc-port` | `4242` |
| `ORTHANC_AET` | `--orthanc-aet` | `ORTHANC` |
| `TRANSDUCIN_AET` | — | `TRANSDUCIN` |

## Reference Infrastructure

| Component | Address |
|---|---|
| Orthanc PACS | `<ORTHANC_HOST>:4242` (DICOM) / `:8042` (REST) |
| OHIF Viewer | `http://<ORTHANC_HOST>:3000` |
| Local AET | `TRANSDUCIN` |
| Revo input | `input/REVO/` |
| Logs | `logs/watcher_YYYYMMDD.log` |

## Technology Stack

| Library | Version | Role |
|---|---|---|
| **Python** | ≥3.11 | Runtime (`.venv/bin/python` — Oracle Linux production) |
| [pydicom](https://pydicom.github.io/) | ≥3.0.1 | DICOM read/write, VR, UIDs |
| [highdicom](https://highdicom.readthedocs.io/) | ≥0.27.0 | SR TID 1500/1501 — `ComprehensiveSR`, `MeasurementReport`, `FindingSite` |
| [pynetdicom](https://pynetdicom.readthedocs.io/) | latest | C-STORE SCU to Orthanc |
| [watchdog](https://python-watchdog.readthedocs.io/) | latest | Hot folder — `FileSystemEventHandler` |
| [numpy](https://numpy.org/) | latest | Pixel data arrays and OCT segmentation |
| [pdfplumber](https://github.com/jsvine/pdfplumber) | latest | Text and image extraction from Cirrus PDFs |
| [pdf2image](https://github.com/Belval/pdf2image) | ≥1.17.0 | PDF → PIL Image rendering (requires poppler) |
| zlib | stdlib | Revo FC130 `.opt` chunk decompression |

**DICOM Storage Classes:**
- `OphthalmicTomographyImageStorage` `1.2.840.10008.5.1.4.1.1.77.1.5.4`
- `OphthalmicPhotography8BitImageStorage` `1.2.840.10008.5.1.4.1.1.77.1.5.1` (SLO, ENFACE, ANGPRV)
- SR TID 1500/1501 Measurement Report with anatomic context

## Version History

| Version | Description |
|---|---|
| **1.0.0** | First public release. Full Revo FC130 pipeline (B-scans, SLO, ENFACE, ANGPRV, OCTA_MIP). Clinical extraction: CMT, ETDRS 9-sector grid, pRNFL, mRNFL, mGCIPL, biometry, C/D ratio. SR TID 1500/1501 with anatomic context. Cirrus HD-OCT `.EX.DCM` private tags + vendor-aware SR. Cirrus PDF OCR. PTS 925Wi perimetry. Standardized PatientID protocol. |

## Repository Structure

```
Transducin/
├── transducin/              # Core RetinaOS modules
│   ├── clinical_data.py
│   ├── opt_extractor.py
│   ├── revo_opt_reader.py
│   ├── sr_builder.py
│   ├── cirrus_extractor.py
│   ├── cirrus_pdf_extractor.py
│   ├── pts925_extractor.py
│   ├── hot_folder_watcher.py
│   └── verify_sr.py
├── deploy/                  # Production deployment
│   ├── transducin.service   # Systemd unit (Oracle Linux 9 / RHEL 9)
│   └── install.sh           # Automated install script
├── input/REVO/              # .opt input files (not versioned)
├── Output/                  # Generated DICOM (not versioned)
└── logs/                    # Watcher logs (not versioned)
```

## Utility Scripts

PACS maintenance tools at the repository root (require Orthanc connection via `.env`):

| Script | Function |
|---|---|
| `backfill_cirrus_studydesc.py` | Retroactive StudyDescription fix for Cirrus studies already in Orthanc |
| `backfill_revo_studydesc.py` | Retroactive StudyDescription fix for Revo studies already in Orthanc |
| `fix_cirrus_merges.py` | Repair merged Cirrus study UIDs (split incorrectly merged studies) |
| `fix_cirrus_pids.py` | Repair Cirrus PatientID mismatches against the standardized protocol |
| `reprocess_cirrus.py` | Reprocess Cirrus `.EX.DCM` batch (re-extract + re-upload SR) |
| `reprocess_cooked_opts.py` | Reprocess already-converted `.opt` files (SR only, skip image re-export) |
| `reprocess_cirrus_transpose.py` | Fix transposed Cirrus series (B-scan orientation correction) |
| `retag_cirrus_studies.py` | Retag Cirrus study metadata in Orthanc |
| `scan_type_counter.py` | Count scan types in corpus (source data for paper Table 3) |
| `scripts/batch_opt_to_dicom.py` | Batch `.opt` → DICOM locally without C-STORE (set `--input`/`--output`) |

## License

Apache 2.0 — Copyright (c) 2026 Jesús Noel Jaurrieta Hinojos. See [LICENSE](LICENSE).

> Multi-vendor support (Heidelberg, Topcon, Bioptigen) planned for v1.2 via
> [oct-converter](https://github.com/marksgraham/OCT-Converter) as an optional dependency.
>
> SOCT documentation available from Optopol Technology upon request.
