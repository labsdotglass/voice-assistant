"""Model and effort by voice, offline (card #10): a new session gets --model and --effort only for values
claude takes; the model the session's mod reports is background for the talker, and a switch clears it."""

import _root  # noqa: F401  (the repo root on the import path)
import asyncio
import sys

import new_session
from talker import FEED

results = []


def ok(cond, msg):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {msg}")


async def argv_for(**kw) -> list[str]:
    """The command new_session.start would run, caught before it runs."""
    seen = []

    async def fake_exec(*argv, **_):
        seen.extend(argv)
        raise RuntimeError("caught")

    real, real_find = asyncio.create_subprocess_exec, new_session.find_claude
    asyncio.create_subprocess_exec, new_session.find_claude = fake_exec, lambda: "claude"
    try:
        await new_session.start("Hi", "", "", **kw)
    except RuntimeError:
        pass
    finally:
        asyncio.create_subprocess_exec, new_session.find_claude = real, real_find
    return seen


async def main():
    argv = await argv_for(model="Sonnet", effort="high")
    ok(argv[argv.index("--model") + 1] == "sonnet" and argv[argv.index("--effort") + 1] == "high", "a model and effort asked for: passed on")
    ok(argv.index("--effort") < argv.index("--"), "before the message, as options")
    argv = await argv_for()
    ok("--model" not in argv and "--effort" not in argv, "none asked for: your defaults")
    argv = await argv_for(model="gpt-5", effort="extreme")
    ok("--model" not in argv and "--effort" not in argv, "values claude doesn't take: left out, not passed through")

    FEED.add({"type": "switched", "session": "s1", "title": "Editor", "busy": False, "initial": True})
    FEED.pending = []
    news = FEED.add({"type": "model", "model": "claude-opus-5-5"})
    ok(not news and any("Opus 5.5" in p and "only when the user asks" in p and "claude-" not in p for p in FEED.pending),
       "the model is background, never news, and by the name you'd say, not its id")
    FEED.pending = []
    FEED.add({"type": "model", "model": "claude-opus-5-5"})
    ok(not FEED.pending, "the same model again: nothing new")
    FEED.add({"type": "switched", "session": "s2", "title": "Other", "busy": False})
    ok(FEED.model == "", "a switch: the new session reports its own")

    ok(new_session.model_choice(" Sonnet ", "HIGH") == ("sonnet", "high", []), "a model and effort as said: cleaned up")
    ok(new_session.model_choice("Opus 4.5", "turbo") == ("", "", ["Opus 4.5", "turbo"]),
       "ones voice can't pick: left to the defaults, and named so the talker says so")
    ok(new_session.spoken_model("claude-sonnet-5-5") == "Sonnet 5.5" and new_session.spoken_model("claude-opus-5-5[1m]") == "Opus 5.5",
       "a model id as you'd say it")

    print(f"{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
