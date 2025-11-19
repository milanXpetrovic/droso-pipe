% list of all folders to be processed
folders = {'/Users/eyrun/Caltech/TestMovies/12well', ...
           '/Users/eyrun/Caltech/TestMovies/Heisenberg_2flies', ...
           '/Users/eyrun/Caltech/TestMovies/20flies', ...
           '/Users/eyrun/Caltech/TestMovies/6well_1fly'};

% set options (omit any or all to use default options)
options.granularity  = 10000;
%options.num_chunks   = 4;       % set either granularity or num_chunks
options.num_cores    = 4;
options.max_minutes  = Inf;
options.save_JAABA   = 1;
options.save_seg     = 1;

% loop through all folders
for f=1:numel(folders)
    % set parameters for specific folder
    videos.dir_in  = folders{f};
    videos.dir_out = folders{f}; % save results into video folder
    videos.filter = '*.avi';     % extension of the videos to process
    % track all videos within folder
    tracker(videos,options);
end