# %%
import os
from pathlib import Path

import settings.main_settings as settings

treatments_list = ['CS_10D',
                   'Cs_5DIZ',
                   'LDA_5DIZ',
                   'LDA_OCT_5DIZ',
                   'OCT_5DIZ']


main_dirs = [
    settings.VIDEO_PREPROC_DIR,
    settings.PREPROC_TRAJECTORIES,
    settings.SOC_INT_CRITERIA_DIR,
    settings.SNA_ANALYSIS
    ]

for path in main_dirs:
    path = Path(path) / 'src'
    for p in os.listdir(path):
        print(p)

settings.PREPROC_TRAJECTORIES / 'src' / '1_extract-from-excel.py'
settings.PREPROC_TRAJECTORIES / 'src' / '2_get_normalization.py'
settings.PREPROC_TRAJECTORIES / 'src' / '3_normalize-trajectories.py'

1_data
2_networks
3_analysis