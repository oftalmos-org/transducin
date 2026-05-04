# Análisis Técnico de Estándares DICOM y SNOMED-CT para Modalidades Avanzadas de Tomografía de Coherencia Óptica: Hacia la Interoperabilidad en el Proyecto Transducin

La evolución de la tomografía de coherencia óptica (OCT) ha transitado desde una herramienta de nicho para la visualización macular hacia una plataforma multimodal indispensable que abarca desde la biometría axial de alta precisión hasta la angiografía sin contraste y la obtención de imágenes de campo ultra-amplio (UWF). En este contexto, el desarrollo de Transducin, un pipeline de código abierto para la conversión de archivos propietarios a estándares DICOM, enfrenta desafíos significativos relacionados con la madurez de los Information Object Definitions (IODs) y la cobertura semántica de SNOMED-CT.1 Este informe técnico detalla la arquitectura de interoperabilidad necesaria para soportar estas modalidades avanzadas, analizando los suplementos específicos de DICOM, las jerarquías de SNOMED y las limitaciones actuales en bibliotecas de implementación de alto nivel como highdicom.

## 1. Tomografía de Campo Ultra-Amplio (UWF-OCT): Arquitectura Geométrica y Codificación Anatómica

La tecnología UWF-OCT ha superado las limitaciones de los tradicionales 30-50 grados de visión, permitiendo capturas que superan los 100 grados y alcanzan hasta los 220 grados de la superficie retiniana.4 Sin embargo, la estandarización de este tipo de escaneo dentro de DICOM no ha resultado en una clase SOP dedicada, sino en la adaptación de estructuras existentes para manejar la complejidad geométrica y anatómica.

### 1.1 Persistencia de la Clase SOP Ophthalmic Tomography Image Storage

A pesar de la singularidad clínica de las imágenes UWF, no existe una clase SOP específica para UWF-OCT. Todo el volumen de datos, ya sea capturado por sistemas de barrido espectral (SD-OCT) o por fuentes de barrido sintonizable (SS-OCT), debe ser codificado bajo la clase SOP Ophthalmic Tomography Image Storage (UID 1.2.840.10008.5.1.4.1.1.77.1.5.4).1 La distinción entre un escaneo convencional y uno UWF se delega a los metadatos de los parámetros de adquisición y a la descripción de la región anatómica.

Un punto de confusión común en la comunidad de desarrolladores de informática médica es la aplicabilidad del Suplemento 173 (Wide Field Ophthalmic Photography). El análisis exhaustivo del estándar confirma que el Suplemento 173 se limita exclusivamente a objetos de imagen fotográfica (fotografía de fondo de ojo) y no se extiende a la tomografía.8 Para el OCT, las capacidades de "campo amplio" deben describirse utilizando atributos dentro del módulo Ophthalmic Tomography Acquisition Parameters.

### 1.2 Diferenciación mediante Atributos de Campo de Visión (FOV) y Geometría

Para que un objeto DICOM sea identificado correctamente como UWF, el atributo Horizontal Field of View (0022,000C) debe poblarse con el valor angular correspondiente, que en sistemas como el Optos Silverstone supera típicamente los 110 grados.10 Además, para manejar la distorsión inherente a la proyección de una superficie semiesférica sobre un plano bidimensional, se deben utilizar los tags de forma y dimensión del campo de visión.

La inclusión de la Transformation Algorithm Sequence es una recomendación crítica para Transducin, permitiendo documentar el modelo óptico utilizado para corregir las distorsiones periféricas, garantizando así que las mediciones realizadas sobre el objeto DICOM sean clínicamente reproducibles.14

### 1.3 Codificación SNOMED-CT y Modificadores Anatómicos

En cuanto a la semántica anatómica, el código base para la retina es (T-AA610, SRT, "Retina") o su equivalente conceptual en SNOMED-CT (5665001, SCT, "Retina").15 Sin embargo, no existe un código único pre-coordinado para "imagen retiniana de campo ultra-amplio". La implementación estándar requiere el uso de la Anatomic Region Sequence (0008,2218) combinada con la Anatomic Region Modifier Sequence (0008,2220).

La distinción entre UWF posterior y UWF anterior segment se maneja mediante la secuencia de descriptores de volumen introducida por la lógica de segmentación. Para Transducin, la estrategia recomendada es utilizar el código de Retina (T-AA610) con un modificador que especifique la extensión periférica. La International Widefield Imaging Study Group (IWISG) define el límite entre campo amplio y ultra-amplio en las ampollas de las venas vorticosas.4

Para el segmento anterior UWF (utilizado para visualizar el ángulo iridocorneal en 360 grados), se debe emplear (T-AA700, SRT, "Anterior segment of eye") junto con el tag Ophthalmic En Face Volume Descriptor Sequence (0022,1627) configurado como ANTERIOR.18

## 2. Angiografía por OCT (OCT-A): El Suplemento 197 y la Transición a DICOM 2025c

El Suplemento 197 (Ophthalmic Optical Coherence Tomography for Angiographic Imaging) ha sido formalmente incorporado en las ediciones recientes del estándar DICOM, incluyendo la versión 2025c.20 Este suplemento es vital para Transducin v1.2, ya que permite la separación técnica entre el volumen estructural y el análisis de flujo vascular.

### 2.1 Clases SOP para Volúmenes y Proyecciones En-Face

Una de las decisiones arquitectónicas más importantes en OCT-A es la selección de la clase SOP adecuada para el tipo de dato generado. El OCT-A no produce solo una imagen, sino un conjunto de datos derivados de la diferencia de señal entre cuadros (frames) sucesivos.20

El B-scan Volume Analysis Storage es especialmente potente porque permite almacenar la información de flujo angiográfico voxel por voxel, permitiendo que las estaciones de revisión recalculen slabs de manera dinámica.26 Por otro lado, el En Face Image Storage se utiliza para las proyecciones estáticas derivadas, como los mapas de densidad vascular o las proyecciones de intensidad máxima (MIP) de plexos específicos (superficial, profundo, coriocapilar).20

### 2.2 Limitaciones en highdicom y Implementación Manual

