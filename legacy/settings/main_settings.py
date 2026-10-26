# %%
import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent

NUM_THREADS = os.cpu_count() - 4

TREATMENT = 'CS_old3-5d_24group'
# TREATMENT = 'CS_isolated_24group'
# TREATMENT = 'CS_old8-10d_24group'
# TREATMENT = 'CS_10D'

SAVE_DATA_LOCATION = Path('/srv/milky/drosophila-pipeline')

VIDEO_FPS = 24
ARENA_DIAMETER = 81
VIDEO_EXTENSION = '.mp4'

FLIES_IN_GROUP = 24
START_TIME_SECONDS = 0 * 60
END_TIME_SECONDS = 20 * 60
START_TIME_FPS = START_TIME_SECONDS * VIDEO_FPS
END_TIME_FPS = END_TIME_SECONDS * VIDEO_FPS
SECONDS_INCREMENT = 60  # time increment between videos

CONFIG_DIR = ROOT_DIR / "configs"
DATA_DIR = ROOT_DIR / "data"
LOGS_DIR = ROOT_DIR / "logs"
REPORTS_DIR = ROOT_DIR / "reports"
UTILS_DIR = ROOT_DIR / "utils"

TRACKINGS = DATA_DIR / 'trackings'

VIDEO_PREPROC_DIR = ROOT_DIR / '1_video_preproc'
TRACKER_DIR = ROOT_DIR / '2_tracking_flytracker'
PREPROC_TRAJECTORIES = ROOT_DIR / '3_preproc_trajectories'
SOC_INT_CRITERIA_DIR = ROOT_DIR / '4_drosophila_social_interaction_criteria'
SNA_ANALYSIS = ROOT_DIR / '5_drosophila_social_network_analysis'

NORMALIZATION_DIR = PREPROC_TRAJECTORIES / 'get_normalization'

RESULTS_DIR = SAVE_DATA_LOCATION / "res"
