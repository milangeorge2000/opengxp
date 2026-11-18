"""A small third-party-style MCP server used as a proxy target in tests."""
from fastmcp import FastMCP

srv = FastMCP("lims-demo")


@srv.tool(annotations={"readOnlyHint": True})
def get_batch(batch_id: str) -> dict:
    return {"summary": f"Batch {batch_id}: released", "batch_id": batch_id, "status": "released"}


@srv.tool(annotations={"readOnlyHint": False})
def update_batch_status(batch_id: str, status: str) -> dict:
    return {"summary": f"Batch {batch_id} set to {status}", "batch_id": batch_id, "status": status}


if __name__ == "__main__":
    srv.run(show_banner=False)