Actualmente, highdicom (incluso en versiones >= 0.27.0) no implementa de forma nativa las clases SOP del Suplemento 197 en su módulo highdicom.sop.28 Esto representa un gap crítico para el equipo de Transducin. La construcción de estos objetos requiere la utilización de pydicom para definir el dataset base y luego inyectar manualmente los tags específicos de angiografía, tales como la Ophthalmic Volumetric Properties Flag (0022,1622) configurada como YES.27

### 2.3 Mediciones Estructuradas (SR) en OCT-A y Gaps en el Suplemento 247

El Suplemento 247 (Eyecare Measurement Templates) es la adición más reciente al estándar (DICOM 2025c) y busca formalizar las mediciones derivadas de imágenes oftalmológicas.31 Sin embargo, el análisis de la Draft Final Text de marzo de 2025 revela vacíos significativos para el OCT-A.

Aunque el Suplemento 247 define TIDs para el grosor macular (TID 6005) y la capa de fibras nerviosas (TID 6004), no incluye plantillas específicas para métricas de OCT-A como el área de la zona avascular foveal (FAZ), la densidad vascular o el índice de perfusión.31 Esto implica que para Transducin, estas mediciones deben seguir codificándose utilizando el TID 1500 (Measurement Report) genérico, empleando conceptos de SNOMED-CT post-coordinados en lugar de las nuevas plantillas especializadas de Eyecare.35

## 3. Topografía y Tomografía Corneal por AS-OCT: Análisis de Modelos de Datos

La evaluación del segmento anterior mediante OCT ha convergido con las técnicas tradicionales de topografía (Placido) y tomografía (Scheimpflug), pero sus representaciones en DICOM diffieren según el propósito clínico.37

### 3.1 El Suplemento 168 y los Mapas de Curvatura

Para representar la superficie corneal (topografía), el estándar ofrece el Suplemento 168 (Corneal Topography Map Storage), que define la clase SOP Corneal Topography Map Storage (1.2.840.10008.5.1.4.1.1.82.1).7 Esta clase es ideal para almacenar mapas de curvatura axial, tangencial y de elevación. El Suplemento 168 incluye tags específicos para índices de regularidad:

Surface Regularity Index (0046,0230): Mide fluctuaciones locales en la potencia corneal.39

Surface Asymmetry Index (0046,0232): Suma cambios de potencia meridionales.39

Keratoconus Prediction Index (0046,0236): Indica la probabilidad de ectasia.39

### 3.2 Mediciones Refractivas según el Suplemento 130

Si el objetivo del escaneo AS-OCT del Revo FC130 es extraer valores numéricos para cálculo de lente intraocular (IOL), se debe aplicar el Suplemento 130 (Ophthalmic Refractive Measurements Storage and SR).40 Este suplemento no es para imágenes, sino para el almacenamiento de valores discretos de queratometría y refracción. La clase SOP Keratometry Measurements Storage (1.2.840.10008.5.1.4.1.1.78.3) es el destino apropiado para los valores K1 y K2 derivados del OCT.7

### 3.3 SNOMED-CT: Topografía vs. Tomografía vs. AS-OCT

Existe una distinción semántica clara en SNOMED-CT que Transducin debe respetar para garantizar la interoperabilidad con sistemas de registro clínico electrónico (EHR).

Es fundamental notar que las plantillas para topografía corneal que originalmente estaban previstas para el Suplemento 247 fueron removidas en las revisiones recientes y han sido postergadas para futuras actualizaciones del estándar.31 Por lo tanto, no existe actualmente un TID especializado en el Suplemento 247 para topografía.

## 4. Biometría OCT y Mediciones Axiales: Suplemento 144

El dispositivo Optopol Revo FC130 realiza mediciones biométricas mediante OCT de alta velocidad, una funcionalidad que requiere una implementación estricta del Suplemento 144 (Ophthalmic Axial Measurements Storage SOP Classes).18

### 4.1 La Clase SOP Ophthalmic Axial Measurements Storage

Este IOD está diseñado específicamente para dispositivos de biometría que producen valores numéricos junto con, opcionalmente, imágenes de control de calidad.43 La clase SOP correspondiente es Ophthalmic Axial Measurements Storage (1.2.840.10008.5.1.4.1.1.78.7).43

Para Transducin, la implementación de biometría debe capturar los siguientes tags críticos del Grupo 0022:

Ophthalmic Axial Length (0022,1019): Valor de la longitud axial en milímetros.18

Ophthalmic Axial Length Measurements Type (0022,1010): Define si es TOTAL LENGTH (medición única) o LENGTH SUMMATION (suma de segmentos como CCT+ACD+LT).44

Lens Thickness (0022,1130): Grosor del cristalino.18

Central Corneal Thickness (0022,1131): Grosor corneal central.18

### 4.2 Integración en TID 1500 SR vs. IOD Dedicado

Una arquitectura recomendada por DICOM WG-09 es utilizar el IOD de OAM para la persistencia de los valores primarios del dispositivo.43 Sin embargo, los resultados de cálculos derivados, como el poder de la IOL, pueden y deben ser referenciados en un informe estructurado SR que utilice el TID 1500 para consolidar la evidencia del examen.43

highdicom no tiene soporte nativo para el Suplemento 144.30 Esto significa que la conversión de biometría en Transducin v1.2 deberá gestionarse mediante la construcción manual de los módulos de Ophthalmic Axial Measurements en un objeto pydicom.dataset.Dataset, utilizando el perfil de highdicom.sr.StructuredReport solo para la capa de informe final.47

## 5. Gaps Documentados en el Suplemento 247 (DICOM 2025c)

El Suplemento 247 representa el esfuerzo por estandarizar el contenido semántico de los informes oftalmológicos, centrándose en mediciones "clave" para el cuidado del paciente.32

### 5.1 Cobertura de Plantillas (TIDs)

El Suplemento 247 define los siguientes TIDs, los cuales están actualmente en proceso de integración en highdicom mediante el PR #407 50:

TID 6002: Mediciones clave de campo visual.35

TID 6003: Mediciones del disco óptico (cup/disc ratio).34

