import logging
from pathlib import Path

from settings.main_settings import LOGS_DIR


def setup_logger(name: str, log_file: str = None, level=logging.INFO) -> logging.Logger:
    log_path = Path(log_file) if log_file else LOGS_DIR / f"{name}.log"
    LOGS_DIR.mkdir(exist_ok=True)

    logger = logging.getLogger(name)
    logger.setLevel(level)

    if not logger.handlers:  # Prevent adding duplicate handlers
        fh = logging.FileHandler(log_path)
        fh.setLevel(level)

        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        fh.setFormatter(formatter)

        logger.addHandler(fh)

    return logger
