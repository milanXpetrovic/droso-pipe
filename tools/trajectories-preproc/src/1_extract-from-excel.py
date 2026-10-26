# %%
from pathlib import Path

import pandas as pd

from settings import main_settings, preproc_trajectories
from utils import fileio
from utils.logger import setup_logger

logger = setup_logger("xls_to_csv")

TREATMENT = main_settings.TREATMENT
INPUT_DIR = preproc_trajectories.INPUT_XLSX_DIR
OUTPUT_DIR = preproc_trajectories.CSV_DIR
SKIP_SHEET = preproc_trajectories.SKIP_SHEET
SUPPORTED_FORMAT = preproc_trajectories.SUPPORTED_FORMAT

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def convert_xlsx_to_csv(input_dir: Path,
                        output_dir: Path,
                        skip_sheet: str,
                        supported_formats
                        ):
    """Convert XLSX files to CSV."""
    treatment = fileio.load_multiple_folders(input_dir)
    print(treatment)

    for group_name, group_dir in treatment.items():
        # TODO: fix that file_format=supported_formats, to work with tuples
        group_xlsx_files = fileio.load_files_from_folder(Path(group_dir), file_format=SUPPORTED_FORMAT)
        if not group_xlsx_files:
            logger.warning(f"No Excel files found in {group_dir}")
            continue

        save_dir = output_dir / group_name
        save_dir.mkdir(parents=True, exist_ok=True)

        for xls_name, xls_path in group_xlsx_files.items():
            try:
                df_dict = pd.read_excel(xls_path, sheet_name=None)
            except Exception as e:
                logger.error(f"Failed to read {xls_path}: {e}")
                continue

            for sheet_name, sheet_data in df_dict.items():
                if sheet_name == skip_sheet:
                    continue
                save_path = save_dir / f"{sheet_name}.csv"
                try:
                    sheet_data.to_csv(save_path, index=False)
                except Exception as e:
                    logger.error(f"Failed to save CSV {save_path}: {e}")

        logger.info(f"Finished processing group: {group_name}")


if __name__ == "__main__":
    convert_xlsx_to_csv(INPUT_DIR, OUTPUT_DIR, SKIP_SHEET, SUPPORTED_FORMAT)
