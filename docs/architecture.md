# sar-viewer Architecture

> Living technical document. Updated as decisions land. See [CLAUDE.md](../CLAUDE.md) for the
> single source of truth on architectural decisions, agent orchestration, and the v0.1 mission scope.

This file currently documents the Phase 2 fusion design. Subsequent phases will append.

---

## Fusion color rationale

The fusion service (`services/fusion/`) blends Sentinel-2 RGB basemaps with SAR-derived
products. The color choices below are not aesthetic — they shape how an investigator reads
the radar signature, and rainbow colormaps are known to introduce false hue boundaries that
humans misread as real data boundaries.

### Allowed colormaps for SAR overlay

| LUT       | Use                             | Rationale                                                                                                                                           |
| --------- | ------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| `gray`    | Default                         | SAR amplitude is intrinsically mono; mapping it to neutral lightness avoids competing with S2 hues and keeps the investigator's eye on the basemap. |
| `viridis` | Allowed when context demands it | Perceptually uniform (Smith & Van der Walt 2015) and color-blind safe. Used sparingly because hue carries meaning the eye is tempted to interpret.  |

### Banned

`jet`, `rainbow`, `hsv`, `gist_rainbow`, `nipy_spectral`, and every other rainbow LUT are
banned across the tool. Rainbow LUTs:

1. Are **not perceptually uniform** — equal data steps yield unequal perceived steps,
   distorting magnitude.
2. Introduce **false hue boundaries** at green/yellow and cyan/blue transitions; a human
   reader sees a "ridge" where the data is smooth.
3. Are **failure-mode hostile to color-blind viewers** (deuteranopic / protanopic readers
   lose the green/red distinction).

The defensive runtime guard in `services/fusion/src/colormap.py` raises `ValueError` if any
caller passes `"jet"` even with the static `Literal` type bypassed. See
**Crameri, F., Shephard, G. E., & Heron, P. J. (2020). "The misuse of colour in science
communication." Nature Communications 11, 5444** for the canonical write-up.

### Alpha ceilings

| Mode               | Max α   | Rationale                                                                                                              |
| ------------------ | ------- | ---------------------------------------------------------------------------------------------------------------------- |
| `sar_on_s2`        | 1.0     | Investigator can choose to dominate the view with SAR if that's their intent.                                          |
| `change_rgb_on_s2` | **0.5** | The multitemporal RGB composite **must not** obliterate the S2 context. >50% opacity becomes a no-context radar slide. |

Values above 0.5 in `change_rgb_on_s2` raise `ValueError`.

### Anomaly overlay colors

The `anomaly_highlight` mode draws filled-and-outlined bboxes over the S2 basemap. Each
`Anomaly.kind` gets a distinct categorical color (Tableau 10 palette indices):

| Kind               | Color                            | Rationale                                                                                                                |
| ------------------ | -------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `new`              | `#d62728` (Tableau red)          | "Newly bright" = attention-grabbing; red leverages the convention that red ≈ alert.                                      |
| `missing`          | `#17becf` (Tableau cyan)         | "Dropped out" = cool/missing; cyan reads as the absence-of-red counterpoint and is color-blind distinguishable from red. |
| `intensity_change` | `#bcbd22` (Tableau yellow-olive) | Real-but-ambiguous; a neutral hue between red and cyan that doesn't pre-commit the investigator to one interpretation.   |

- **Fill opacity**: 25%. A higher fill obscures the basemap and forces the investigator to
  trust the bbox blindly; 25% lets them see what's _underneath_ the polygon and verify the
  finding for themselves.
- **Outline opacity**: 100%. The outline carries the "where" information; making it
  semi-transparent reduces locatability for no perceptual benefit.

### Watermark

Every fused COG carries a `"Sentinel-1 ~10m"` text patch in the bottom-right by default
(toggle via the `WATERMARK` env var or the `watermark=False` kwarg). The watermark is
non-negotiable for human-rights work: an exported PDF without a resolution caveat invites
the misreading "the satellite saw _this_ exactly" — when in fact a single S1 pixel is
roughly the size of a small house. CLAUDE.md §0 lists this caveat as a Definition-of-Done
gate.

---

## References

- Crameri, F., Shephard, G. E., & Heron, P. J. (2020). _The misuse of colour in science
  communication._ Nature Communications 11, 5444.
- Smith, N., & van der Walt, S. (2015). _A better default colormap for matplotlib._ SciPy 2015.
- Cian, F., Marconcini, M., Ceccato, P., & Giupponi, C. (2019). _Multitemporal RGB
  composites for change detection using Sentinel-1 SAR data._ IEEE JSTARS 12(3).
