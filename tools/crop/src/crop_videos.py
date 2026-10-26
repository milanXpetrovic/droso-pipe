# %%
import subprocess
import sys
import time
from pathlib import Path
from typing import Tuple

import cv2
import toml
from moviepy.editor import VideoFileClip

from settings import main_settings, video_preproc
from utils import fileio
from utils.logger import setup_logger

# flake8: noqa: E402
sys.path.append(str(Path(__file__).resolve().parent.parent.parent))


logger = setup_logger("video_preproc_crop")
logger.info("Starting video cropping process")


def ffmpeg_crop(
    input_file: Path,
    output_file: Path,
    x: int,
    y: int,
    width: int,
    height: int,
    start: int,
    duration: int,
    threads: int
):
    output_file.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-ss", str(start),
        "-i", str(input_file),
        "-t", str(duration),
        "-filter:v", f"crop={width}:{height}:{x}:{y}",
        "-an",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-threads", str(threads),
        str(output_file)
    ]

    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        logger.error(f"FFmpeg failed for {output_file.name}: {e}")


def get_video_datetime(video_name: str) -> Tuple[str, str, str, str, str]:
    """Extract date and time from video name."""
    video_name = video_name.replace('.mp4', '').split()[-1].split('_')
    date = video_name[1]
    time = video_name[2]

    year = date[:4]
    month = date[4:6]
    day = date[6:8]
    hour = time[:2]
    minutes = time[2:4]

    return year, month, day, hour, minutes


def build_output_filename(output_path: Path,
                          treatment: str,
                          day: str,
                          month: str,
                          year: str,
                          hour: str,
                          minutes: str,
                          arena_num: str
                          ) -> Path:
    """Generate output filename for cropped videos."""
    video_name = f"{treatment}_{arena_num}_{day}_{month}_{year}-{hour}_{minutes}{main_settings.VIDEO_EXTENSION}"
    output_filename = output_path / video_name

    return output_filename


def crop_video_old(
    video_path: str,
    video_name: str,
    output_path: str,
    treatment: str,
    start: int,
    video_duration: int,
    num_threads: int,
    increment: int
) -> None:
    """
    Crop a video into 4 sections (top-left, top-right, bottom-left, bottom-right), 
    based on the provided coordinates, and save the cropped sections to output files.

    Parameters:
        video_path (str): Path to the input video file.
        video_name (str): Name of the video (used to extract date and time).
        output_path (str): Directory where cropped video files will be saved.
        treatment (str): Treatment label to be included in the output filenames.
        start (int): Start time (in seconds) for video cropping.
        video_duration (int): Duration (in seconds) of the video to crop.

    Returns:
        None: Saves the cropped video sections as output files in the specified directory.
    """

    try:
        video = VideoFileClip(video_path)
    except Exception as e:
        logger.error(f"Error loading video {video_path}: {e}")
        return

    width, height = video.size
    middle_x, middle_y = width // 2, height // 2

    coordinates = [
        (0, 0, middle_x, middle_y),            # top_left
        (middle_x, 0, width, middle_y),        # top_right
        (0, middle_y, middle_x, height),       # bottom_left
        (middle_x, middle_y, width, height)    # bottom_right
    ]

    year, month, day, hour, minutes = get_video_datetime(video_name)
    output_path = Path(output_path) / treatment
    output_path.mkdir(parents=True, exist_ok=True)

    arenas = ['A1', 'A2', 'A3', 'A4']
    output_files = [
        build_output_filename(output_path, treatment, day, month, year, hour, minutes, arena_num)
        for arena_num in arenas
    ]

    seconds_increment = 0
    for (x1, y1, x2, y2), output_file in zip(coordinates, output_files):
        cropped_video = video.crop(x1=x1, y1=y1, x2=x2, y2=y2)
        cropped_video = cropped_video.subclip(start + seconds_increment, start + video_duration + seconds_increment)
        cropped_video.write_videofile(
            output_file, codec='libx264', fps=video.fps, audio=False, threads=num_threads)
        cropped_video.close()

        seconds_increment += increment

    video.close()


def get_video_resolution(video_path: str) -> Tuple[int, int]:

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise ValueError(f"Cannot open video file {video_path}")
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    return width, height


