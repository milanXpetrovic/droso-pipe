#%%
import subprocess
from pathlib import Path

# Adjust these paths
VIDEOS_DIR = "/srv/milky/drosophila-pipeline/video-preproc/output"
SCRIPTS_DIR = "/srv/milky/drosophila-pipeline/tracking-scripts"
CONTAINER_NAME = "boring_turing"
MATLAB_SCRIPT = "run_tracker_batch"
TREATMENT = "CS_old8-10d_24group"  # <-- change or make dynamic if needed


def container_exists(name: str) -> bool:
    result = subprocess.run(
        ["docker", "ps", "-q", "-f", f"name={name}"],
        capture_output=True, text=True
    )
    return result.stdout.strip() != ""


def start_container():
    print("Starting MATLAB container with mounted volumes...")
    subprocess.run([
        "docker", "run", "-d",
        "--name", CONTAINER_NAME,
        "-p", "8888:8888",
        "-v", f"{VIDEOS_DIR}:/videos",
        "-v", f"{SCRIPTS_DIR}:/scripts",
        "matlab_fly:r2023b"
    ], check=True)


def run_tracker(folders):
    # Format MATLAB cell array string
    matlab_folders = "{" + ",".join(f"'/videos/{TREATMENT}/{Path(f).name}'" for f in folders) + "}"
    matlab_cmd = f"addpath('/scripts'); {MATLAB_SCRIPT}({matlab_folders}); exit;"

    print("Running tracker inside container...")
    subprocess.run([
        "docker", "exec", CONTAINER_NAME,
        "matlab", "-batch", matlab_cmd
    ], check=True)


def main():
    treatment_path = Path(VIDEOS_DIR) / TREATMENT
    if not treatment_path.exists():
        raise FileNotFoundError(f"Treatment folder does not exist: {treatment_path}")

    folders_to_track = [str(f) for f in treatment_path.iterdir() if f.is_dir()]

    if not container_exists(CONTAINER_NAME):
        start_container()

    run_tracker(folders_to_track)


if __name__ == "__main__":
    main()
