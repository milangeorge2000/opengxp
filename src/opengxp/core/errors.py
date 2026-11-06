from fastmcp.exceptions import ToolError


class GxPInputError(ToolError):
    """An input the calling agent can fix. The message always says how to fix it.
    Subclasses ToolError so the message reaches the client even with masked error details."""
