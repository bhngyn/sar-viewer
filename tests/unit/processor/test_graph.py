"""Unit tests for SNAP graph XML generation.

These tests do NOT require SNAP to be installed.  They parse the generated XML
and assert that the correct operator sequence and key parameters are present.

The test strategy is to verify the graph *structure* (operator names, wiring,
critical parameters) so that any accidental regression in the pipeline recipe
is caught in CI before a developer has to wait for a SNAP run to fail.
"""

from __future__ import annotations

from xml.etree import ElementTree as ET

from services.processor.src.graph import build_grd_graph, build_slc_insar_graph


def _parse(xml: str) -> ET.Element:
    return ET.fromstring(xml)


def _operators(root: ET.Element) -> list[str]:
    """Return operator names in document order."""
    return [op.text or "" for op in root.findall(".//operator")]


def _node_params(root: ET.Element, node_id: str) -> dict[str, str]:
    """Return {param_name: param_value} for a node by its id attribute."""
    node = root.find(f".//node[@id='{node_id}']")
    assert node is not None, f"Node '{node_id}' not found in graph"
    return {el.tag: (el.text or "") for el in (node.find("parameters") or ET.Element("parameters"))}


def _node_sources(root: ET.Element, node_id: str) -> dict[str, str]:
    """Return {alias: refid} for source references of a node."""
    node = root.find(f".//node[@id='{node_id}']")
    assert node is not None, f"Node '{node_id}' not found in graph"
    sources_el = node.find("sources")
    if sources_el is None:
        return {}
    return {el.tag: el.get("refid", "") for el in sources_el}


# ---------------------------------------------------------------------------
# GRD graph tests
# ---------------------------------------------------------------------------


