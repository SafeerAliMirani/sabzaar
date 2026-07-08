#!/bin/bash
# Scale the current-tree detector to the full Larkana district on Galileo (GPU).
# Same detect_core.py used locally - the AOI and block size come from env vars.
#
# IMPORTANT: adjust the account / partition / module lines to your allocation;
# these names vary by cluster. Check `sinfo` and `module avail` on Galileo.

#SBATCH --job-name=sabzaar-trees
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --gres=gpu:1
#SBATCH --time=06:00:00
#SBATCH --partition=boost_usr_prod        # <-- your GPU partition (e.g. g100_usr_prod)
#SBATCH --account=YOUR_ACCOUNT            # <-- your CINECA account
#SBATCH --output=sabzaar_%j.log

set -e
module purge
# Adjust to your cluster. Either a module-provided python + CUDA:
module load python cuda 2>/dev/null || true
# ...and a venv holding: deepforest, torch(+CUDA), rasterio, geopandas, pillow
source .venv-detect/bin/activate

# deepforest uses the GPU automatically when torch.cuda.is_available() is true.
python -c "import torch; print('CUDA:', torch.cuda.is_available())"

# ---- full district AOI (edit to taste; this covers Larkana's built-up area) ----
export AOI_W=68.150 AOI_S=27.520 AOI_E=68.270 AOI_N=27.600
export BLOCK=16                                  # larger blocks are fine on a GPU
export OUTFILE=app/data/current_trees.geojson

# NOTE ON INTERNET: many HPC compute nodes have no outbound network. If so,
# pre-cache first from a login node (which usually does have internet):
#   1) python detect_core.py            # populates _esri_sample/ + model caches
#      (or a small fetch loop over the AOI), then
#   2) sbatch run_galileo.slurm         # GPU job reads the caches, no network needed.
# _buildings.geojson (Overture footprints) must also be present in the repo dir.

srun python detect_core.py