TID 6004: Mediciones de la capa de fibras nerviosas de la retina (RNFL).40

TID 6005: Mediciones de grosor macular (subcampos ETDRS).31

TID 6006: Mediciones de la capa de células ganglionares (GCL).34

### 5.2 Gaps Identificados para la Publicación Científica (Paper 6)

Ausencia de UWF: No hay descriptores semánticos para la periferia retiniana fuera de los sectores ETDRS estándar de 6mm.31

Omisión de OCT-A: Las métricas vasculares (densidad de vasos, zona avascular) no tienen un contenedor dedicado en el Suplemento 247, obligando al uso de plantillas ROI genéricas (TID 6009).31

Remoción de Topografía: Aunque originalmente planeada, fue removida del suplemento final y será abordada en una revisión futura.31

Desconexión de Biometría: El Suplemento 247 no incluye biometría axial, manteniendo esta modalidad restringida a los atributos de nivel de imagen del Suplemento 144.35

## 6. Estado de Implementación en highdicom y Roadmap de Transducin

El stack técnico de Transducin depende de la evolución de ImagingDataCommons/highdicom.

### 6.1 El Pull Request #407 y el Suplemento 247

El PR #407 es la pieza central para la interoperabilidad de Transducin v1.2. Añade soporte para los TIDs 6001, 6004 y 6005.50 Una vez fusionado, Transducin podrá generar informes de RNFL y mácula utilizando las plantillas estándar en lugar de construcciones TID 1500 genéricas.

### 6.2 Soporte para Suplemento 197 (OCT-A)

No existe actualmente una versión de highdicom que implemente las clases SOP específicas de OCT-A (.7 y.8) de forma nativa.28 Esto sitúa al soporte de OCT-A en Transducin como un desarrollo "manual" sobre pydicom, lo cual es un gap de implementación documentado para el paper.30

## Referencias Bibliográficas (AMA Style)

DICOM Standards Committee. PS3.3: Information Object Definitions. Digital Imaging and Communications in Medicine (DICOM) Standard. 2025. 21

American Academy of Ophthalmology. Recommendations for the Standardization of Images in Ophthalmology. AAO Clinical Statement. 2021. 2

Dicom Systems. Ophthalmology Imaging and Workflow Integrity. 2025.

Choudhry N, et al. Classification and Guidelines for Widefield Imaging: Recommendations from the International Widefield Imaging Study Group. Ophthalmology Retina. 2019;3(10):843-849. 21

Namba H, Xu BY. Anterior Segment Optical Coherence Tomography in Glaucoma. Current Opinion in Ophthalmology. 2025.

Lucente A, et al. Widefield and Ultra-Widefield Retinal Imaging: A Geometrical Analysis. Journal of Clinical Medicine. 2023. 54

DICOM Standards Committee. PS3.4: Service Class Specifications. 2025. 7

DICOM Standards Committee. Supplement 173: Wide Field Ophthalmic Photography Image Storage SOP Classes. 2015. 53

DICOM Standards Committee. PS3.17: Explanatory Information. 2025. 21

DICOM Standards Committee. Module: Ophthalmic Tomography Acquisition Parameters. PS3.3 C.8.17.9. 2025. 10

Papayannis A, et al. Ultra-Wide Field Swept-Source OCT-A in Diabetic Retinopathy. Invest Ophthalmol Vis Sci. 2016;57(12):5490. 55

DICOM Standards Committee. Attributes: Field of View Shape and Dimensions. PS3.3 C.8.11.4. 2024. 12

DICOM Standards Committee. Attribute: Transformation Algorithm Sequence (0022,1513). Supplement 173. 13

Lee WW, et al. Longitudinal UWF-OCT findings in Retinal Detachment. Review of Ophthalmology. 2025. 5

SNOMED International. SNOMED-CT Concept: Retina (5665001). 2025. 15

Silva PS, et al. Peripheral Lesions and Diabetic Retinopathy Severity. Ophthalmology. 2013;120(12):2587-2595. 16

International Widefield Imaging Study Group. Defining Widefield and Ultra-Widefield. 2019. 17

DICOM Standards Committee. Supplement 144: Ophthalmic Axial Measurements Storage SOP Classes. 2010. 18

DICOM Standards Committee. PS3.3: Ophthalmic En Face Volume Descriptor Sequence. 2025. 19

DICOM Standards Committee. Supplement 197: Ophthalmic OCT for Angiographic Imaging. 2017. 21

DICOM Standards Committee. Supplement 168: Corneal Topography Map Storage. 2011. 21

Clunie D. DICOM Status and Approved Supplements. dclunie.com. 2026. 22

Hsiao YS, et al. Measurements on Vessel Length Density and FAZ with OCTA. Invest Ophthalmol Vis Sci. 2015;56(7):1646.

DICOM Standards Committee. SOP Class: Ophthalmic OCT En Face Image Storage (1.2.840.10008.5.1.4.1.1.77.1.5.7). 21

DICOM Standards Committee. SOP Class: Ophthalmic OCT B-scan Volume Analysis Storage (1.2.840.10008.5.1.4.1.1.77.1.5.8). 53

DICOM Standards Committee. Module: Ophthalmic Tomography B-scan Volume Analysis Image. PS3.3 C.8.17.14. 26

DICOM Standards Committee. Supplement 197 Final Text: April 2017. 20

ImagingDataCommons. highdicom: SOP Classes implementation status. GitHub. 2026. 53

Bridge CP. highdicom: High-level Python library for DICOM. GitHub Repository. 2025. 53

Bridge CP, et al. highdicom: a Python Library for Standardized Encoding of Medical Image Annotations. PMC. 2024. 61

DICOM Standards Committee. Supplement 247: Eyecare Measurement Templates. Final Text. 2025. 31

DICOM Standards Committee. Eyecare Measurement Templates Presentation. WG-09 Ophthalmology. 2025. 40

Solomon H. Supplement 247 Scope and Limitations. DICOM Standards Committee News. 2025. 33

DICOM Standards Committee. TID 6003: Optic Disc Key Measurements. Supplement 247. 34

DICOM Standards Committee. PS3.16: Content Mapping Resource - Eyecare TIDs. 2025. 31

