"""end_voice closes voice only once the goodbye has been heard (no models, no audio)."""

import _root  # noqa: F401  (the repo root on the import path)
import asyncio
import io
import sys
from contextlib import redirect_stdout

from pipecat.frames.frames import BotStartedSpeakingFrame, BotStoppedSpeakingFrame, UserStartedSpeakingFrame
from pipecat.tests.utils import run_test

from talker import ENDING, VoiceStateReporter

results = []
closed: list[str] = []
ENDING.on_end = closed.append


def ok(cond, msg):
    results.append(cond)
    print(("PASS " if cond else "FAIL ") + msg)


async def settle(secs=0.5):
    await asyncio.sleep(secs)


async def main():
    ENDING.request()
    ENDING.bot_stopped()  # a pause between sentences of the goodbye
    await settle()
    ok(not closed, "a pause before the goodbye reply is complete doesn't close")
    ENDING.reply_finished(spoke=True)
    ENDING.bot_stopped()
    await settle()
    ok(closed == ["end_voice: the goodbye finished playing"], f"the speaker going quiet after the goodbye closes it: {closed}")
    ENDING.bot_stopped()
    await settle()
    ok(len(closed) == 1, "and only once")

    closed.clear()
    ENDING.request()
    ENDING.reply_finished(spoke=True)
    ENDING.cancel()  # you start talking during the goodbye
    ENDING.bot_stopped()
    await settle()
    ok(not closed, "talking during the goodbye keeps voice open")

    ENDING.request()
    ENDING.reply_finished(spoke=False)
    await settle(0.1)
    ok(closed == ["end_voice: ended with nothing to say"], "nothing said: closes at once")

    closed.clear()
    ENDING.FALLBACK = 0.3
    ENDING.request()
    ENDING.reply_finished(spoke=True)
    await settle()
    ok(closed == ["end_voice: the goodbye never reported finishing"], "the speaker never reports stopping: closes after the fallback")

    # The same through the pipeline's own frames, as the reporter sees them next to the speaker.
    closed.clear()
    ENDING.request()
    ENDING.reply_finished(spoke=True)
    with redirect_stdout(io.StringIO()):  # the reporter's state lines for the mod
        await run_test(VoiceStateReporter(), frames_to_send=[BotStartedSpeakingFrame(), BotStoppedSpeakingFrame()])
        await settle()
    ok(len(closed) == 1, "via the reporter: the bot stopping speaking after the goodbye closes it")
    closed.clear()
    ENDING.request()
    ENDING.reply_finished(spoke=True)
    with redirect_stdout(io.StringIO()):
        await run_test(VoiceStateReporter(), frames_to_send=[BotStartedSpeakingFrame(), UserStartedSpeakingFrame(), BotStoppedSpeakingFrame()])
        await settle()
    ok(not closed, "via the reporter: you speaking up during the goodbye keeps it open")

    print(f"{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
