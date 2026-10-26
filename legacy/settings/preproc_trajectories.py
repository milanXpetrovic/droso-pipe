from settings import main_settings

TREATMENT = main_settings.TREATMENT
SAVE_DATA_LOCATION = main_settings.SAVE_DATA_LOCATION

# /srv/milky/drosophila-pipeline/video-preproc/output/CS_old3-5d_24group

SKIP_SHEET = "Sheet1"
SUPPORTED_FORMAT = ".xls"
REQUIRED_COLUMNS = {"pos x", "pos y", "major axis len"}

PREPROC_TRAJECTORIES = SAVE_DATA_LOCATION / '3_preproc-trajectories'

INPUT_XLSX_DIR = SAVE_DATA_LOCATION / '2_tracking-flytracker' / TREATMENT
CSV_DIR = PREPROC_TRAJECTORIES / 'extract_from_excel' / TREATMENT
NORMALIZATION_DIR = PREPROC_TRAJECTORIES / 'get_normalization' / TREATMENT
NORMALIZED_TRAJECTORIES_DIR = PREPROC_TRAJECTORIES / 'normalize_trajectories' / TREATMENT
