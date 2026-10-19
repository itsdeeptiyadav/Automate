from enum import Enum


class TaskStatus(str, Enum):
    PENDING = "PENDING"
    PLANNING = "PLANNING"
    RUNNING = "RUNNING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    NEEDS_INPUT = "NEEDS_INPUT"
    CANCELLED = "CANCELLED"


TERMINAL_STATUSES = {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}
ACTIVE_STATUSES = {TaskStatus.PLANNING, TaskStatus.RUNNING, TaskStatus.VERIFYING}


class ErrorType(str, Enum):
    VALIDATION = "VALIDATION"      # bad arguments / unparsable input
    PERMISSION = "PERMISSION"      # outside workspace, write not approved
    NOT_FOUND = "NOT_FOUND"        # nothing matched
    CONFLICT = "CONFLICT"          # duplicate / constraint violation
    TRANSIENT = "TRANSIENT"        # worth retrying (timeouts, flaky IO)
    VERIFICATION = "VERIFICATION"  # outcome did not match source of truth
    LLM = "LLM"                    # model unavailable or invalid output
    INTERNAL = "INTERNAL"          # unexpected bug


class EventType(str, Enum):
    STATE = "state"
    PLAN = "plan"
    THOUGHT = "thought"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    TOOL_ERROR = "tool_error"
    RETRY = "retry"
    RECOVERY = "recovery"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_GRANTED = "approval_granted"
    APPROVAL_DENIED = "approval_denied"
    VERIFICATION = "verification"
    SECURITY = "security"
    NEEDS_INPUT = "needs_input"
    CANCEL = "cancel"
    FINAL = "final"
    ERROR = "error"