SNOMED International. Coded Findings for RNFL and Macula in DICOM SR. 2025. 64

McNabb RP, et al. Quantitative Clinical Corneal Topography. PMC. 2015. 66

New England College of Optometry. Corneal Tomography vs Topography in Ectasia Screening. 2025. 38

DICOM Standards Committee. PS3.3 Annex C.8.25: Corneal Topography Modules. 2024. 39

DICOM Standards Committee. Supplement 130: Ophthalmic Refractive Measurements Storage and SR. 2007. 41

DICOM Standards Committee. PS3.3 C.8.17: Ophthalmic Tomography Parameters. 2025. 2

SNOMED International. SNOMED-CT Observable Entities for Ophthalmology. 2025. 32

Pathak M. Current Concepts and Recent Updates of Optical Biometry. PMC. 2024.

DICOM Standards Committee. PS3.3 C.8.25.14: Ophthalmic Axial Measurements. 2024. 32

DICOM Standards Committee. PS3.6: Data Dictionary - Tag (0022,1019). 2025. 45

DICOM Standards Committee. Supplement 144 Final Text: June 2010. 43

Bridge CP. highdicom: pydicom-based dataset construction. ReadTheDocs. 2025. 30

oftalmos-org. Issue #406: Support for Supplement 247 in highdicom. GitHub. 2025. 48

SNOMED International. Concept: Ocular axial length (251692002). 49

oftalmos-org. PR #407: Feat restructure sr/templates and add Sup 247 TIDs. GitHub. 2025. 21

DICOM Standards Committee. TID 6004: RNFL Key Measurements. Supplement 247. 9

DICOM Standards Committee. TID 6006: GCL Key Measurements. Supplement 247. 34

ImagingDataCommons. highdicom v0.27.0 Release Notes. GitHub. 2025. 72

ImagingDataCommons. highdicom.spatial: 3D volume affine operations. ReadTheDocs. 2025. 73

apint0-media. Issue #174: DICOM SEG for multiframe data. GitHub highdicom. 2022. 71

ChristianEschen. Issue #200: Problem writing XA angiography data. GitHub highdicom. 2022. 48

hackermd. highdicom segmentation frame indexing. GitHub Issues. 2022. 71

Pathak M, et al. Concordance of SS-OCT Biometry (Revo NX) vs IOLMaster 700. PMC. 2024.

Zhou SW, et al. Reliability of SS-OCT/OCTA in Clinical Practice. IOVS. 2025;66(8):83. 75

Optopol. B-OCT Biometry Module Specifications. 2025.

DICOM Standards Committee. Context Group 4208: Mydriatic Agents. PS3.16. 43

Koutsidis C, Stanga P. Benefits of Combining UWF FFA and UWF OCT-A in DR. Invest Ophthalmol Vis Sci. 2025;66(8):1012. 76

Shah SH, et al. Standardized UWF Swept-Source OCT Imaging Protocol. Dovepress. 2025. 77

DICOM Standards Committee. CID 2: Anatomic Modifier. PS3.16. 65

DICOM Standards Committee. CID 244: Laterality Modifier. PS3.16. 78

IHE Eye Care. Key Measurements in DICOM Encapsulated PDF. Technical Framework Supplement. 2019. 52

DICOM Standards Committee. PS3.3 C.8.25.14.1.1.4: Segmental Axial Length. 2024. 69

SNOMED International. SNOMED-CT: Technical and Clinical Documentation. 2026. 49

Sadda S, et al. The Power of Ultra-widefield Imaging and Navigated Peripheral OCT. The Ophthalmologist. 2025. 82

Optos PLC. Defining Ultra-Widefield: IWISG Recommendations. 2025. 17

DICOM Standards Committee. Supplement 240: Heightmap Segmentation. 2024. 83

Papayannis A, et al. UWF-OCT in Posterior Segment Diseases. PMC. 2016. 84

Alanazi et al. White-to-white corneal diameter among different races. PeerJ. 2025;13:19227. 86

SNOMED International. SNOMED-CT Concept: Laser vision correction (444391000124107). 40

DICOM Standards Committee. Attribute: Anterior Chamber Depth Definition Code Sequence (0022,1125). 88

Shahlaee et al. Vessel Density and FAZ in Healthy Eyes: OCTA Study. Invest Ophthalmol Vis Sci. 2016.

Coscas et al. OCTA Vessel Density stratificated by age. France. 2025. 89

Pathak M. IOL power calculations in extremes of axial lengths. India. 2024.

Barrett GD. Barrett Formulas: Strategies to Improve IOL Power Prediction. ResearchGate. 2024.

Falavarjani KG, et al. FAZ and Vessel Density in Healthy Subjects: OCTA Study. J Ophthalmic Vis Res. 2018;13:260-5. 90

highdicom Documentation. Package Modules and Classes overview. 2025. 30

IHE Eye Care. Unified Eye Care Workflow (U-EYECARE). 2019. 52

DICOM Standards Committee. PS3.1: Introduction and Overview. 2025. 6

Optos PLC. MonacoPro next-gen UWF SLO and SD-OCT integration. 2025. 92

DICOM Standards Committee. Supplement 223: Archive Inventory. 2022. 31

Optopol. REVO FC 130 Swept-Source OCT Specifications. 2025. 11

Izatt JA, et al. Correction of Fan Distortion in Anterior Segment OCT. PMC. 2011. 93

Medeiros F. Clustered Glaucoma Testing to Detect Progression. AAO. 2025.

Review of Optometry. Normative values for macula vessel density and FAZ. 2026. 94

Carl Zeiss Meditec. CIRRUS HD-OCT DICOM Conformance Statement v11.7. 2025. 1

Miller JB, et al. U-Net Automated Segmentation for UWF-OCT images. Ophthalmology Science. 2023. 95

Ophthalmology Times. How AI is Reshaping Ophthalmology in 2025.

AAO Young Ophthalmologists. How to Read Corneal Topography. 2025. 37

Topcon Healthcare. Anterior Eye: What you should know about OCT assessment. 2021.

