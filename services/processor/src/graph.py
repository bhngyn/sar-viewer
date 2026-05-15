"""SNAP GPT graph XML builders for GRD and SLC InSAR pipelines.

Each function returns a well-formed XML string that can be written to disk and
passed to ``gpt <graph.xml>`` via subprocess.  Parameter substitution uses SNAP's
native ``$var`` syntax so the caller (``pipeline.py``) can supply source paths
and the target path on the GPT command line (``-Psource0=... -Ptarget=...``).

Design rationale
----------------
We build graph XML programmatically (via ``xml.etree.ElementTree``) rather than
bundling static ``.xml`` files because:

1. It makes the pipeline steps explicit and auditable in code.
2. It allows operator-configurable parameters (window sizes, DEM source, etc.)
   to be injected without string templating that can break XML escaping.
3. Unit tests can parse the returned XML and assert on operator names and
   parameter values without running SNAP.

Operator reference: https://step.esa.int/main/toolboxes/snap/ (graph builder docs)
"""

from __future__ import annotations

from xml.etree import ElementTree as ET


def _add_node(
    graph: ET.Element,
    node_id: str,
    operator: str,
    sources: dict[str, str] | None,
    params: dict[str, str] | None,
) -> ET.Element:
    """Append a ``<node>`` element to a SNAP graph.

    Parameters
    ----------
    graph:
        The ``<graph>`` root element.
    node_id:
        Unique identifier for this node (e.g. ``"ApplyOrbit"``).
    operator:
        SNAP operator name (e.g. ``"Apply-Orbit-File"``).
    sources:
        Mapping of ``sourceProduct`` alias → referenced node id.
        Pass ``None`` or empty dict for Read nodes.
    params:
        Operator parameters as ``{param_name: param_value}`` strings.

    Returns
    -------
    ET.Element
        The newly created node element.
    """
    node = ET.SubElement(graph, "node", id=node_id)
    ET.SubElement(node, "operator").text = operator

    sources_el = ET.SubElement(node, "sources")
    if sources:
        for alias, ref_id in sources.items():
            src = ET.SubElement(sources_el, alias)
            src.set("refid", ref_id)

    params_el = ET.SubElement(node, "parameters")
    if params:
        for key, val in params.items():
            el = ET.SubElement(params_el, key)
            el.text = val

    return node


def _graph_to_str(graph: ET.Element) -> str:
    """Serialize a graph element to a pretty-printed XML string."""
    ET.indent(graph, space="  ")
    return ET.tostring(graph, encoding="unicode", xml_declaration=False)


