"""`python -m ultracua.daemon` -> run the JSON-RPC server on stdio."""

from __future__ import annotations

from .server import main

if __name__ == "__main__":
    main()
