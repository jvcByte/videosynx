"""Let an agent drive videosynx: submit renders, read what came out.

The package is split so the HTTP client is usable without the MCP server.
`client` knows how to talk to the API and nothing else; `server` binds it
to tools. The in-product agent pane wants the first without the second.
"""

from . import client

__all__ = ["client"]
