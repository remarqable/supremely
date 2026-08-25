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


class EmailNotConfiguredError(AppError):
    code, http_status = 'E_EMAIL_NOT_CONFIGURED', 400

    def __init__(self, message: str = 'No email service is configured'):
        super().__init__(message)