class TestBuildGrdGraph:
    def test_returns_valid_xml(self) -> None:
        xml = build_grd_graph()
        root = _parse(xml)
        assert root.tag == "graph"

    def test_operator_sequence(self) -> None:
        xml = build_grd_graph()
        root = _parse(xml)
        ops = _operators(root)
        expected = [
            "Read",
            "Apply-Orbit-File",
            "ThermalNoiseRemoval",
            "Remove-GRD-Border-Noise",
            "Calibration",
            "Speckle-Filter",
            "Terrain-Correction",
            "LinearToFromdB",
            "Write",
        ]
        assert ops == expected, f"Operator sequence mismatch: {ops}"

    def test_read_node_source_placeholder(self) -> None:
        xml = build_grd_graph(source_placeholder="$source0")
        root = _parse(xml)
        params = _node_params(root, "Read")
        assert params["file"] == "$source0"

    def test_write_node_target_placeholder(self) -> None:
        xml = build_grd_graph(target_placeholder="$target")
        root = _parse(xml)
        params = _node_params(root, "Write")
        assert params["file"] == "$target"

    def test_write_format_is_geotiff_bigtiff(self) -> None:
        xml = build_grd_graph()
        root = _parse(xml)
        params = _node_params(root, "Write")
        assert params["formatName"] == "GeoTIFF-BigTIFF"

    def test_calibration_outputs_sigma0(self) -> None:
        xml = build_grd_graph()
        root = _parse(xml)
        params = _node_params(root, "Calibration")
        assert params["outputSigmaBand"] == "true"
        assert params["outputBetaBand"] == "false"
        assert params["outputGammaBand"] == "false"

    def test_speckle_filter_is_refined_lee(self) -> None:
        xml = build_grd_graph()
        root = _parse(xml)
        params = _node_params(root, "SpeckleFilter")
        assert params["filter"] == "Refined Lee"

    def test_speckle_filter_default_window_size_7x7(self) -> None:
        xml = build_grd_graph()
        root = _parse(xml)
        params = _node_params(root, "SpeckleFilter")
        assert params["filterSizeX"] == "7"
        assert params["filterSizeY"] == "7"

    def test_speckle_filter_custom_window_size(self) -> None:
        xml = build_grd_graph(speckle_filter_window_size="5")
        root = _parse(xml)
        params = _node_params(root, "SpeckleFilter")
        assert params["filterSizeX"] == "5"
        assert params["filterSizeY"] == "5"

    def test_terrain_correction_default_dem(self) -> None:
        xml = build_grd_graph()
        root = _parse(xml)
        params = _node_params(root, "TerrainCorrection")
        assert params["demName"] == "SRTM 1Sec HGT"

    def test_terrain_correction_pixel_spacing(self) -> None:
        xml = build_grd_graph(pixel_spacing_m="20.0")
        root = _parse(xml)
        params = _node_params(root, "TerrainCorrection")
        assert params["pixelSpacingInMeter"] == "20.0"

    def test_linear_to_db_follows_terrain_correction(self) -> None:
        """LinearToFromdB must come AFTER terrain correction, not before.

        Applying dB conversion before TC would corrupt the sigma0 values
        because TC re-samples in linear scale.  This test asserts the
        operator ordering is correct.
        """
        xml = build_grd_graph()
        root = _parse(xml)
        ops = _operators(root)
        tc_idx = ops.index("Terrain-Correction")
        db_idx = ops.index("LinearToFromdB")
        assert db_idx > tc_idx, "LinearToFromdB must come after Terrain-Correction in GRD pipeline"

    def test_chain_wiring_read_to_orbit(self) -> None:
        xml = build_grd_graph()
        root = _parse(xml)
        sources = _node_sources(root, "ApplyOrbit")
        assert sources.get("sourceProduct") == "Read"

    def test_chain_wiring_orbit_to_thermal(self) -> None:
        xml = build_grd_graph()
        root = _parse(xml)
        sources = _node_sources(root, "ThermalNoise")
        assert sources.get("sourceProduct") == "ApplyOrbit"

    def test_chain_wiring_thermal_to_border(self) -> None:
        xml = build_grd_graph()
        root = _parse(xml)
        sources = _node_sources(root, "BorderNoise")
        assert sources.get("sourceProduct") == "ThermalNoise"

    def test_chain_wiring_border_to_calibration(self) -> None:
        xml = build_grd_graph()
        root = _parse(xml)
        sources = _node_sources(root, "Calibration")
        assert sources.get("sourceProduct") == "BorderNoise"

    def test_chain_wiring_calibration_to_speckle(self) -> None:
        xml = build_grd_graph()
        root = _parse(xml)
        sources = _node_sources(root, "SpeckleFilter")
        assert sources.get("sourceProduct") == "Calibration"

    def test_chain_wiring_speckle_to_tc(self) -> None:
        xml = build_grd_graph()
        root = _parse(xml)
        sources = _node_sources(root, "TerrainCorrection")
        assert sources.get("sourceProduct") == "SpeckleFilter"

    def test_chain_wiring_tc_to_db(self) -> None:
        xml = build_grd_graph()
        root = _parse(xml)
        sources = _node_sources(root, "LinearToFromdB")
        assert sources.get("sourceProduct") == "TerrainCorrection"

    def test_chain_wiring_db_to_write(self) -> None:
        xml = build_grd_graph()
        root = _parse(xml)
        sources = _node_sources(root, "Write")
        assert sources.get("sourceProduct") == "LinearToFromdB"


# ---------------------------------------------------------------------------
# SLC InSAR graph tests
# ---------------------------------------------------------------------------