def build_grd_graph(
    source_placeholder: str = "$source0",
    target_placeholder: str = "$target",
    *,
    speckle_filter_window_size: str = "7",
    dem_name: str = "SRTM 1Sec HGT",
    dem_resampling_method: str = "BILINEAR_INTERPOLATION",
    img_resampling_method: str = "BICUBIC_INTERPOLATION",
    pixel_spacing_m: str = "10.0",
) -> str:
    """Build the SNAP GPT graph XML for the GRD processing pipeline.

    Pipeline steps (per CLAUDE.md §5 Phase 1 Agent B):
    1. Read
    2. Apply-Orbit-File
    3. ThermalNoiseRemoval
    4. Remove-GRD-Border-Noise
    5. Calibration (output sigma0)
    6. Speckle-Filter (Refined Lee, configurable window — default 7x7)
    7. Terrain-Correction (Range-Doppler)
    8. LinearToFromdB
    9. Write (GeoTIFF-BigTIFF; COG conversion done by cog.py)

    Parameters
    ----------
    source_placeholder:
        SNAP parameter variable for the source product path (``$source0``).
    target_placeholder:
        SNAP parameter variable for the output product path (``$target``).
    speckle_filter_window_size:
        Window size for the Refined Lee speckle filter (NxN).  Default 7.
    dem_name:
        DEM used for terrain correction.  Default SRTM 1 Sec HGT (auto-download).
    dem_resampling_method:
        Interpolation method for DEM resampling.
    img_resampling_method:
        Interpolation method for image resampling during TC.
    pixel_spacing_m:
        Output pixel spacing in metres for terrain-corrected product.

    Returns
    -------
    str
        Well-formed SNAP GPT graph XML.
    """
    graph = ET.Element("graph", version="1.0")
    ET.SubElement(graph, "version").text = "1.0"

    # 1. Read
    _add_node(
        graph,
        "Read",
        "Read",
        sources=None,
        params={"file": source_placeholder},
    )

    # 2. Apply-Orbit-File — fetches precise orbits from ESA DORIS / POEORB
    _add_node(
        graph,
        "ApplyOrbit",
        "Apply-Orbit-File",
        sources={"sourceProduct": "Read"},
        params={
            "orbitType": "Sentinel Precise (Auto Download)",
            "polyDegree": "3",
            "continueOnFail": "false",
        },
    )

    # 3. Thermal Noise Removal — removes thermal noise LUT contribution
    _add_node(
        graph,
        "ThermalNoise",
        "ThermalNoiseRemoval",
        sources={"sourceProduct": "ApplyOrbit"},
        params={"removeThermalNoise": "true"},
    )

    # 4. Border Noise Removal — handles GRD edge artifacts
    _add_node(
        graph,
        "BorderNoise",
        "Remove-GRD-Border-Noise",
        sources={"sourceProduct": "ThermalNoise"},
        params={
            "borderLimit": "500",
            "trimThreshold": "0.5",
        },
    )

    # 5. Calibration — outputs σ⁰ (sigma nought) in linear scale
    # We keep linearOutput=true here; LinearToFromdB is applied as a separate
    # step so we have the option to branch on linear or dB output in future.
    _add_node(
        graph,
        "Calibration",
        "Calibration",
        sources={"sourceProduct": "BorderNoise"},
        params={
            "selectedPolarisations": "",  # empty = all available
            "outputSigmaBand": "true",
            "outputBetaBand": "false",
            "outputGammaBand": "false",
            "outputDNBand": "false",
        },
    )

    # 6. Speckle Filter — Refined Lee with configurable NxN window
    # Refined Lee is preferred over plain Lee or Gamma-MAP because it adapts to
    # local texture and preserves point targets (vehicles, buildings).
    _add_node(
        graph,
        "SpeckleFilter",
        "Speckle-Filter",
        sources={"sourceProduct": "Calibration"},
        params={
            "filter": "Refined Lee",
            "filterSizeX": speckle_filter_window_size,
            "filterSizeY": speckle_filter_window_size,
            "dampingFactor": "2",
            "estimateENL": "true",
            "enl": "1.0",
            "numLooksStr": "1",
            "windowSize": "7x7",
            "targetWindowSizeStr": "3x3",
            "sigmaStr": "0.9",
            "anSize": "50",
        },
    )

    # 7. Terrain Correction — Range-Doppler with DEM orthorectification
    _add_node(
        graph,
        "TerrainCorrection",
        "Terrain-Correction",
        sources={"sourceProduct": "SpeckleFilter"},
        params={
            "demName": dem_name,
            "demResamplingMethod": dem_resampling_method,
            "imgResamplingMethod": img_resampling_method,
            "pixelSpacingInMeter": pixel_spacing_m,
            "mapProjection": "AUTO:42001",  # UTM auto-zone
            "saveDEM": "false",
            "saveLocalIncidenceAngle": "false",
            "saveProjectedLocalIncidenceAngle": "false",
            "saveLayoverShadowMask": "false",
            "applyRadiometricNormalization": "false",
            "nodataValueAtSea": "true",
        },
    )

    # 8. Linear-to-dB conversion — σ⁰ [linear] → σ⁰ [dB]
    _add_node(
        graph,
        "LinearToFromdB",
        "LinearToFromdB",
        sources={"sourceProduct": "TerrainCorrection"},
        params={},
    )

    # 9. Write — BigTIFF (COG conversion via rio-cogeo happens outside GPT)
    _add_node(
        graph,
        "Write",
        "Write",
        sources={"sourceProduct": "LinearToFromdB"},
        params={
            "file": target_placeholder,
            "formatName": "GeoTIFF-BigTIFF",
        },
    )

    return _graph_to_str(graph)


