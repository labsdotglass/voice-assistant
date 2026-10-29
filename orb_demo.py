"""Shows the orb's edge snapping: drops it at spots around the screen, as a drag would, while you watch.

    uv run orb_demo.py          # voice must be on; the orb ends up back in the bottom right corner

Each drop prints where it let go; the orb then slides against any edge near it (see the voice log's
"orb dropped" lines for where it snapped to).
"""

import asyncio
import sys

import aiohttp

from home import SOCKET

# (where its centre is let go, as fractions of the screen; what should happen)
DROPS = [
    (0.06, 0.50, "near the left edge: snaps to it"),
    (0.50, 0.95, "near the top: snaps to it"),
    (0.95, 0.93, "near the top right corner: snaps into the corner"),
    (0.50, 0.50, "in the middle: stays where it was let go"),
    (0.04, 0.05, "near the bottom left corner: snaps into the corner"),
    (0.96, 0.45, "near the right edge: snaps to it"),
    (0.97, 0.04, "near the bottom right corner: back home"),
]


async def main():
    async with aiohttp.ClientSession(connector=aiohttp.UnixConnector(path=str(SOCKET))) as http:
        for x, y, what in DROPS:
            print(f"letting go {what}")
            async with http.post("http://voice/orb-drop", json={"x": x, "y": y}) as r:
                if not (await r.json())["ok"]:
                    sys.exit("the orb isn't showing (voice is off, or VOICE_ORB=0)")
            await asyncio.sleep(2.5)


asyncio.run(main())