def crop_video(
    video_path: str,
    video_name: str,
    output_path: str,
    treatment: str,
    start: int,
    video_duration: int,
    num_threads: int,
    increment: int
) -> None:
    """
    Crop a video into 4 sections (top-left, top-right, bottom-left, bottom-right), 
    based on the provided coordinates, and save the cropped sections to output files.

    Parameters:
        video_path (str): Path to the input video file.
        video_name (str): Name of the video (used to extract date and time).
        output_path (str): Directory where cropped video files will be saved.
        treatment (str): Treatment label to be included in the output filenames.
        start (int): Start time (in seconds) for video cropping.
        video_duration (int): Duration (in seconds) of the video to crop.

    Returns:
        None: Saves the cropped video sections as output files in the specified directory.
    """

    try:
        # Get original video dimensions
        original_width, original_height = get_video_resolution(video_path)
    except ValueError as e:
        logger.error(f"Error getting video resolution for {video_path}: {e}")
        return
    except Exception as e:  # Catch any other unexpected errors from get_video_resolution
        logger.error(f"Unexpected error getting video resolution for {video_path}: {e}")
        return

    # Calculate dimensions for the four quadrants
    # These ensure that the whole video is covered, even with odd dimensions
    crop_w1 = original_width // 2
    crop_h1 = original_height // 2
    crop_w2 = original_width - crop_w1  # Width for the right-side quadrants
    crop_h2 = original_height - crop_h1  # Height for the bottom-side quadrants

    # Define coordinates for FFmpeg's crop filter: (x_offset, y_offset, crop_width, crop_height)
    coordinates = [
        (0, 0, crop_w1, crop_h1),                # Top-Left
        (crop_w1, 0, crop_w2, crop_h1),          # Top-Right
        (0, crop_h1, crop_w1, crop_h2),          # Bottom-Left
        (crop_w1, crop_h1, crop_w2, crop_h2)     # Bottom-Right
    ]

    year, month, day, hour, minutes = get_video_datetime(video_name)
    output_path_obj = Path(output_path) / treatment  # Renamed to avoid conflict with output_path parameter
    output_path_obj.mkdir(parents=True, exist_ok=True)

    arenas = ['A1', 'A2', 'A3', 'A4']  # TL, TR, BL, BR
    output_files = [
        build_output_filename(output_path_obj, treatment, day, month, year, hour, minutes, arena_num)
        for arena_num in arenas
    ]

    seconds_increment = 0
    for (x, y, w, h), output_file_path in zip(coordinates, output_files):  # Renamed output_file to output_file_path
        ffmpeg_crop(
            Path(video_path),
            output_file_path,
            x, y, w, h,
            start + seconds_increment,
            video_duration,
            num_threads
        )
        logger.info(f"Cropped: {output_file_path.name}")
        seconds_increment += increment


def load_processed_videos(toml_path: Path) -> dict:
    """Load processed videos from TOML file."""
    return toml.load(toml_path) if toml_path.exists() else {}


def update_processed_videos(video_name: str, toml_path: Path) -> None:
    """Add or update the video entry in the TOML file as processed."""
    toml_path.parent.mkdir(parents=True, exist_ok=True)  # ensure directory exists
    data = load_processed_videos(toml_path)
    if video_name not in data:
        data[video_name] = True
        with toml_path.open("w") as f:
            toml.dump(data, f)


def main():
    VIDEOS_LOCATION = video_preproc.VIDEOS_LOCATION
    TREATMENT = main_settings.TREATMENT
    SAVE_PATH = video_preproc.SAVE_PATH
    START_SECONDS = main_settings.START_TIME_SECONDS
    DURATION_SECONDS = main_settings.END_TIME_SECONDS
    VIDEO_EXTENSION = main_settings.VIDEO_EXTENSION

    INPUT_VIDEO_PATH = Path(VIDEOS_LOCATION) / TREATMENT
    videos = fileio.load_files_from_folder(INPUT_VIDEO_PATH, VIDEO_EXTENSION)

    toml_path = video_preproc.PROCESSED_VIDEOS_DIR / f"{TREATMENT}.toml"
    processed_videos = load_processed_videos(toml_path)

    start = time.time()
    for video_name, video_path in videos.items():

        # TODO: remove this, was just for test
        print(f"working with {video_name}")

        if processed_videos.get(video_name):
            continue

        # TODO: implement metadata file of video load

        crop_video(video_path,
                   video_name,
                   SAVE_PATH,
                   TREATMENT,
                   START_SECONDS,
                   DURATION_SECONDS,
                   main_settings.NUM_THREADS,
                   main_settings.SECONDS_INCREMENT
                   )
        logger.info(f'Processed video: {video_name}')
        update_processed_videos(video_name, toml_path)

    logger.info(f'Processing time: {time.time() - start}')


if __name__ == '__main__':
    main()
