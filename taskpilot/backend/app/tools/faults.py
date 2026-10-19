"""Demo/test-only fault injection (off unless FAULT_INJECTION is set).

write_timeout_once : the first invoice write COMMITS and then raises a simulated
                     timeout, so the agent must inspect the DB before retrying
                     (the correct behaviour is NOT to insert a second row).
read_flaky_once    : the first invoice lookup raises a simulated transient error.
"""
from app.tools.base import ToolContext, TransientToolError

_fired: set[tuple[str, str]] = set()


def maybe_fail(ctx: ToolContext, kind: str) -> None:
    if ctx.settings.fault_injection != kind:
        return
    key = (ctx.task_id, kind)
    if key in _fired:
        return
    _fired.add(key)
    raise TransientToolError(f"simulated transient failure ({kind})")
