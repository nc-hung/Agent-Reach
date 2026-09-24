# -*- coding: utf-8 -*-
"""``python -m agent_reach.social_scraper.mcp`` → the MCP server."""

import asyncio

from .server import main

if __name__ == "__main__":
    asyncio.run(main())
