# Changelog

All notable changes to Transducin are documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [1.0.0] — 2026-04-27

Primera versión pública. Pipeline completo para:

- **Optopol Revo FC130**: parser `.OPT` con especificación del formato chunk-container documentada por primera vez; B-scans → `OphthalmicTomographyImageStorage`; imágenes SLO/ENFACE/ANGPRV/OCTA_MIP → `OphthalmicPhotography8Bit`; extracción clínica CMT, ETDRS 9 sectores, pRNFL (anillo peripapillar), mRNFL, mGCIPL, biometría (AL, CCT, K1/K2), C/D ratio; SR TID 1500/1501 con SNOMED-CT y UCUM
- **Zeiss Cirrus HD-OCT**: extracción de CMT, ETDRS, RNFL, GCL/IPL desde tags privados coding scheme `99CZM` en `.EX.DCM`; SR TID 1500 vendor-aware; pipeline OCR para PDFs Cirrus (pdfplumber + pdf2image, MIT)
- **PTS 925Wi Optopol**: DICOM Secondary Capture (SOP OPV `1.2.840.10008.5.1.4.1.1.80`) con PDF embebido; `pts925_watcher` como servicio Orthanc-polling
- **Lateralidad**: inferida del signo aritmético de OCTPARAMS tag 23 (posición foveal horizontal en mm); validado al 100% en 18 archivos de dos modelos y tres versiones de software (11.5.0–21.1.2)
- **DMARKERS**: discriminador determinístico optic_nerve vs macular; override sobre inferencia por dimensiones
- **AnatomicRegionSequence**: T-AA700 (segmento anterior), T-AA610 (segmento posterior), T-AA630 (nervio óptico) en todas las instancias de imagen y SR per DICOM CP-1676
- **PixelSpacing calibrado**: derivado de PARAMS.DAT (µm/px real por scan)
- **Fechas de adquisición**: 8 tags DICOM (`AcquisitionDate/Time`, `ContentDate/Time`, `StudyDate/Time`, `SeriesDate/Time`) poblados desde UID del scan en PARAMS.DAT
- **PatientID**: vacío (`""`) cuando no hay NOEL ID — nunca `"UNKNOWN"` en producción
- **Protocolo NOEL**: `apellido_paterno[:2] + apellido_materno[0] + nombre[0] + YYYYMMDD` como PatientID universal
- **C-STORE → Orthanc PACS** vía pynetdicom; hot-folder watcher multivendor con `watchdog`
- **Licencia Apache 2.0**: PyMuPDF (AGPL-3.0) reemplazado por pdfplumber + pdf2image (MIT)
- **JOSS**: `paper.md` + `paper.bib` en raíz del repo
- **Tests**: 22 pytest cubriendo magic bytes, CMT, ETDRS, BM/BOTTOM fallback, NOEL ID, OCTPARAMS tag 23, tipos de scan

[1.0.0]: https://github.com/oftalmos-org/transducin/releases/tag/v1.0.0