Entokey. Anterior Segment OCT in Glaucoma. 2024.

DICOM Standards Committee. PS3.18: Web Services (DICOMweb). 2025.

American Academy of Ophthalmology. Medical Information Technology Guidelines. 2025. 1

Hussain NH, Hussain AH. Diametric measurement of FAZ in healthy young adults. Int J Retin Vitr. 2016;2:27. 97

Barrett GD. Barrett Universal II IOL Formula Overview. 2025.

DICOM Standards Committee. Module: Multi-frame Functional Groups. PS3.3 C.7.6.16. 19

DICOM Standards Committee. Supplement 190: Volume Rendering Presentation States. 2017. 9

Barrett GD. Optimized Constants for IOL power calculation. APACRS. 2019.

BlueCross BlueShield RI. Medical Policy: Corneal Topography. 2025. 98

VSAC NLM. SNOMEDCT Concepts for Anterior Chamber Disorders. 2024. 99

VSAC NLM. SNOMEDCT Concepts for Contact Lenses (313011004). 2022. 100

malaterre. Issue #235: Cr and Cb subsampling for YBR_FULL_422. GitHub highdicom. 2021. 101

DICOM Standards Committee. PS3.3: Patient and Study Modules. 2025. 70

DICOM Standards Committee. Supplement 95: Real World Value Mapping. 32

Singh et al. Repeatability of SS-OCT based biometry. IOLMaster 700. 2019. 86

Madhumallika Pathak. IOL calculation in difficult situations. PMC. 2024.

Al-Fakih A, et al. PRE using third-generation IOL formulae. PMC. 2024.

highdicom.sr.CodedConcept and pydicom types documentation. 2025. 62

Olawade D, et al. AI systems in ophthalmology diagnostics review. PMC. 2025.

Silva PS, et al. Comparison of UWF and nonmydriatic photography in telehealth. JAMA Ophthalmol. 2016. 16

Hsiao YS, et al. OCTA images generated by AngioVue repeatability study. IOVS. 2015.

Papayannis A, et al. UWF-directed OCT for NVE detection in DR. PMC. 2023. 103

Lee WW, et al. UWF SS-OCT for detecting staphylomas vs MRI. Tokyo. 2025. 5

Works cited

Medical Information Technology - American Academy of Ophthalmology, accessed April 26, 2026, https://www.aao.org/education/medical-information-technology-guidelines

Recommendations for Standardization of Images in Ophthalmology - 2021, accessed April 26, 2026, https://www.aao.org/education/clinical-statement/recommendations-standardization-of-images-in-ophth

Topcon Healthcare Expands Access to Standardized DICOM OCT Imaging Data, accessed April 26, 2026, https://topconhealthcare.com/article/topcon-healthcare-expands-access-to-standardized-dicom-oct-imaging-data/

Coming to terms with 'ultra-widefield' and 'widefield' imaging - Modern Retina, accessed April 26, 2026, https://www.modernretina.com/view/coming-terms-ultra-widefield-and-widefield-imaging

A Review of Ultra-Widefield OCT - Review of Ophthalmology, accessed April 26, 2026, https://www.reviewofophthalmology.com/article/a-review-of-ultrawidefield-oct

Clearing up the language of retinal imaging - Retina Specialist, accessed April 26, 2026, https://www.retina-specialist.com/article/clearing-up-the-language-of-retinal-imaging

B.5 Standard SOP Classes - DICOM, accessed April 26, 2026, https://dicom.nema.org/dicom/2013/output/chtml/part04/sect_B.5.html

AAO Guidelines For Standardizing Images In Ophthalmology I OBN, accessed April 26, 2026, https://ophthalmologybreakingnews.com/recommendations-by-aao-for-standardization-of-images-in-ophthalmology

DICOM supplements Overview, accessed April 26, 2026, https://dicom.nema.org/Dicom/News/supplements/

Spherical Lens Power Attribute - DICOM Standard Browser - Innolitics, accessed April 26, 2026, https://dicom.innolitics.com/ciods/ophthalmic-tomography-image/ophthalmic-tomography-acquisition-parameters/0022001b/00220007

Ultra-Widefield Retinal Optical Coherence Tomography (OCT) and Angio-OCT Using an Add-On Lens - PMC, accessed April 26, 2026, https://pmc.ncbi.nlm.nih.gov/articles/PMC12248670/

C.8.11.4 DX Detector Module - DICOM, accessed April 26, 2026, https://dicom.nema.org/medical/Dicom/2024c/output/chtml/part03/sect_C.8.11.4.html

Supplement 173: Wide Field Ophthalmic Photography Image Storage SOP Classes - DICOM, accessed April 26, 2026, https://www.dicomstandard.org/News-dir/ftsup/docs/sups/sup173.pdf

Standardized Ultra-Widefield Swept-Source OCT Imaging: A Reproducible Protocol for Peripheral Retinal Assessment - PMC, accessed April 26, 2026, https://pmc.ncbi.nlm.nih.gov/articles/PMC13100825/

SNOMED CT, US Edition - Retinal disorder - Classes | NCBO BioPortal, accessed April 26, 2026, https://purl.bioontology.org/ontology/SNOMEDCT/29555009

Ultra-wide field retinal imaging: A wider clinical perspective - PMC, accessed April 26, 2026, https://pmc.ncbi.nlm.nih.gov/articles/PMC8012972/

DEFINING ULTRA-WIDEFIELD - Optos, accessed April 26, 2026, https://www.optos.com/globalassets/public/optos/providers/clinical-papers/css-defining-uwf-us.pdf

Supplement 144 Ophthalmic Axial Measurements Storage SOP Classes - DICOM, accessed April 26, 2026, https://www.dicomstandard.org/News-dir/ftsup/docs/sups/sup144.pdf

C.8.17.14 Ophthalmic Optical Coherence Tomography En Face Image Module - DICOM, accessed April 26, 2026, https://dicom.nema.org/medical/dicom/current/output/chtml/part03/sect_c.8.17.14.html

Ophthalmic Tomography Angiographic (OCT-A) Image Storage SOP Classes - DICOM, accessed April 26, 2026, https://www.dicomstandard.org/news/supplements/view/ophthalmic-tomography-angiographic-(oct-a)-image-storage-sop-classes

