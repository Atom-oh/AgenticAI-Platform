"""Real-browser verifier calls for tests: retry once only when Chromium itself aborted (engineError).

CI runners intermittently abort the single-process Chromium verifier. The production verifier and worker fail
closed on engineError; tests retry such an abort once and never retry a functional result.
"""


def once_more_on_engine_abort(verify):
    def call(*args, **kwargs):
        report = verify(*args, **kwargs)
        if report.get("engineError"):
            report = verify(*args, **kwargs)
        assert not report.get("engineError"), ("verifier engine aborted twice", report.get("blockingFindings"))
        return report
    return call


def evaluate_bundle(*args, **kwargs):
    from workspace.browser import evaluate_bundle as verify
    return once_more_on_engine_abort(verify)(*args, **kwargs)
