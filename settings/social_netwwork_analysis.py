from pathlib import Path

from settings import main_settings

TREATMENT = main_settings.TREATMENT
ROOT_DIR = Path(main_settings.SAVE_DATA_LOCATION)

DATA_DIR = ROOT_DIR / '5_social_network_analysis'

INPUT_DIR = ROOT_DIR / "3_preproc-trajectories" / "normalize_trajectories"
OUTPUT_DIR = DATA_DIR / "data" / "processed"
NORMALIZATION_DIR = DATA_DIR / "data" / "normalization"
RESULTS_DIR = DATA_DIR / "data" / "results"

PIPELINE_DIR = DATA_DIR / "src"
LOGS_DIR = DATA_DIR / "logs"
CONFIG_DIR = DATA_DIR / "configs"
REPORTS_DIR = DATA_DIR / "reports"

N_RANDOM_GROUPS = 10000
N_OF_SAMPLES = 12