Supplements - DICOM, accessed April 26, 2026, https://www.dicomstandard.org/supplements

DICOM Standard Status, accessed April 26, 2026, https://www.dclunie.com/dicom-status/status.html

Foveal avascular zone area and parafoveal vessel density measurements in different stages of diabetic retinopathy by optical coherence tomography angiography - PMC, accessed April 26, 2026, https://pmc.ncbi.nlm.nih.gov/articles/PMC5638976/

SOP Instance UID Attribute - DICOM Standard Browser - Innolitics, accessed April 26, 2026, https://dicom.innolitics.com/ciods/rt-plan/sop-common/00080018

Supplement 240: Heightmap Segmentation and Revised Ophthalmic OCT En Face Image - DICOM, accessed April 26, 2026, https://www.dicomstandard.org/docs/librariesprovider2/default-document-library/pdfc395fa7f-d2d8-4321-8068-3898f1b8a8eb.pdf?sfvrsn=ce921ac8_3

Ophthalmic Optical Coherence Tomography B-scan Volume Analysis CIOD, accessed April 26, 2026, https://dicom.innolitics.com/ciods/ophthalmic-optical-coherence-tomography-b-scan-volume-analysis

Supplement 197: Ophthalmic Optical Coherence Tomography for Angiographic Imaging Storage SOP Classes - DICOM, accessed April 26, 2026, https://www.dicomstandard.org/News-dir/ftsup/docs/sups/sup197.pdf

accessed December 31, 1969, https://github.com/ImagingDataCommons/highdicom/search?q=1.2.840.10008.5.1.4.1.1.77.1.5.7

accessed December 31, 1969, https://github.com/ImagingDataCommons/highdicom/search?q=OCT-A+ophthalmic+axial+wide+field

Documentation of the Highdicom Package — highdicom 0.27.0 documentation, accessed April 26, 2026, https://highdicom.readthedocs.io/

Supplement 247: Eyecare Measurement Templates - DICOM, accessed April 26, 2026, https://dicom.nema.org/Dicom/News/March2025/docs/sups/sup247.pdf

Approved Supplements - DICOM, accessed April 26, 2026, https://www.dicomstandard.org/approved-supplements

Eyecare Measurement Templates - DICOM, accessed April 26, 2026, https://www.dicomstandard.org/docs/librariesprovider2/dicomdocuments/eyecare-measurement-templates.docx?sfvrsn=dc0ac5ed_3

Digital Imaging and Communications in Medicine (DICOM) Supplement 247: Eyecare Measurement Templates, accessed April 26, 2026, https://www.dicomstandard.org/docs/librariesprovider2/default-document-library/sup247_pc_eyecaretemplates_pdf.pdf?sfvrsn=3449109f_3

sup247_ft_EyecareTemplates.docx - DICOM - NEMA, accessed April 26, 2026, https://dicom.nema.org/medical/dicom/final/sup247_ft_EyecareTemplates.docx

DICOM for Advanced Image Analysis, accessed April 26, 2026, https://www.dicomstandard.org/docs/librariesprovider2/default-document-library/sup247_pc_eyecaretemplates_pptx.pptx?sfvrsn=393a226d_3

How to Read Corneal Topography - American Academy of Ophthalmology, accessed April 26, 2026, https://www.aao.org/young-ophthalmologists/yo-info/article/how-to-read-corneal-topography

Corneal Tomography vs. Topography in Ectasia Screening - NECO, accessed April 26, 2026, https://www.neco.edu/corneal-tomography-vs-topography-in-ectasia-screening/

Supplement 168 Corneal Topography Map Storage SOP Class - DICOM, accessed April 26, 2026, https://www.dicomstandard.org/News-dir/ftsup/docs/sups/sup168.pdf

Supplement 247: Eyecare Measurement Templates DICOM WORKING GROUP 9 OPHTHALMOLOGY DRAFT FINAL TEXT June 2025, accessed April 26, 2026, https://www.dicomstandard.org/news-dir/progress/docs/sups/sup247-slides.pdf

Supplement 130 - Ophthalmic Refractive Measurements Storage and SR SOP Classes - DICOM, accessed April 26, 2026, https://www.dicomstandard.org/News-dir/ftsup/docs/sups/sup130.pdf

HL7.FHIR.UV.EYECARE\Ophthalmology observations (SNOMED) - FHIR v4.0.1, accessed April 26, 2026, https://build.fhir.org/ig/HL7/fhir-eyecare-ig/ValueSet-observable-entities.html

Supplement 130 Refractive Reports - DICOM, accessed April 26, 2026, https://dicom.nema.org/medical/dicom/Final/sup144_ft.doc

Ophthalmic Axial Length Measurements Type Attribute - DICOM Standard Browser, accessed April 26, 2026, https://dicom.innolitics.com/ciods/ophthalmic-axial-measurements/ophthalmic-axial-measurements/00221008/00221050/00221010

Ophthalmic Axial Length Attribute - DICOM Standard Browser, accessed April 26, 2026, https://dicom.innolitics.com/ciods/ophthalmic-axial-measurements/ophthalmic-axial-measurements/00221007/00221050/00221212/00221211/00221019

Ophthalmic Axial Length Measurements Type Attribute - DICOM Standard Browser, accessed April 26, 2026, https://dicom.innolitics.com/ciods/ophthalmic-axial-measurements/ophthalmic-axial-measurements/00221007/00221230/00221010

Developer Guide — highdicom 0.27.0 documentation, accessed April 26, 2026, https://highdicom.readthedocs.io/en/latest/development.html

Issues · ImagingDataCommons/highdicom - GitHub, accessed April 26, 2026, https://github.com/ImagingDataCommons/highdicom/issues

SNOMED CT, US Edition - Ocular axial length - Classes | NCBO BioPortal, accessed April 26, 2026, http://purl.bioontology.org/ontology/SNOMEDCT/251692002

Pull requests · ImagingDataCommons/highdicom - GitHub, accessed April 26, 2026, https://github.com/ImagingDataCommons/highdicom/pulls

