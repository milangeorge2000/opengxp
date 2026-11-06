from opengxp.core.envelope import DISCLAIMER, Citation, Finding, GxPResult, Provenance, provenance
from opengxp.core.errors import GxPInputError

# Default MCP annotations for deterministic, offline calculators.
READ_ONLY_OFFLINE = {
    "readOnlyHint": True,
    "idempotentHint": True,
    "destructiveHint": False,
    "openWorldHint": False,
}

__all__ = [
    "DISCLAIMER", "Citation", "Finding", "GxPResult", "Provenance", "provenance",
    "GxPInputError", "READ_ONLY_OFFLINE",
]
