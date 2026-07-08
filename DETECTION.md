# Current-tree detection (Sabzaar)

The canopy-height layer answers *"is this tall vegetation"* well, because it is built
from a canopy **height** model - and height is what separates a tree from grass, a
lawn, or a field. Its one weakness is **currency**: its source imagery is a few years
old, so trees planted since then are missing and a few felled ones still show.

This pipeline adds a second, independent layer: **individual trees detected on
present-day high-resolution imagery**, so the two can be compared.

## Method

- **Imagery:** Esri World Imagery (~0.6 m/px at zoom 18) for the prototype. It is used
  only to **derive tree points** for non-commercial civic research - we publish the
  derived points, not the imagery. The pipeline is source-agnostic: point it at
  institutional sub-meter imagery (Maxar / Planet) for sharper crowns by changing one
  URL and the zoom.
- **Model:** [DeepForest](https://deepforest.readthedocs.io) - a pretrained RetinaNet
  tree-crown detector (trained on NEON airborne data). No training or labels required.
- **Pipeline** (`detect_trees.py`): fetch + stitch imagery over an area of interest,
  upsample so crowns are large enough for the detector, run `predict_tile`, convert box
  centroids to lon/lat, and write `app/data/current_trees.geojson` plus a
  `detect_preview.png` for a visual sanity check.

## Honest limitations

- At ~0.6 m, small or tightly-clustered crowns are missed or merged; the detector was
  trained at ~0.1 m. Expect good recall on medium/large trees, weaker on saplings.
  Institutional 0.3 m imagery closes most of this gap.
- DeepForest is trained on North-American forests, not arid South-Asian streetscapes,
  so it is a reasonable **estimate**, not a census. Points are shown as *detected*
  trees, with a confidence score, not as verified ground truth.
- This layer and the height layer will disagree in places - that disagreement is the
  useful signal (recent change, or model error on either side), not a bug.

## How to run

Environment (one-time), isolated so it can't disturb the rest of the pipeline:

```
python -m venv .venv-detect
.venv-detect/Scripts/python -m pip install deepforest        # Windows
# (on Linux/HPC add a CUDA build of torch for GPU)
```

- `detect_trees.py` - single small area of interest + an annotated `detect_preview.png`
  for eyeballing quality.
- `detect_core.py` - processes an area in blocks (bounded memory, one model load) and
  writes `app/data/current_trees.geojson`. The AOI, block size, upsample and output
  path are read from env vars (`AOI_W/S/E/N`, `BLOCK`, `UP`, `OUTFILE`), defaulting to
  the dense city core.

## Scaling to the full district (HPC / Galileo)

`detect_core.py` uses the GPU automatically when `torch.cuda.is_available()`. To cover
the whole district, set the AOI env vars and submit `run_galileo.slurm` (adjust the
account / partition / module lines to your allocation).

One caveat that bites on clusters: **compute nodes often have no internet**. If so,
run once on a login node (which usually does) to populate `_esri_sample/` and the model
caches, then submit the GPU job - it reads the caches and needs no network. The Overture
`_buildings.geojson` must also be present in the repo directory.
