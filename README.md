# sar-viewer

A locally-runnable, dockerized SAR/OSINT investigation tool for human rights researchers. Draw an area of interest on a map, search and process Sentinel-1 SAR scenes (and Sentinel-2 for context), detect anomalies against a temporal baseline, and export a report.

> **Resolution caveat.** Sentinel-1 is ~5×20 m on GRD and ~2.3×14 m on SLC. This tool can surface buildings, vehicle clusters, ships >20 m, aircraft on tarmac, earthworks, flooding, demolition, and encampments. It **cannot** identify individuals, license plates, or objects smaller than ~5 m. Every exported report carries this footer.

## Status

**Phase 0 — Foundation.** Repository scaffolding, shared Pydantic models, docker-compose stubs, CI, and pre-commit hooks. No SAR processing or algorithms yet — those land in Phases 1–4 per [`CLAUDE.md`](./CLAUDE.md) §5.

## Quickstart

Prereqs: macOS or Linux, Docker ≥ 24, [uv](https://docs.astral.sh/uv/) ≥ 0.4, Python 3.11 (uv will install it), ≥ 16 GB RAM, ≥ 50 GB free disk.

```bash
# 1. Clone
git clone https://github.com/bhngyn/sar-viewer.git
cd sar-viewer

# 2. Configure secrets (never commit the resulting .env)
cp .env.example .env
# Edit .env: CDSE_CLIENT_ID, CDSE_CLIENT_SECRET, SH_INSTANCE_ID
#   Get credentials from https://dataspace.copernicus.eu/

# 3. Bring the stack up
make up
curl http://localhost:8000/health        # → {"status":"ok"}
open  http://localhost:5173              # UI stub (Phase 3 will replace)

# 4. Run lint, types, tests
uv sync
make ci
```

## Layout

```
services/          api, ingest, processor, analyzer, fusion, tiler, ui
shared/            Pydantic models, geo helpers, storage interface
tests/             unit + integration + fixtures
docs/              architecture, SAR primer, operator guide (Phase 4)
.github/workflows/ CI (ruff + mypy + pytest + docker compose build)
```

Full architectural decisions and the four-phase build plan are in [`CLAUDE.md`](./CLAUDE.md). Threat model and operator guide ship in Phase 4.

## Security & OPSEC

Standard profile per `CLAUDE.md` §6: no telemetry, secrets via `.env`, AOI redaction in logs, unprivileged containers, `HTTPS_PROXY` / `ALL_PROXY` respected for Tor / VPN. Encryption-at-rest, tamper-evident audit logs, and signed releases are documented residual risks for v0.2+.

## License

[AGPL-3.0-or-later](./LICENSE). Humanitarian-friendly copyleft — improvements stay in the commons.