class TestBuildSlcInSARGraph:
    def test_returns_valid_xml(self) -> None:
        xml = build_slc_insar_graph()
        root = _parse(xml)
        assert root.tag == "graph"

    def test_operator_sequence(self) -> None:
        xml = build_slc_insar_graph()
        root = _parse(xml)
        ops = _operators(root)
        # Expected pipeline per CLAUDE.md §5 Phase 1 Agent B
        expected = [
            "Read",  # ReadA
            "Read",  # ReadB
            "TOPSAR-Split",  # SplitA
            "Apply-Orbit-File",  # OrbitA
            "TOPSAR-Split",  # SplitB
            "Apply-Orbit-File",  # OrbitB
            "Back-Geocoding",
            "Enhanced-Spectral-Diversity",
            "Interferogram",
            "TOPSAR-Deburst",
            "TopoPhaseRemoval",
            "Multilook",
            "GoldsteinPhaseFiltering",
            "Terrain-Correction",
            "Write",
        ]
        assert ops == expected, f"SLC operator sequence mismatch: {ops}"

    def test_source_a_placeholder(self) -> None:
        xml = build_slc_insar_graph(source_a_placeholder="$source0")
        root = _parse(xml)
        params = _node_params(root, "ReadA")
        assert params["file"] == "$source0"

    def test_source_b_placeholder(self) -> None:
        xml = build_slc_insar_graph(source_b_placeholder="$source1")
        root = _parse(xml)
        params = _node_params(root, "ReadB")
        assert params["file"] == "$source1"

    def test_target_placeholder(self) -> None:
        xml = build_slc_insar_graph(target_placeholder="$target")
        root = _parse(xml)
        params = _node_params(root, "Write")
        assert params["file"] == "$target"

    def test_split_default_subswath(self) -> None:
        xml = build_slc_insar_graph()
        root = _parse(xml)
        params_a = _node_params(root, "SplitA")
        params_b = _node_params(root, "SplitB")
        assert params_a["subswath"] == "IW1"
        assert params_b["subswath"] == "IW1"

    def test_split_custom_subswath(self) -> None:
        xml = build_slc_insar_graph(subswath="IW2")
        root = _parse(xml)
        params_a = _node_params(root, "SplitA")
        assert params_a["subswath"] == "IW2"

    def test_split_polarization(self) -> None:
        xml = build_slc_insar_graph(polarization="VV")
        root = _parse(xml)
        params_a = _node_params(root, "SplitA")
        assert params_a["selectedPolarisations"] == "VV"

    def test_back_geocoding_sources(self) -> None:
        xml = build_slc_insar_graph()
        root = _parse(xml)
        sources = _node_sources(root, "BackGeocoding")
        assert sources.get("sourceProduct") == "OrbitA"
        assert sources.get("sourceProduct2") == "OrbitB"

    def test_goldstein_alpha_default(self) -> None:
        xml = build_slc_insar_graph()
        root = _parse(xml)
        params = _node_params(root, "GoldsteinFilter")
        assert params["alpha"] == "1.0"

    def test_goldstein_alpha_custom(self) -> None:
        xml = build_slc_insar_graph(goldstein_alpha="0.5")
        root = _parse(xml)
        params = _node_params(root, "GoldsteinFilter")
        assert params["alpha"] == "0.5"

    def test_multilook_default_looks(self) -> None:
        xml = build_slc_insar_graph()
        root = _parse(xml)
        params = _node_params(root, "Multilook")
        assert params["nAzLooks"] == "1"
        assert params["nRgLooks"] == "4"

    def test_interferogram_includes_coherence(self) -> None:
        """Coherence must be requested in the interferogram step."""
        xml = build_slc_insar_graph()
        root = _parse(xml)
        params = _node_params(root, "Interferogram")
        assert params["includeCoherence"] == "true"

    def test_terrain_correction_dem(self) -> None:
        xml = build_slc_insar_graph()
        root = _parse(xml)
        params = _node_params(root, "TerrainCorrection")
        assert params["demName"] == "SRTM 1Sec HGT"

    def test_two_read_nodes_exist(self) -> None:
        """SLC pipeline requires exactly two Read nodes (reference + secondary)."""
        xml = build_slc_insar_graph()
        root = _parse(xml)
        read_nodes = root.findall(".//node[@id='ReadA']") + root.findall(".//node[@id='ReadB']")
        assert len(read_nodes) == 2


# ---------------------------------------------------------------------------
# Constant checks (graph format stability)
# ---------------------------------------------------------------------------


class TestGraphConstants:
    """Ensure graph XML is stable and well-formed across repeated calls."""

    def test_grd_xml_is_idempotent(self) -> None:
        xml1 = build_grd_graph()
        xml2 = build_grd_graph()
        assert xml1 == xml2

    def test_slc_xml_is_idempotent(self) -> None:
        xml1 = build_slc_insar_graph()
        xml2 = build_slc_insar_graph()
        assert xml1 == xml2

    def test_grd_has_version_element(self) -> None:
        xml = build_grd_graph()
        root = _parse(xml)
        version_el = root.find("version")
        assert version_el is not None
        assert version_el.text == "1.0"
