"""OpenGxP gateway: one MCP server that mounts every OpenGxP sub-server.
Tool names already carry their server prefix (cmc_, stab_, sdtm_, ind_, ref_)."""

from fastmcp import FastMCP

from opengxp import __version__
from opengxp.cmc.server import cmc
from opengxp.stability.server import stab
from opengxp.cdisc.server import sdtm
from opengxp.ind.server import ind
from opengxp.ref.server import ref

mcp = FastMCP(
    "opengxp",
    version=__version__,
    instructions="Guideline-as-code tools for regulated pharma work. Decision support only; verify against current guidance.",
    mask_error_details=True,
)
mcp.mount(cmc)
mcp.mount(stab)
mcp.mount(sdtm)
mcp.mount(ind)
mcp.mount(ref)


def main() -> None:
    mcp.run(show_banner=False)


if __name__ == "__main__":
    main()
