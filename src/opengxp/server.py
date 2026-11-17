"""OpenGxP gateway: one MCP server that mounts every OpenGxP sub-server.
Tool names already carry their server prefix (cmc_, stab_, sdtm_, label_)."""

from fastmcp import FastMCP

from opengxp import __version__
from opengxp.cmc.server import cmc

mcp = FastMCP(
    "opengxp",
    version=__version__,
    instructions="Guideline-as-code tools for regulated pharma work. Decision support only; verify against current guidance.",
    mask_error_details=True,
)
mcp.mount(cmc)


def main() -> None:
    mcp.run(show_banner=False)


if __name__ == "__main__":
    main()