Digital Imaging and Communications in Medicine (DICOM) Supplement 247: Eyecare Measurement Templates, accessed April 26, 2026, https://www.dicomstandard.org/News-dir/ftsup/docs/sups/sup247.pdf

IHE Eye Care Technical Framework Supplement Key Measurements in DICOM® Encapsulated PDF Revision 1.2, accessed April 26, 2026, https://www.ihe.net/uploadedFiles/Documents/Eye_Care/IHE_EyeCare_Suppl_Key_Measurement_PDF.pdf

ImagingDataCommons/highdicom: High-level DICOM abstractions for the Python programming language - GitHub, accessed April 26, 2026, https://github.com/ImagingDataCommons/highdicom

Widefield and Ultra-Widefield Retinal Imaging: A Geometrical Analysis - PMC - NIH, accessed April 26, 2026, https://pmc.ncbi.nlm.nih.gov/articles/PMC9867331/

ULTRA-WIDE FIELD SWEPT-SOURCE OPTICAL COHERENCE TOMOGRAPHY ANGIOGRAPHY (UWF SS OCT-A) IN DIABETIC RETINOPATHY | IOVS, accessed April 26, 2026, https://iovs.arvojournals.org/article.aspx?articleid=2557969

DICOM Tags, accessed April 26, 2026, https://www.dicomlibrary.com/dicom/dicom-tags/

Nomenclature and Guidelines for Widefield Imaging - American Academy of Ophthalmology, accessed April 26, 2026, https://www.aao.org/eyenet/article/nomenclature-and-guidelines-for-widefield-imaging

DICOM Attributes - NV5 Geospatial Software, accessed April 26, 2026, https://www.nv5geospatialsoftware.com/docs/DICOMAttributes.html

SOP Class UID Attribute - DICOM Standard Browser - Innolitics, accessed April 26, 2026, https://dicom.innolitics.com/ciods/rt-dose/sop-common/00080016

B.5 Standard SOP Classes - DICOM, accessed April 26, 2026, https://dicom.nema.org/medical/dicom/2017c/output/chtml/part04/sect_B.5.html

Recommendations for Standardization of Images in Ophthalmology - PMC, accessed April 26, 2026, https://pmc.ncbi.nlm.nih.gov/articles/PMC8335850/

Highdicom: a Python Library for Standardized Encoding of Image Annotations and Machine Learning Model Outputs in Pathology and Radiology - PMC, accessed April 26, 2026, https://pmc.ncbi.nlm.nih.gov/articles/PMC9712874/

Ocular Biometry OCR: a machine learning algorithm leveraging optical character recognition to extract intra ocular lens biometry measurements - PMC, accessed April 26, 2026, https://pmc.ncbi.nlm.nih.gov/articles/PMC11743993/

Anatomic Region Modifier Sequence Attribute - DICOM Standard Browser - Innolitics, accessed April 26, 2026, https://dicom.innolitics.com/ciods/digital-intra-oral-x-ray-image/intra-oral-image/00082218/00082220

CID 2 Anatomic Modifier - DICOM, accessed April 26, 2026, https://dicom.nema.org/medical/dicom/current/output/chtml/part16/sect_cid_2.html

Quantitative single and multi-surface clinical corneal topography utilizing OCT - PMC, accessed April 26, 2026, https://pmc.ncbi.nlm.nih.gov/articles/PMC4517424/

Corneal Topography/Computer-Assisted Corneal Topography/Photokeratoscopy - Blue Cross Blue Shield of Mississippi, accessed April 26, 2026, https://www.bcbsms.com/policy-search/medical/policy-detail/corneal-topographycomputerassisted-corneal-topographyphotokeratoscopy

DICOM News, accessed April 26, 2026, https://www.dicomstandard.org/news

C.8.25.14 Ophthalmic Axial Measurements Module - DICOM, accessed April 26, 2026, https://dicom.nema.org/medical/dicom/current/output/chtml/part03/sect_c.8.25.14.html

Tag Thickness Attribute - DICOM Standard Browser - Innolitics, accessed April 26, 2026, https://dicom.innolitics.com/ciods/enhanced-mr-color-image/enhanced-mr-color-image-multi-frame-functional-groups/52009229/00189006/00189035

DICOM SEG for CMR cine data (2D + t) · Issue #174 · ImagingDataCommons/highdicom, accessed April 26, 2026, https://github.com/ImagingDataCommons/highdicom/issues/174

Releases · ImagingDataCommons/highdicom - GitHub, accessed April 26, 2026, https://github.com/ImagingDataCommons/highdicom/releases

Volumes — highdicom 0.27.0 documentation, accessed April 26, 2026, https://highdicom.readthedocs.io/en/stable/volume.html

Problem writing x-ray angiography data (2D+ time) · Issue #200 · ImagingDataCommons/highdicom - GitHub, accessed April 26, 2026, https://github.com/ImagingDataCommons/highdicom/issues/200

Application of Ultrawide Field (UWF) Swept Source Optical Coherence Tomography/Angiography (SS-OCT/OCTA) in Real-World Clinical Practice | IOVS, accessed April 26, 2026, https://iovs.arvojournals.org/article.aspx?articleid=2803465

Ultrawide field FA and Ultrawide filed OCT-A, real world benefits of combining use of them, accessed April 26, 2026, https://iovs.arvojournals.org/article.aspx?articleid=2806198

Standardized Ultra-Widefield Swept-Source OCT Imaging | OPTH - Dove Medical Press, accessed April 26, 2026, https://www.dovepress.com/standardized-ultra-widefield-swept-source-oct-imaging-a-reproducible-p-peer-reviewed-fulltext-article-OPTH

CID 244 Laterality - DICOM, accessed April 26, 2026, ftp://dicom.nema.org/MEDICAL/dicom/2015b/output/chtml/part16/sect_CID_244.html

IHE Eye Care Technical Framework Supplement Key Measurements in DICOM® Encapsulated PDF Revision 1.1, accessed April 26, 2026, https://www.ihe.net/uploadedFiles/Documents/Eye_Care/IHE_EyeCare_Suppl_Key_Measurement_PDF_Rev1-1_TI_2019-04-29.pdf

