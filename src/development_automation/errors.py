class DevelopmentAutomationError(Exception):
    """Base error for development automation control-plane failures."""


class DuplicateMessageIdError(DevelopmentAutomationError):
    """Raised when append-only storage receives a repeated message ID."""


class InvalidTransitionError(DevelopmentAutomationError):
    """Raised when a run event attempts an unsupported state transition."""


class MalformedControlDocumentError(DevelopmentAutomationError):
    """Raised when a Markdown plus YAML control document cannot be parsed."""


class PathTraversalError(DevelopmentAutomationError):
    """Raised when a requested control-plane path escapes its root directory."""


class StaleHeadError(DevelopmentAutomationError):
    """Raised when an event references a stale Git head SHA."""


class StopBoundaryError(DevelopmentAutomationError):
    """Raised when workflow execution crosses the milestone stop boundary."""


class TaskLimitError(DevelopmentAutomationError):
    """Raised when repair or no-progress limits are exceeded."""