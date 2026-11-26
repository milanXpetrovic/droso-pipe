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
fit_masks_to_video(job.calibration, job.video);
fprintf('FlyTracker on %s\n', job.video);
simple_noninteractive_flytracker(job.out_dir, job.video, job.calibration, options);
fprintf('FlyTracker finished\n');
end

function fit_masks_to_video(calib_file, video)
% The template was made on one crop, but crops differ a little in size (each is cut around its own arena),
% and FlyTracker needs masks the size of the video's frames. In circle mode the arena is found again in
% every video (force_arena_calib), so the masks only need the right size; in full-frame mode the whole
% frame is the arena. calib_file is the worker's local copy of the template, never the template itself.
s = load(calib_file);
v = VideoReader(video);
sz = [v.Height, v.Width];
clear v
if isequal(size(s.calib.mask), sz)
    return
end
fprintf('Calibration masks %dx%d -> video %dx%d\n', size(s.calib.mask, 1), size(s.calib.mask, 2), sz(1), sz(2));
whole = ones(sz);
s.calib.mask = whole;
s.calib.full_mask = whole;
s.calib.masks = repmat({whole}, 1, max(1, numel(s.calib.masks)));
save(calib_file, '-struct', 's');
end
