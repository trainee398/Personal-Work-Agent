"""User-facing failures with the exit codes defined in the specification."""

from enum import IntEnum


class ExitCode(IntEnum):
    USAGE = 2
    NOT_FOUND = 3
    DENIED = 4
    REMOTE = 5
    CONFIGURATION = 6
    BUSY = 7


class WorkPilotError(Exception):
    def __init__(self, message: str, code: ExitCode = ExitCode.CONFIGURATION) -> None:
        super().__init__(message)
        self.code = code


class NotFoundError(WorkPilotError):
    def __init__(self, message: str) -> None:
        super().__init__(message, ExitCode.NOT_FOUND)


class PolicyError(WorkPilotError):
    def __init__(self, message: str) -> None:
        super().__init__(message, ExitCode.DENIED)
