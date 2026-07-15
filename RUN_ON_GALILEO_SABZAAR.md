# Run Sabzaar current-tree detection on Galileo100

Tailored to your setup: `[email protected]`, account `EIRI_E_UNISA2`,
miniforge at `/g100_work/EIRI_E_UNISA2/smirani0/`, scratch `$SCRATCH`
(`/g100_scratch/userexternal/smirani0`), GPU via `g100_usr_interactive`.

**The one thing I can't do:** the SSH login. CINECA requires your password + one-time
code (2FA), so steps that touch the cluster are yours. Everything else (local imagery
cache, and deploying the result) I can do on your PC. Commands below are copy-paste.

---

## Step 1 - (PC, I can do this) cache the imagery for the district
The compute node has no internet, so tiles are fetched on your PC first.

```powershell
cd "C:\Users\Pirah\Claude\Projects\new project for cv\Sabzaar"
$env:AOI_W="68.150"; $env:AOI_S="27.520"; $env:AOI_E="68.270"; $env:AOI_N="27.600"
$env:FETCH_ONLY="1"
.\.venv-detect\Scripts\python.exe detect_core.py     # fills _esri_sample\ (no GPU/model needed)
```
(The full built-up district is a large tile set. Esri imagery is used only to derive tree
points for non-commercial research - we ship derived points, not imagery.)

## Step 2 - (you) log in, create the env once (login node HAS internet)
```bash
ssh [email protected]
source /g100_work/EIRI_E_UNISA2/smirani0/miniforge3/etc/profile.d/conda.sh
conda create -p /g100_work/EIRI_E_UNISA2/smirani0/sabzaar_detect python=3.11 -y
conda activate /g100_work/EIRI_E_UNISA2/smirani0/sabzaar_detect
pip install deepforest rasterio geopandas
# match Galileo's CUDA (V100 nodes -> cu118 is safe; check `module avail cuda` / nvidia-smi):
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu118
# pre-cache the model so the offline compute node can load it:
python -c "from deepforest import main; m=main.deepforest(); m.load_model('weecology/deepforest-tree')"
```

## Step 3 - (you) ship code + tiles + buildings to scratch
From your PC (a second terminal), or use WinSCP/FileZilla to the same paths:
```bash
DEST=/g100_scratch/userexternal/smirani0/sabzaar
ssh [email protected] "mkdir -p $DEST/app/data $DEST/logs"
scp detect_core.py run_galileo.slurm [email protected]:$DEST/
scp _buildings.geojson [email protected]:$DEST/
scp -r _esri_sample [email protected]:$DEST/
```

## Step 4 - (you) submit on Galileo
```bash
ssh [email protected]
cd /g100_scratch/userexternal/smirani0/sabzaar
sbatch run_galileo.slurm
squeue -u smirani0                 # watch; log in logs/sabzaar_trees_*.out
```

## Step 5 - (PC, I can do this) bring the result back and deploy
```bash
scp [email protected]:/g100_scratch/userexternal/smirani0/sabzaar/app/data/current_trees.geojson \
    "C:\Users\Pirah\Claude\Projects\new project for cv\Sabzaar\app\data\current_trees.geojson"
```
Then commit + push (Cloudflare Pages auto-redeploys); the map's "Detected trees" layer now covers
the whole district. I'll also widen the layer's label/hint from "centre sample" to "district".

---

### Notes
- **Partition/time:** `g100_usr_interactive` is what your GNN job used. If the district job
  needs longer or a different GPU queue, adjust `-p` / `--time` (check `sinfo`).
- **CUDA:** if `torch.cuda.is_available()` prints False in the job log, the torch build
  doesn't match the node's CUDA - reinstall torch with the matching `cuXXX` index.
- **No internet on compute node** is why we pre-cache tiles (Step 1) and the model (Step 2).
  If your login node can reach Esri, you could instead run `FETCH_ONLY` there and skip the scp
  of `_esri_sample`.
- **Sharper crowns:** point `ESRI` in `detect_core.py` at institutional sub-meter imagery
  (Maxar/Planet) for better detection than Esri's ~0.6 m.
