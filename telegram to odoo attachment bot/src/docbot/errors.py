class DocbotError(Exception):
    """Base class for all application errors."""


class ConfigError(DocbotError):
    """Configuration is missing or invalid. Carries every problem found, not just the first."""

    def __init__(self, problems: list[str]):
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


class InvalidTransition(DocbotError):
    def __init__(self, src: str, dst: str):
        self.src = src
        self.dst = dst
        super().__init__(f"job status transition {src} -> {dst} is not allowed")


class LeaseLost(DocbotError):
    """The caller no longer owns the job (lease expired and another worker took it, or job is unlocked)."""


class TransientError(DocbotError):
    """Retryable failure (Odoo down, network, timeout). The job keeps its status and is retried with backoff.

    ``counts=False``: a problem of the setup, not of the job (e.g. Odoo refuses our login until someone fixes
    the API key). The job waits and is retried, but the attempt does not count, so it never ends up failed.
    """

    def __init__(self, message: str = "", *, retry_after: float | None = None, counts: bool = True):
        self.retry_after = retry_after
        self.counts = counts
        super().__init__(message)


class PermanentError(DocbotError):
    """Non-retryable failure. The job goes to `failed`."""