Ophthalmic Axial Length Measurements Segment Name Code Sequence Attribute, accessed April 26, 2026, https://dicom.innolitics.com/ciods/ophthalmic-axial-measurements/ophthalmic-axial-measurements/00221008/00221255/00221257/00221101

SNOMED CT, US Edition - Corneal topography - Classes | NCBO BioPortal, accessed April 26, 2026, https://bioportal.bioontology.org/ontologies/SNOMEDCT?p=classes&conceptid=252830007

The Power of Ultra-widefield Imaging and Navigated Peripheral OCT - The Ophthalmologist, accessed April 26, 2026, https://theophthalmologist.com/issues/2025/articles/october/the-power-of-ultra-widefield-imaging-and-navigated-peripheral-oct/

Supplement 240: Height Map Segmentation and Revised Ophthalmic OCT En Face Image, accessed April 26, 2026, https://implementer.digitalhealth.gov.au/standards/supplement-240-height-map-segmentation-and-revised-ophthalmic-oct-en-face-image

Update on wide- and ultra-widefield retinal imaging - PMC - NIH, accessed April 26, 2026, https://pmc.ncbi.nlm.nih.gov/articles/PMC4652247/

Ultra-Widefield Steering-Based SD-OCT Imaging of the Retinal Periphery - PMC - NIH, accessed April 26, 2026, https://pmc.ncbi.nlm.nih.gov/articles/PMC4877257/

Measurements of white-to-white corneal diameter and anterior chamber parameters using the Pentacam AXL wave and their correlations in the adult Saudi population - PMC, accessed April 26, 2026, https://pmc.ncbi.nlm.nih.gov/articles/PMC11970417/

(PDF) Measurements of white-to-white corneal diameter and anterior chamber parameters using the Pentacam AXL wave and their correlations in the adult Saudi population - ResearchGate, accessed April 26, 2026, https://www.researchgate.net/publication/390482888_Measurements_of_white-to-white_corneal_diameter_and_anterior_chamber_parameters_using_the_Pentacam_AXL_wave_and_their_correlations_in_the_adult_Saudi_population

Anterior Chamber Depth Definition Code Sequence Attribute - DICOM Standard Browser, accessed April 26, 2026, https://dicom.innolitics.com/ciods/ophthalmic-axial-measurements/ophthalmic-axial-measurements/00221125

VESSEL DENSITY OF SUPERFICIAL, INTERMEDIATE, AND DEEP CAPILLARY PLEXUSES USING OPTICAL COHERENCE TOMOGRAPHY ANGIOGRAPHY - PMC, accessed April 26, 2026, https://pmc.ncbi.nlm.nih.gov/articles/PMC6358199/

Foveal Avascular Zone and Vessel Density in Healthy Subjects: An Optical Coherence Tomography Angiography Study - PMC, accessed April 26, 2026, https://pmc.ncbi.nlm.nih.gov/articles/PMC6058537/

Foveal avascular zone and vessel density in healthy subjects: An optical coherence tomography angiography study - ResearchGate, accessed April 26, 2026, https://www.researchgate.net/publication/326372339_Foveal_avascular_zone_and_vessel_density_in_healthy_subjects_An_optical_coherence_tomography_angiography_study

Optos launches next-gen UWF imaging system with SD-OCT, accessed April 26, 2026, https://glance.eyesoneyecare.com/stories/2025-02-17/optos-launches-next-gen-uwf-imaging-system-with-sd-oct/

Corneal topography from spectral optical coherence tomography (sOCT) - PMC - NIH, accessed April 26, 2026, https://pmc.ncbi.nlm.nih.gov/articles/PMC3233243/

Study Identifies Normative Values for Macula Vessel Density and FAZ on OCT-A, accessed April 26, 2026, https://www.reviewofoptometry.com/news/article/study-identifies-normative-values-for-macula-vessel-density-and-faz-on-octa

Automated Feature Segmentation of Ultra-Widefield OCT Images - PubMed, accessed April 26, 2026, https://pubmed.ncbi.nlm.nih.gov/41625354/

Automated Feature Segmentation of Ultra-Widefield OCT Images - PMC - NIH, accessed April 26, 2026, https://pmc.ncbi.nlm.nih.gov/articles/PMC12858344/

(PDF) Diametric measurement of foveal avascular zone in healthy young adults using optical coherence tomography angiography - ResearchGate, accessed April 26, 2026, https://www.researchgate.net/publication/311212090_Diametric_measurement_of_foveal_avascular_zone_in_healthy_young_adults_using_optical_coherence_tomography_angiography

Computer-assisted corneal topography (also called photokeratoscopy or videokeratography) provides a quantitative measure of corn - Blue Cross Blue Shield of Rhode Island, accessed April 26, 2026, https://www.bcbsri.com/providers/sites/providers/files/policies/2025/03/2024%20UPDATE%20Corneal%20Topography%2C%20Computer%20Assisted%20Corneal%20Topography%2C%20Photokeratoscopy.docx.pdf

Term - Browse Code Systems - NIH, accessed April 26, 2026, https://vsac.nlm.nih.gov/context/cs/codesystem/SNOMEDCT/version/2024-03/code/231959000/info

313011004 - Browse Code Systems - NIH, accessed April 26, 2026, https://vsac.nlm.nih.gov/context/cs/codesystem/SNOMEDCT/version/2022-09/code/313011004/info

It prompts Failed to read frame #18, when using highdicom to read the specified dicom file. · Issue #235 - GitHub, accessed April 26, 2026, https://github.com/ImagingDataCommons/highdicom/issues/235

Widefield and Ultra-widefield Imaging: When and Why to Use Them - Mivision Education, accessed April 26, 2026, https://www.mieducation.com/pages/widefield-and-ultra-widefield-imaging-when-and-why-to-use-them

Using Ultrawide Field-Directed Optical Coherence Tomography for Differentiating Nonproliferative and Proliferative Diabetic Retinopathy - PMC, accessed April 26, 2026, https://pmc.ncbi.nlm.nih.gov/articles/PMC9910382/

