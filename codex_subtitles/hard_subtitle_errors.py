class StageError(RuntimeError):
    def __init__(self, code, message, *, stage, artifact=None, job_id=None,
                 retryable=True, prior_artifacts_valid=True, available_backends=None, next_action=None):
        super().__init__(message)
        self.details = dict(code=code, message=message, stage=stage, artifact=str(artifact) if artifact else None,
                            job_id=job_id, retryable=retryable, prior_artifacts_valid=prior_artifacts_valid,
                            available_backends=available_backends, next_action=next_action)
