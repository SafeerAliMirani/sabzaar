#!/bin/bash
# Sabzaar current-tree detection on CINECA Galileo100 (GPU).
# Tailored to smirani0 / EIRI_E_UNISA2 (same pattern as the CA1 train_gnn_gpu.slurm).
# The GPU compute node has NO internet, so imagery tiles and the DeepForest model must be
# cached beforehand - see RUN_ON_GALILEO_SABZAAR.md.

#SBATCH --job-name=sabzaar_trees
#SBATCH -A EIRI_E_UNISA2
#SBATCH -p g100_usr_interactive
#SBATCH --gres=gpu:1
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G
#SBATCH --time=00:30:00
#SBATCH --output=logs/sabzaar_trees_%j.out

# NOTE: no `set -u` - conda activate scripts reference unbound vars.
unset PYTHONPATH LD_LIBRARY_PATH
module purge
source /g100_work/EIRI_E_UNISA2/smirani0/miniforge3/etc/profile.d/conda.sh
conda activate /g100_work/EIRI_E_UNISA2/smirani0/sabzaar_detect
export LD_LIBRARY_PATH=$CONDA_PREFIX/lib:$LD_LIBRARY_PATH
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1     # use the pre-cached model, don't hit the network
set -e

cd "${SABZAAR_DIR:-$SCRATCH/sabzaar}"
mkdir -p logs app/data

python -c "import torch; print('CUDA:', torch.cuda.is_available(), (torch.cuda.get_device_name(0) if torch.cuda.is_available() else ''))"

# ---- area of interest: Larkana built-up district (edit as needed) ----
export AOI_W=68.150 AOI_S=27.520 AOI_E=68.270 AOI_N=27.600
export BLOCK=16                                   # bigger blocks are fine on a GPU
export OUTFILE=app/data/current_trees.geojson

# Requires (shipped from your PC, see runbook): detect_core.py, _esri_sample/ (cached tiles),
# _buildings.geojson (Overture footprints), and the DeepForest model in $HOME/.cache.
srun python detect_core.py
