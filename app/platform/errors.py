"""Application error types."""


class AppError(Exception):
    code = 'E_UNKNOWN'
    http_status = 500

    def __init__(self, message: str = 'An unexpected error occurred'):
        self.message = message
        super().__init__(self.message)


class ValidationError(AppError):
    code, http_status = 'E_INVALID_INPUT', 400


class NotFoundError(AppError):
    code, http_status = 'E_NOT_FOUND', 404


class UnauthorizedError(AppError):
    code, http_status = 'E_UNAUTHORIZED', 401


class ForbiddenError(AppError):
    code, http_status = 'E_FORBIDDEN', 403


class RateLimitError(AppError):
    code, http_status = 'E_RATE_LIMITED', 429

    def __init__(self, message: str = 'Too many requests. Try again shortly.'):
        super().__init__(message)


class TenantViolation(RuntimeError):
    """An attempt to read or write across organizations.

    Its own type rather than a bare RuntimeError: in a system whose whole
    job is keeping tenants apart, this is the most significant thing that
    can go wrong, and it should not be indistinguishable in a log from a
    programming mistake.
    """


class EmailNotConfiguredError(AppError):
    code, http_status = 'E_EMAIL_NOT_CONFIGURED', 400

    def __init__(self, message: str = 'No email service is configured'):
        super().__init__(message)


class EmailSendError(AppError):
    """The provider was asked to send and refused.

    Distinct from EmailNotConfiguredError, which means nothing was asked:
    this one carries the provider's own words, so the operator reads what
    Mailgun or the SMTP server actually said rather than a guess.
    """

    code, http_status = 'E_EMAIL_SEND_FAILED', 502

    def __init__(self, message: str = 'The email provider refused the message'):
        super().__init__(message)
