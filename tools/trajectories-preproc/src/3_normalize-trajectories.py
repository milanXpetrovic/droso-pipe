# %%
from pathlib import Path

import pandas as pd
import toml

from settings import main_settings, preproc_trajectories
from utils import fileio
from utils.logger import setup_logger

logger = setup_logger("normalize_trajectories")

TREATMENT = main_settings.TREATMENT
INPUT_DIR = preproc_trajectories.CSV_DIR
OUTPUT_DIR = preproc_trajectories.NORMALIZED_TRAJECTORIES_DIR
NORMALIZATION_DIR = preproc_trajectories.NORMALIZATION_DIR

START_TIME_FPS = main_settings.START_TIME_FPS
END_TIME_FPS = main_settings.END_TIME_FPS
REQUIRED_COLUMNS = preproc_trajectories.REQUIRED_COLUMNS

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def normalize_fly(df: pd.DataFrame, norm: dict, start: int, end: int) -> pd.DataFrame:
    """Normalize the fly trajectory data based on the provided normalization parameters."""

    df = df.iloc[start:end].copy()
    df.ffill(inplace=True)

    df["pos x"] = (df["pos x"] - norm["min_x"]) / norm["x_px_ratio"]
    df["pos y"] = (df["pos y"] - norm["min_y"]) / norm["y_px_ratio"]

    mean_ratio = (norm["x_px_ratio"] + norm["y_px_ratio"]) / 2
    df["major axis len"] /= mean_ratio

    df.reset_index(drop=True, inplace=True)
    return df


def process_group(group_name: str,
                  group_path: Path,
                  normalization_dir: Path,
                  output_dir: Path,
                  required_columns: set,
                  start: int,
                  end: int):
    """Process a single group of fly trajectory files by normalizing them and saving the results."""

    group_save_dir = output_dir / group_name
    group_save_dir.mkdir(parents=True, exist_ok=True)

    fly_dict = fileio.load_files_from_folder(group_path)
    norm_path = normalization_dir / f"{group_name}.toml"

    if not norm_path.exists():
        logger.warning(f"Normalization file not found: {norm_path}")
        return

    with open(norm_path, "r") as f:
        norm = toml.load(f)

    for fly_name, fly_path in fly_dict.items():
        try:
            df = pd.read_csv(fly_path)

            if not required_columns .issubset(df.columns):
                missing_columns = required_columns - set(df.columns)
                logger.error(f"{fly_name} in {group_name} is missing columns: {missing_columns}")
                continue

            df_norm = normalize_fly(df, norm, start, end)
            df_norm.to_csv(group_save_dir / fly_name)
        except Exception as e:
            logger.error(f"Failed to normalize {fly_name} in {group_name}: {e}")


def main_settings() -> None:
    """Main function to load all groups, process each group, and normalize the trajectory data."""
    treatment = fileio.load_multiple_folders(INPUT_DIR)
    for group_name, group_path in treatment.items():
        process_group(
            group_name=group_name,
            group_path=group_path,
            normalization_dir=NORMALIZATION_DIR,
            output_dir=OUTPUT_DIR,
            required_columns=REQUIRED_COLUMNS,
            start=START_TIME_FPS,
            end=END_TIME_FPS,
        )


if __name__ == "__main__":
    main_settings()
