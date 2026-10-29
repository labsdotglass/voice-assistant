"""Voice parity (no model): everything the orb's right-click menu does can also be asked for out loud.

Reads the menu from audio_helper/main.swift (the items willOpenMenu adds and the command each one logs),
then checks each command has a spoken route: an action in talker.MENU_ACTIONS that daemon.by_voice
handles (or a talker tool of its own, as Stop voice is end_voice). Every choice in the menu's submenus
(languages, voices, Pass on my words) must be findable by its spoken name too. Fails naming what's missing.
"""

import _root  # noqa: F401  (the repo root on the import path)
import ast
import re
import sys
from pathlib import Path

import settings

ROOT = Path(__file__).resolve().parent.parent
results = []

# The command an orb menu item logs -> the action that does it by voice. A new item in the menu fails
# below until it's added here, with its route in by_voice and MENU_ACTIONS (or a talker tool).
SPOKEN = {
    "show-panel": "show_transcript",
    "hide-panel": "hide_transcript",
    "clear": "clear_conversation",
    "language": "language",
    "voice": "voice",
    "pass-on": "pass_on",
    "more enroll": "learn_voice",
    "more tour": "show_tour",
    "more progress": "progress",
    "stop": "tool:end_voice",
}


def ok(cond, msg):
    results.append(cond)
    print(f"{'PASS' if cond else 'FAIL'}  {msg}")


def menu_commands(swift: str) -> dict[str, str]:
    """The orb menu's items: {title: the command it logs}, from willOpenMenu and the handlers it names."""
    body = swift[swift.index("func willOpenMenu"):]
    body = body[:body.index("\n    }\n")]
    handlers = {f: c.strip() for f, c in re.findall(r'@objc func (\w+)\([^)]*\) \{ log\("command: ([^"\\]+)', swift)}
    items = {}
    for title, selector in re.findall(r'NSMenuItem\(title: "([^"]+)", action: #selector\((\w+)', body):
        items[title] = handlers.get(selector, f"? (no handler {selector})")
    for title, selector in re.findall(r'\("([^"]+)", #selector\((\w+)\)\)', body):  # More's list of (title, selector)
        items[title] = handlers.get(selector, f"? (no handler {selector})")
    for title, selector in re.findall(r'addChoices\(to: menu, title: "([^"]+)".*?#selector\((\w+)', body):
        items[title] = handlers.get(selector, f"? (no handler {selector})")  # the choice's code follows the command
    return items


def module_list(source: str, name: str) -> list[str]:
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
            return ast.literal_eval(node.value)
    return []


def by_voice_actions(source: str) -> set[str]:
    """The actions daemon.by_voice handles: every string it compares `action` with."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef) and node.name == "by_voice":
            found = set()
            for cmp in ast.walk(node):
                if isinstance(cmp, ast.Compare) and getattr(cmp.left, "id", None) == "action":
                    for c in ast.walk(cmp.comparators[0]):
                        if isinstance(c, ast.Constant) and isinstance(c.value, str):
                            found.add(c.value)
            return found
    return set()


def talker_tools(source: str) -> set[str]:
    return {d.args[0].value for node in ast.parse(source).body if isinstance(node, ast.AsyncFunctionDef)
            for d in node.decorator_list if isinstance(d, ast.Call) and getattr(d.func, "id", None) == "tool"}


def gaps(items: dict[str, str], menu_actions: list[str], handled: set[str], tools: set[str]) -> list[str]:
    """What the menu offers that voice can't do, one line each."""
    missing = []
    for title, command in items.items():
        route = SPOKEN.get(command)
        if route is None:
            missing.append(f"menu item {title!r} (command {command!r}) has no spoken route: add it to by_voice "
                           "(daemon.py) and MENU_ACTIONS (talker.py), then to SPOKEN in test_parity.py")
        elif route.startswith("tool:"):
            if route[5:] not in tools:
                missing.append(f"menu item {title!r} relies on the talker tool {route[5:]}, which talker.py doesn't define")
        else:
            if route not in menu_actions:
                missing.append(f"menu item {title!r}: action {route!r} isn't in MENU_ACTIONS (talker.py)")
            if route not in handled:
                missing.append(f"menu item {title!r}: action {route!r} isn't handled by by_voice (daemon.py)")
    missing += [f"MENU_ACTIONS offers {a!r} but by_voice doesn't handle it" for a in menu_actions if a not in handled]
    return missing


def main():
    swift, daemon, talker = ((ROOT / f).read_text() for f in ("audio_helper/main.swift", "daemon.py", "talker.py"))
    items = menu_commands(swift)
    ok(len(items) >= 10, f"the orb menu's items are read from main.swift ({len(items)}: {', '.join(items)})")
    menu_actions, handled, tools = module_list(talker, "MENU_ACTIONS"), by_voice_actions(daemon), talker_tools(talker)
    ok(bool(menu_actions) and bool(handled), "MENU_ACTIONS and by_voice's actions are read")
    missing = gaps(items, menu_actions, handled, tools)
    ok(not missing, "every orb menu item can be asked for by voice" + "".join(f"\n      - {m}" for m in missing))

    # The menu's choices, each findable by the name you'd say.
    menu = settings.menu(settings.DEFAULTS)
    lost = [label for code, label in menu["languages"]
            if [code, label] != settings.MENU_DIVIDER and settings.find_language(label) != code]
    for code, *_ in settings.LANGUAGES:
        lost += [f"{label} ({code})" for vid, label in settings.voices(code) if settings.find_voice(label.split()[0], code) != vid]
    lost += [label for mode, label in menu["modes"] if settings.find_mode(label) != mode]
    ok(not lost, "every language, voice and Pass on my words choice is found by its spoken name"
       + (f": not found: {', '.join(lost)}" if lost else ""))

    # The check itself catches a menu item with no route, a route missing from either side, and an unknown command.
    broken = {**items, "Mute": "mute"}
    ok(any("'Mute'" in m for m in gaps(broken, menu_actions, handled, tools)), "a new menu item with no spoken route fails")
    ok(any("by_voice" in m for m in gaps(items, menu_actions, handled - {"show_tour"}, tools)), "an action by_voice drops fails")
    ok(any("MENU_ACTIONS" in m for m in gaps(items, [a for a in menu_actions if a != "progress"], handled, tools)),
       "an action missing from MENU_ACTIONS fails")
    ok(any("end_voice" in m for m in gaps(items, menu_actions, handled, tools - {"end_voice"})), "a missing talker tool fails")

    print(f"\n{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


main()
