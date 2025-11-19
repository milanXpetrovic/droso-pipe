# %%
import os, sys
import re
import toml
from pathlib import Path

import numpy as np
import pandas as pd
import scipy.io

from settings import main_settings
import fileio

TREATMENT = main_settings.TREATMENT

path = f"/srv/milky/drosophila-pipeline/3_preproc-trajectories/extract_from_excel/{TREATMENT}"
all_groups = fileio.load_multiple_folders(path)

normalization_path = Path('/srv/milky/drosophila-pipeline/3_preproc-trajectories/get_normalization') / TREATMENT

SAVE_PATH = Path('./test/') / TREATMENT
os.makedirs(SAVE_PATH, exist_ok=True)

for idx, group in enumerate(all_groups.items(), start=1):
    group_name, group_path = group
    csv_dict = fileio.load_files_from_folder(group_path)

    group_normalization_path = normalization_path / f'{group_name}.toml'
    with open(group_normalization_path, "r") as f:
        norm = toml.load(f)

    pxpermm_group = (norm['x_px_ratio'] + norm['y_px_ratio']) / 2

    structured_arrays = []
    for fly_name, fly_path in csv_dict.items():
        df = pd.read_csv(fly_path)
        df = df[["pos x", "pos y", "ori", "major axis len", "minor axis len"]]

        df = df.interpolate().ffill().bfill()

        x = np.array([df["pos x"].to_numpy()])
        y = np.array([df["pos y"].to_numpy()])
        theta = np.array([df["ori"].to_numpy()])
        a = np.array([(df["major axis len"]/4).to_numpy()])
        b = np.array([(df["minor axis len"]/4).to_numpy()])

        FLY_ID = re.findall(r'\d+', fly_name)[0]
        id = np.array([[np.uint8(FLY_ID)]])
        pxpermm = np.array([[np.float64(pxpermm_group)]])
        fps = np.array([[np.float64(main_settings.VIDEO_FPS)]])
        structured_array = np.array([(x, y, theta, a, b, id, pxpermm, fps)],
                                    dtype=[('x', object),
                                           ('y', object),
                                           ('theta', object),
                                           ('a', object),
                                           ('b', object), 
                                           ('id', object),
                                           ('pxpermm', object),
                                           ('fps', object)])

        structured_arrays.append(structured_array)

    trx_csv = np.array(structured_arrays)
    trx_csv = trx_csv.T

    mat_csv = {
        '__header__': b'MATLAB 5.0 MAT-file, Platform: GLNXA64, Created on: Thu Jul 06 10:49:32 2023',
        '__version__': '1.0',
        '__globals__': [],
        'trx': trx_csv
    }

    filename = SAVE_PATH / f'{TREATMENT}_movie_{idx}.mat'
    scipy.io.savemat(filename, mat_csv)
