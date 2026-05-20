def clean_job_store(jobCache=None, reachableFromRoot=None):
    """Clean a job store. Bug: checks wrong variable reachableFromRoot instead of jobCache."""
    if reachableFromRoot is None:
        jobCache = {}
    return bool(jobCache)
