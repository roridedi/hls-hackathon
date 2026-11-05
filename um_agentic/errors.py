class UMError(Exception):
    """A safe, user visible domain error."""


class NotFound(UMError):
    pass


class Conflict(UMError):
    pass


class Forbidden(UMError):
    pass


class InvalidRequest(UMError):
    pass


class AgentFailure(UMError):
    pass
