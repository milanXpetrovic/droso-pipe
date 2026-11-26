# Worker

A worker asks the NAS for work, runs the tool locally and hands the results back to `staging/`.
The NAS checks them and stores them as assets. Recording always has priority: the worker waits while
recording software runs and, optionally, outside its active hours.

## Rig 1 PC (Windows)

1. **Register the worker.** Admin → Workers → Add: name `rig1-win`, tools `["crop@1.1.0"]`, Save.
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

6. **Start automatically.** In a normal PowerShell window (not "Run as administrator", or the worker
   will not see the `worker` connection to the NAS):

        powershell -ExecutionPolicy Bypass -File C:\droso-pipe\worker\install-task.ps1

   This creates the task `droso-worker`: starts at log on without a window (`run-worker.ps1`), restarts the
   worker a minute after it stops, logs to `C:\w\worker.log`. Run it again after changing `worker.toml`. The Machines page shows what it does. Remove with
   `Unregister-ScheduledTask -TaskName droso-worker -Confirm:$false`.
   Antivirus: exclude `C:\w\` (temporary files).

## milky (Linux, no root)

Use `mode = "rclone"` with an rclone remote that logs in as `worker`, and `uv` for Python.
Same program, same config otherwise.
