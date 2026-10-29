"""A session voice starts or goes to: the orb shows a spinner while it opens, and ticks once it's open.
Cleared on failure (no tick) and when the opening breaks."""

import _root  # noqa: F401  (the repo root on the import path)
import asyncio
import sys

import new_session

results = []


def ok(cond, msg):
    results.append(cond)
    print(("PASS " if cond else "FAIL ") + msg)


class FakeOrb:
    def __init__(self):
        self.calls = []

    def set_orb(self, **state):
        self.calls.append(("set", state))

    def tick(self):
        self.calls.append(("tick",))


async def opening(result, seen, orb):
    seen.append(list(orb.calls))  # what the orb had been told while this ran
    if isinstance(result, Exception):
        raise result
    return result


async def main():
    orb, seen = FakeOrb(), []
    got = await new_session.on_the_orb(orb, opening((True, "open"), seen, orb))
    ok(seen[0] == [("set", {"opening": True})], "spinner on while the session opens")
    ok(orb.calls[1:] == [("set", {"opening": False}), ("tick",)], "opened: spinner off, then a tick")
    ok(got == (True, "open"), "passes on what opening said")

    orb, seen = FakeOrb(), []
    await new_session.on_the_orb(orb, opening((False, "didn't start"), seen, orb))
    ok(orb.calls == [("set", {"opening": True}), ("set", {"opening": False})], "failed: spinner off, no tick")

    orb, seen = FakeOrb(), []
    try:
        await new_session.on_the_orb(orb, opening(RuntimeError("boom"), seen, orb))
        ok(False, "an error is passed on")
    except RuntimeError:
        ok(orb.calls[-1] == ("set", {"opening": False}), "broke: spinner off anyway")

    orb, seen = FakeOrb(), []
    started = asyncio.get_running_loop().time()
    await new_session.on_the_orb(orb, opening((True, "open"), seen, orb), settle=0.3)
    ok(asyncio.get_running_loop().time() - started >= 0.3 and orb.calls[-1] == ("tick",), "opened: spinner stays while the app switches, then a tick")

    orb, seen = FakeOrb(), []
    started = asyncio.get_running_loop().time()
    await new_session.on_the_orb(orb, opening((False, "didn't start"), seen, orb), settle=0.3)
    ok(asyncio.get_running_loop().time() - started < 0.1, "failed: no wait")

    got = await new_session.on_the_orb(None, opening((True, "open"), [], FakeOrb()))
    ok(got == (True, "open"), "no orb: still opens")

    print(f"\n{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
