# %%
from pathlib import Path

from settings import main_settings

VIDEOS_LOCATION = Path("/srv/milky/drosophila-pipeline/1_video-preproc/input")
INPUT_VIDEO_PATH = VIDEOS_LOCATION / main_settings.TREATMENT

SAVE_PATH = main_settings.SAVE_DATA_LOCATION / 'video-preproc' / 'output'

# data dir in pipeline for metdatada and logger
PROCESSED_VIDEOS_DIR = main_settings.DATA_DIR / 'video_preproc'
