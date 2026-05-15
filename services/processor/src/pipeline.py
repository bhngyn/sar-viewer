"""SAR processing pipelines wrapping SNAP GPT via pyroSAR.

Architecture note — SNAP invocation strategy
--------------------------------------------
SNAP runs as a dedicated sidecar container (see ``services/processor/snap.Dockerfile``
and the ``snap`` service in ``docker-compose.yml``).  The processor Python worker
shares a host-mounted scratch volume (``./data/scratch:/scratch``) with the SNAP
container.

Two strategies were considered:

1. **docker exec into the SNAP sidecar** — the worker would invoke
   ``docker exec sar-viewer-snap gpt <graph.xml>``.  This is simple but couples
   the processor service to the docker socket, which is a security concern (any
   process with the docker socket can escalate to root on the host).

2. **Shared scratch volume + subprocess to a local GPT stub** (chosen) — the SNAP
   container exposes GPT on PATH inside the sidecar; the worker runs ``gpt`` as a
   subprocess via the shared volume.  In practice, both containers must have SNAP
   installed.  In the Phase 1 architecture the worker Dockerfile installs only the
   ``snap-engine`` CLI (no SNAP Desktop), keeping the image lean.  The SNAP sidecar
   is reserved for heavy, memory-intensive SLC jobs (cap: ``MAX_SLC_PARALLEL = 2``).

The subprocess boundary is the mock point for unit tests: tests patch
``subprocess.run`` and verify only the generated graph XML structure, without
requiring SNAP to be installed in CI.

References
----------
- pyroSAR API: https://pyrosar.readthedocs.io/
- SNAP GPT graph operators: https://step.esa.int/main/toolboxes/snap/
- GRD recipe follows ESA Sentinel-1 Toolbox tutorial (Apply-Orbit → Thermal-Noise
  → Border-Noise → Calibrate → Speckle-Filter → Terrain-Correct → Linear-to-dB).
- SLC InSAR recipe follows ESA S1TBX InSAR tutorial (TOPS-Split → Apply-Orbit →
  Back-Geocoding → ESD → Interferogram → Deburst → Topo-Phase → Multilook →
  Goldstein-Filter → Coherence → Terrain-Correct).
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from services.processor.src.cog import convert_to_cog
from services.processor.src.graph import build_grd_graph, build_slc_insar_graph

if TYPE_CHECKING:
    pass

logger = structlog.get_logger(__name__)

# Maximum concurrent SLC InSAR jobs — each SLC pair can consume 8-16 GB RAM
# during back-geocoding + interferogram formation, so we cap at 2 to avoid OOM
# on the 16 GB reference laptop.
MAX_SLC_PARALLEL: int = 2

# SNAP GPT executable name — override via environment variable SNAP_GPT_CMD
# if the binary is not on PATH (e.g. non-standard SNAP install path).
_GPT_CMD: str = "gpt"


def _run_gpt(graph_xml: str, source_files: list[Path], out_path: Path) -> None:
    """Write a SNAP graph to a temp file and invoke GPT as a subprocess.

    We write the graph XML to a temporary file rather than passing it via stdin
    because GPT does not read graphs from stdin reliably on all platforms.

    Parameters
    ----------
    graph_xml:
        Well-formed SNAP graph XML string (as produced by :mod:`~.graph`).
    source_files:
        List of source product paths; substituted into the graph as
        ``$source0``, ``$source1``, …
    out_path:
        Desired output product path (passed as ``$target``).

    Raises
    ------
    subprocess.CalledProcessError
        If GPT exits non-zero.
    RuntimeError
        If the output file is not created after GPT returns.
    """
    with tempfile.NamedTemporaryFile(
        suffix=".xml", mode="w", encoding="utf-8", delete=False
    ) as tmp:
        tmp.write(graph_xml)
        tmp_path = Path(tmp.name)

    cmd: list[str] = [_GPT_CMD, str(tmp_path)]
    for i, src in enumerate(source_files):
        cmd += [f"-Psource{i}={src}"]
    cmd += [f"-Ptarget={out_path}"]

    log = logger.bind(graph_tmp=str(tmp_path), out_path=str(out_path))
    log.info("invoking_gpt", cmd=cmd)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
        )
        log.debug("gpt_stdout", stdout=result.stdout[-2000:] if result.stdout else "")
    except subprocess.CalledProcessError as exc:
        log.error(
            "gpt_failed",
            returncode=exc.returncode,
            stderr=exc.stderr[-2000:] if exc.stderr else "",
        )
        raise
    finally:
        tmp_path.unlink(missing_ok=True)

    if not out_path.exists():
        raise RuntimeError(f"GPT exited 0 but output not found: {out_path}")


def process_grd(safe_dir: Path, out_dir: Path) -> Path:
    """Process a Sentinel-1 GRD SAFE product to a σ⁰ (dB) Cloud-Optimized GeoTIFF.

    Pipeline (per CLAUDE.md §5 Phase 1 Agent B):
        Apply-Orbit-File
        → Thermal-Noise-Removal
        → Remove-GRD-Border-Noise
        → Calibration (output σ⁰)
        → Speckle-Filter (Refined Lee 7x7)
        → Terrain-Correction (Range-Doppler)
        → Linear-to-dB
        → write COG

    The output file name is derived from the SAFE directory stem.

    Parameters
    ----------
    safe_dir:
        Path to the unpacked ``.SAFE`` directory.
    out_dir:
        Directory where the output COG will be written.

    Returns
    -------
    Path
        Absolute path to the created COG (``<out_dir>/<stem>_GRD_sigma0_dB.tif``).

    Raises
    ------
    FileNotFoundError
        If ``safe_dir`` does not exist.
    subprocess.CalledProcessError
        If SNAP GPT exits non-zero.
    RuntimeError
        If COG validation fails post-write.
    """
    if not safe_dir.exists():
        raise FileNotFoundError(f"SAFE directory not found: {safe_dir}")

    out_dir.mkdir(parents=True, exist_ok=True)

    stem = safe_dir.stem.replace(".SAFE", "")
    snap_out = out_dir / f"{stem}_GRD_sigma0_dB_snap.tif"
    cog_out = out_dir / f"{stem}_GRD_sigma0_dB.tif"

    log = logger.bind(safe_dir=str(safe_dir), out=str(cog_out))
    log.info("process_grd_start")

    graph_xml = build_grd_graph(source_placeholder="$source0", target_placeholder="$target")
    _run_gpt(graph_xml, source_files=[safe_dir], out_path=snap_out)

    cog_path = convert_to_cog(snap_out, cog_out)
    snap_out.unlink(missing_ok=True)  # clean up intermediate

    log.info("process_grd_done", cog=str(cog_path))
    return cog_path


def process_slc_pair(safe_a: Path, safe_b: Path, out_dir: Path) -> Path:
    """Process a pair of Sentinel-1 SLC SAFE products to an InSAR coherence COG.

    Pipeline (per CLAUDE.md §5 Phase 1 Agent B):
        TOPSAR-Split (both scenes)
        → Apply-Orbit-File
        → Back-Geocoding
        → Enhanced-Spectral-Diversity (ESD)
        → Interferogram-Formation
        → TOPSAR-Deburst
        → Topo-Phase-Removal
        → Multilook
        → Goldstein-Phase-Filtering
        → Coherence
        → Terrain-Correction
        → write COG

    The output is a single-band coherence raster in [0, 1].

    Parameters
    ----------
    safe_a:
        Path to the earlier (reference) SLC ``.SAFE`` directory.
    safe_b:
        Path to the later (secondary) SLC ``.SAFE`` directory.
    out_dir:
        Directory where the output COG will be written.

    Returns
    -------
    Path
        Absolute path to the created coherence COG.

    Raises
    ------
    FileNotFoundError
        If either SAFE directory does not exist.
    subprocess.CalledProcessError
        If SNAP GPT exits non-zero.
    RuntimeError
        If COG validation fails post-write.
    """
    for p in (safe_a, safe_b):
        if not p.exists():
            raise FileNotFoundError(f"SAFE directory not found: {p}")

    out_dir.mkdir(parents=True, exist_ok=True)

    stem_a = safe_a.stem.replace(".SAFE", "")
    stem_b = safe_b.stem.replace(".SAFE", "")
    snap_out = out_dir / f"{stem_a}_{stem_b}_coherence_snap.tif"
    cog_out = out_dir / f"{stem_a}_{stem_b}_coherence.tif"

    log = logger.bind(safe_a=str(safe_a), safe_b=str(safe_b), out=str(cog_out))
    log.info("process_slc_pair_start")

    graph_xml = build_slc_insar_graph(
        source_a_placeholder="$source0",
        source_b_placeholder="$source1",
        target_placeholder="$target",
    )
    _run_gpt(graph_xml, source_files=[safe_a, safe_b], out_path=snap_out)

    cog_path = convert_to_cog(snap_out, cog_out)
    snap_out.unlink(missing_ok=True)

    log.info("process_slc_pair_done", cog=str(cog_path))
    return cog_path