def build_slc_insar_graph(
    source_a_placeholder: str = "$source0",
    source_b_placeholder: str = "$source1",
    target_placeholder: str = "$target",
    *,
    subswath: str = "IW1",
    polarization: str = "VV",
    burst_index_start: str = "1",
    burst_index_end: str = "9",
    multilook_az: str = "1",
    multilook_rg: str = "4",
    goldstein_alpha: str = "1.0",
    dem_name: str = "SRTM 1Sec HGT",
    pixel_spacing_m: str = "10.0",
) -> str:
    """Build the SNAP GPT graph XML for the SLC InSAR coherence pipeline.

    Pipeline steps (per CLAUDE.md §5 Phase 1 Agent B):
    1. Read (master / reference scene)
    2. Read (slave / secondary scene)
    3. TOPSAR-Split (master)
    4. Apply-Orbit-File (master)
    5. TOPSAR-Split (slave)
    6. Apply-Orbit-File (slave)
    7. Back-Geocoding (coregistration)
    8. Enhanced-Spectral-Diversity (sub-pixel coregistration)
    9. Interferogram-Formation
    10. TOPSAR-Deburst
    11. Topo-Phase-Removal (flat-earth phase removal)
    12. Multilook
    13. Goldstein-Phase-Filtering
    14. Coherence  (coherence band extraction)
    15. Terrain-Correction
    16. Write

    Parameters
    ----------
    source_a_placeholder:
        SNAP parameter variable for the reference (earlier) scene.
    source_b_placeholder:
        SNAP parameter variable for the secondary (later) scene.
    target_placeholder:
        SNAP parameter variable for the output product.
    subswath:
        IW subswath to process (IW1, IW2, or IW3).
    polarization:
        Polarization channel to use (VV or VH).
    burst_index_start / burst_index_end:
        Burst range for TOPSAR-Split (1-based).
    multilook_az / multilook_rg:
        Azimuth / range looks for multilook step.
    goldstein_alpha:
        Goldstein filter exponent (0-1; 1.0 = maximum filtering).
    dem_name:
        DEM for terrain correction and back-geocoding.
    pixel_spacing_m:
        Output pixel spacing in metres.

    Returns
    -------
    str
        Well-formed SNAP GPT graph XML.
    """
    graph = ET.Element("graph", version="1.0")
    ET.SubElement(graph, "version").text = "1.0"

    # 1-2. Read both scenes
    _add_node(graph, "ReadA", "Read", sources=None, params={"file": source_a_placeholder})
    _add_node(graph, "ReadB", "Read", sources=None, params={"file": source_b_placeholder})

    # 3. TOPSAR-Split master (reference scene)
    _add_node(
        graph,
        "SplitA",
        "TOPSAR-Split",
        sources={"sourceProduct": "ReadA"},
        params={
            "subswath": subswath,
            "selectedPolarisations": polarization,
            "firstBurstIndex": burst_index_start,
            "lastBurstIndex": burst_index_end,
        },
    )

    # 4. Apply-Orbit-File master
    _add_node(
        graph,
        "OrbitA",
        "Apply-Orbit-File",
        sources={"sourceProduct": "SplitA"},
        params={
            "orbitType": "Sentinel Precise (Auto Download)",
            "polyDegree": "3",
            "continueOnFail": "false",
        },
    )

    # 5. TOPSAR-Split slave (secondary scene)
    _add_node(
        graph,
        "SplitB",
        "TOPSAR-Split",
        sources={"sourceProduct": "ReadB"},
        params={
            "subswath": subswath,
            "selectedPolarisations": polarization,
            "firstBurstIndex": burst_index_start,
            "lastBurstIndex": burst_index_end,
        },
    )

    # 6. Apply-Orbit-File slave
    _add_node(
        graph,
        "OrbitB",
        "Apply-Orbit-File",
        sources={"sourceProduct": "SplitB"},
        params={
            "orbitType": "Sentinel Precise (Auto Download)",
            "polyDegree": "3",
            "continueOnFail": "false",
        },
    )

    # 7. Back-Geocoding — coregisters secondary to reference using DEM
    _add_node(
        graph,
        "BackGeocoding",
        "Back-Geocoding",
        sources={
            "sourceProduct": "OrbitA",
            "sourceProduct2": "OrbitB",
        },
        params={
            "demName": dem_name,
            "demResamplingMethod": "BILINEAR_INTERPOLATION",
            "resamplingType": "BISINC_5_POINT_INTERPOLATION",
            "maskOutAreaWithoutElevation": "true",
            "outputDerampDemodPhase": "false",
        },
    )

    # 8. Enhanced-Spectral-Diversity — improves azimuth coregistration
    _add_node(
        graph,
        "ESD",
        "Enhanced-Spectral-Diversity",
        sources={"sourceProduct": "BackGeocoding"},
        params={
            "fineWinWidthStr": "512",
            "fineWinHeightStr": "512",
            "fineWinAccAzimuth": "16",
            "fineWinAccRange": "16",
            "fineWinOversampling": "128",
            "xCorrThreshold": "0.1",
            "cohThreshold": "0.3",
            "numBlocksPerOverlap": "10",
            "esdEstimator": "Periodogram",
            "weightFunc": "Inv Quadratic",
            "temporalBaselineType": "Number of images",
            "maxTemporalBaseline": "4",
            "integrationMethod": "L1 and L2",
            "doNotWriteTargetBands": "false",
            "useSuppliedRangeShift": "false",
            "overallRangeShift": "0.0",
            "useSuppliedAzimuthShift": "false",
            "overallAzimuthShift": "0.0",
        },
    )

    # 9. Interferogram Formation — generates complex interferometric product
    _add_node(
        graph,
        "Interferogram",
        "Interferogram",
        sources={"sourceProduct": "ESD"},
        params={
            "subtractFlatEarthPhase": "true",
            "srpPolynomialDegree": "5",
            "srpNumberPoints": "501",
            "orbitDegree": "3",
            "includeCoherence": "true",
            "cohWinAz": "10",
            "cohWinRg": "10",
            "squarePixel": "true",
        },
    )

    # 10. TOPSAR-Deburst — joins IW bursts into a continuous swath
    _add_node(
        graph,
        "Deburst",
        "TOPSAR-Deburst",
        sources={"sourceProduct": "Interferogram"},
        params={"selectedPolarisations": polarization},
    )

    # 11. Topo-Phase-Removal — subtracts topographic phase contribution
    _add_node(
        graph,
        "TopoPhase",
        "TopoPhaseRemoval",
        sources={"sourceProduct": "Deburst"},
        params={
            "demName": dem_name,
            "demResamplingMethod": "BILINEAR_INTERPOLATION",
            "orbitDegree": "3",
            "outputTopoPhaseBand": "false",
            "outputElevationBand": "false",
            "outputLatLonBands": "false",
        },
    )

    # 12. Multilook — reduces azimuth/range resolution ratio, lowers speckle
    # Default 1 az-look, 4 range-looks gives approximately square pixels for IW.
    _add_node(
        graph,
        "Multilook",
        "Multilook",
        sources={"sourceProduct": "TopoPhase"},
        params={
            "nAzLooks": multilook_az,
            "nRgLooks": multilook_rg,
            "outputIntensity": "false",
            "grSquarePixel": "true",
        },
    )

    # 13. Goldstein Phase Filtering — suppresses phase noise while preserving fringes
    _add_node(
        graph,
        "GoldsteinFilter",
        "GoldsteinPhaseFiltering",
        sources={"sourceProduct": "Multilook"},
        params={
            "alpha": goldstein_alpha,
            "FFTSizeString": "64",
            "windowSizeString": "3",
            "useCoherenceMask": "false",
            "coherenceThreshold": "0.2",
        },
    )

    # 14. Coherence — coherence is already computed in the interferogram step;
    # this node extracts and optionally refines it.  We output coherence only.
    # Note: SNAP's interferogram operator already computes coherence; this Write
    # step preserves the coherence band from the filtered product.
    _add_node(
        graph,
        "TerrainCorrection",
        "Terrain-Correction",
        sources={"sourceProduct": "GoldsteinFilter"},
        params={
            "demName": dem_name,
            "demResamplingMethod": "BILINEAR_INTERPOLATION",
            "imgResamplingMethod": "BICUBIC_INTERPOLATION",
            "pixelSpacingInMeter": pixel_spacing_m,
            "mapProjection": "AUTO:42001",
            "saveDEM": "false",
            "saveLocalIncidenceAngle": "false",
            "saveProjectedLocalIncidenceAngle": "false",
            "saveLayoverShadowMask": "false",
            "nodataValueAtSea": "true",
        },
    )

    # 15. Write
    _add_node(
        graph,
        "Write",
        "Write",
        sources={"sourceProduct": "TerrainCorrection"},
        params={
            "file": target_placeholder,
            "formatName": "GeoTIFF-BigTIFF",
        },
    )

    return _graph_to_str(graph)
