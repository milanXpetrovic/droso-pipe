import os

from settings import main_settings

RAW_DATA = main_settings.TRACKINGS
RAW_NORMALIZATION = main_settings.NORMALIZATION_DIR

TREATMENT = main_settings.TREATMENT
ARENA_DIEAMETER_MM = main_settings.ARENA_DIAMETER
FPS = main_settings.FPS
START = main_settings.START_TIME_SECONDS
END = main_settings.END_TIME_SECONDS

DATA_PREPARED = False

EXP_DURATION = 20
TIMECUT = 0

ANGLE_BIN = 5
DISTANCE_BIN = 0.25
DISTANCE_MAX = 100

RANDOM_GROUP_SIZE = 24
N_RANDOM_1 = 500
N_RANDOM_2 = 500
MOVECUT = True
# Paths
ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
LOGS_DIR = os.path.join(ROOT_DIR, "logs")
# Input files
INPUT_DIR = os.path.join(ROOT_DIR, "data", "input", TREATMENT)
# TRACKINGS = os.path.join(INPUT_DIR, "trackings", TREATMENT)
TRACKINGS = os.path.join(INPUT_DIR, "trackings")
PXPERMM = os.path.join(INPUT_DIR, "pxpermm", f"{TREATMENT}.json")
NROMALIZATION = os.path.join(INPUT_DIR, "normalization.json")

OUTPUT_DIR = os.path.join(ROOT_DIR, "data", "output")
