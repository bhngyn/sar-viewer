# Operator Guide

> **Status**: Stub — Phase 4 will expand this into a full operator guide
> (install, first-run, common workflows, troubleshooting). The content below
> covers only the Phase 1 processor-specific items needed to run smoke tests.

## Real-data SAR smoke test (manual checklist)

The unit tests in `tests/unit/processor/` mock the SNAP subprocess boundary and
verify graph XML structure. Before declaring Phase 1 complete, an operator must
run a real-data smoke test using an actual Sentinel-1 SAFE product.

### Prerequisites

- Docker compose stack running (`make up`)
- A Sentinel-1 GRD SAFE product downloaded to `data/cache/`
  (use CDSE OData or the `ingest` service once Phase 1 Agent A is merged)
- Sufficient disk space: ~2 GB for one GRD scene + intermediates

### GRD smoke test

```bash
# 1. Copy a real S1 GRD SAFE into the scratch volume
cp -r /path/to/S1A_IW_GRD_*.SAFE data/scratch/

# 2. Exec into the processor container and run the pipeline
docker exec sar-viewer-worker python -c "
from pathlib import Path
from services.processor.src.pipeline import process_grd

result = process_grd(
    safe_dir=Path('/scratch/S1A_IW_GRD_XXXX.SAFE'),
    out_dir=Path('/scratch/grd_output'),
)
print('Output COG:', result)
"

# 3. Validate the output COG
docker exec sar-viewer-worker python -c "
from pathlib import Path
from services.processor.src.cog import validate_cog
print(validate_cog(Path('/scratch/grd_output/XXXX_GRD_sigma0_dB.tif')))
"
```

Expected output: `True`

### SLC InSAR smoke test

<!-- TODO(phase-4): add SLC pair smoke test with a known coherent pair -->

The SLC pipeline requires two Sentinel-1 SLC SAFE products from the same
relative orbit, with a temporal baseline of 6–24 days for meaningful coherence.

A worked example using a public archive pair will be added in Phase 4.

### Notes

- SNAP will auto-download orbit files and SRTM DEM tiles on first run.
  This requires network access from inside the `snap` sidecar container.
- Processing time: ~5–10 minutes per GRD scene on a 4-core laptop.
  SLC coherence pairs take 20–45 minutes depending on scene size.
- Memory: the `snap` service is configured with `-Xmx14G`. Do not run more
  than 2 SLC jobs simultaneously (`MAX_SLC_PARALLEL = 2` in pipeline.py).
