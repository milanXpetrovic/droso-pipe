# Worker

A worker asks the NAS for work, runs the tool locally and hands the results back to `staging/`.
The NAS checks them and stores them as assets. Recording always has priority: the worker waits while
recording software runs and, optionally, outside its active hours.

## Rig 1 PC (Windows)

1. **Register the worker.** Admin → Workers → Add: name `rig1-win`, tools `["crop@1.0.0"]`, Save.
   The token is shown once in a yellow message; copy it.

2. **Code and Python.** Put the repository at `C:\droso-pipe` (git clone, or copy `worker\` and `tools\`).
   Python 3.11 or newer, then:

        py -m pip install imageio-ffmpeg psutil numpy scipy h5py pandas pyarrow

   For tracking, also FlyTracker (MATLAB with the Image Processing Toolbox must already be installed):

        git clone https://github.com/kristinbranson/FlyTracker.git C:\FlyTracker

   In the admin, add `flytracker@1.0.0` to the worker's tools.

3. **Access as `worker`.** Z: is connected as `lab`, which may not write to `staging\`. Windows allows only
   one user per server *name*, so connect the second user through another name for the same NAS:

        net use \TRUENAS\videos /user:worker /persistent:yes
        dir \TRUENAS\videos\staging

   (If Z: uses `\TRUENAS`, use `\10.11.48.18` here instead; the point is that the two names differ.)

4. **Config.** Copy `worker.example.toml` to `worker.toml` (never into git), set `token`,
   `root = '\TRUENAS\videos'`, and the tool command with the full path to python if `python` is not on PATH.

5. **Test once,** with `active_hours` commented out:

        py C:\droso-pipe\worker\droso_worker.py --config C:\droso-pipe\worker\worker.toml --once

6. **Start automatically.** Task Scheduler → Create Task:
   - General: run only when user is logged on (the share connection belongs to the user)
   - Trigger: At log on
   - Action: program `cmd.exe`, arguments
     `/c py C:\droso-pipe\worker\droso_worker.py --config C:\droso-pipe\worker\worker.toml >> C:\w\worker.log 2>&1`
   - Settings: if the task fails, restart every 1 minute; do not stop the task if it runs long
   - Antivirus: exclude `C:\w\` (temporary files)

## milky (Linux, no root)

Use `mode = "rclone"` with an rclone remote that logs in as `worker`, and `uv` for Python.
Same program, same config otherwise.
