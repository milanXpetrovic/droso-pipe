# %%
from pathlib import Path

import pandas as pd
from scipy.io import loadmat

from settings import main_settings, preproc_trajectories
from utils import fileio
from utils.logger import setup_logger

logger = setup_logger("mat_to_csv")

TREATMENT = main_settings.TREATMENT
INPUT_DIR = preproc_trajectories.INPUT_XLSX_DIR
OUTPUT_DIR = preproc_trajectories.CSV_DIR

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

treatments = fileio.load_multiple_folders(INPUT_DIR)

for group_name, group_path in treatments.items():
    group = fileio.load_files_from_folder(group_path, '.mat')
    group = {k: v for k, v in group.items() if 'track' in k}

    file_name, group_path = next(iter(group.items()))
    data = loadmat(group_path)
    cols = [x[0] for x in data['trk'][0][0][0][0]]
    trk = data['trk'][0][0][1]

    save_path = Path(OUTPUT_DIR / group_name)
    save_path.mkdir(parents=True, exist_ok=True)

    for i in range(0, len(trk)):
        df = pd.DataFrame(trk[i], columns=cols)
        fly_path = save_path / f'fly{i+1}.csv'
        df.to_csv(fly_path)
