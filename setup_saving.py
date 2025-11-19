# %%
from pathlib import Path

# SET BASE PATH HERE
BASE_PATH = Path("./test").resolve()


def create_pipeline_dirs(base: Path):
    base.mkdir(parents=True, exist_ok=True)

    pipeline_root = base / "drosophila-pipeline"
    pipeline_root.mkdir(exist_ok=True)

    # Main directories
    main_dirs = [
        "data",
        "1_video-preproc",
        "2_tracking-flytracker",
        "3_preproc-trajectories",
        "4_drosophila-social-interaction-criteria",
        "5_drosophila-social-network-analysis",
    ]
    for d in main_dirs:
        (pipeline_root / d).mkdir(parents=True, exist_ok=True)

    # Subdirectories inside 3_preproc-trajectories
    sub_dirs = [
        "1_extract-from-excel",
        "2_get_normalization",
        "3_preproc-trajectories",
    ]
    traj_path = pipeline_root / "3_preproc-trajectories"
    for d in sub_dirs:
        (traj_path / d).mkdir(parents=True, exist_ok=True)

    print(f"[✓] Pipeline folders created at: {pipeline_root}")


# Run it
create_pipeline_dirs(BASE_PATH)
