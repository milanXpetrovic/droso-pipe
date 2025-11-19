# %%
import math
import sys

import pandas as pd
import toml

from settings import main_settings, preproc_trajectories
from utils import fileio
from utils.logger import setup_logger

logger = setup_logger("trajectories_normalization")

TREATMENT = main_settings.TREATMENT
INPUT_DIR = preproc_trajectories.CSV_DIR
OUTPUT_DIR = preproc_trajectories.NORMALIZATION_DIR
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def calculate_boundaries(fly_dict):
    """Get min and max x/y values from all flies in the group."""
    min_x, min_y = float("inf"), float("inf")
    max_x, max_y = float("-inf"), float("-inf")

    for path in fly_dict.values():
        df = pd.read_csv(path)
        min_x = min(min_x, df["pos x"].min())
        max_x = max(max_x, df["pos x"].max())
        min_y = min(min_y, df["pos y"].min())
        max_y = max(max_y, df["pos y"].max())

    return min_x, min_y, max_x, max_y


def compute_ratios(min_x, min_y, max_x, max_y):
    """Calculate pixel-to-mm ratios using arena size and bounds."""
    center_x = (min_x + max_x) / 2
    center_y = (min_y + max_y) / 2

    dist_x = math.dist((min_x, center_y), (max_x, center_y))
    dist_y = math.dist((center_x, min_y), (center_x, max_y))
    ARENA_DIAMETER = main_settings.ARENA_DIAMETER

    return {
        "min_x": float(min_x),
        "min_y": float(min_y),
        "x_px_ratio": dist_x / ARENA_DIAMETER,
        "y_px_ratio": dist_y / ARENA_DIAMETER,
    }


def process_group(group_name, group_path):
    """Compute and save normalization data for one group."""
    fly_dict = fileio.load_files_from_folder(group_path)
    if not fly_dict:
        logger.warning(f"No CSVs found in {group_path}")
        return

    try:
        min_x, min_y, max_x, max_y = calculate_boundaries(fly_dict)
        norm_data = compute_ratios(min_x, min_y, max_x, max_y)
        output_path = OUTPUT_DIR / f"{group_name}.toml"

        with open(output_path, "w") as f:
            toml.dump(norm_data, f)

        logger.info(f"Processed {group_name}")

    except Exception as e:
        logger.error(f"Failed processing {group_name}: {e}")


def main():
    """Run normalization for all treatment groups."""
    treatment = fileio.load_multiple_folders(INPUT_DIR)
    for group_name, group_path in treatment.items():
        print(group_name)
        process_group(group_name, group_path)


if __name__ == "__main__":
    main()
