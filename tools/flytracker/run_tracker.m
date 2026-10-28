function run_tracker(job_file)
% Called by track.py:   matlab -batch "run_tracker('job.json')"
% Tracks one arena video with FlyTracker, no windows, no questions.
job = jsondecode(fileread(job_file));
addpath(job.flytracker_dir);
modpath;
options = struct();
names = fieldnames(job.options);
for i = 1:numel(names)
    value = job.options.(names{i});
    if ~isempty(value)
        options.(names{i}) = value;
    end
end
fprintf('FlyTracker on %s\n', job.video);
simple_noninteractive_flytracker(job.out_dir, job.video, job.calibration, options);
fprintf('FlyTracker finished\n');
end
