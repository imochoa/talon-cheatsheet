from talon import Module, actions, registry
from typing import Any, NamedTuple, Iterable
import configparser
import pathlib
import logging
import json
import re
import shutil

from enum import StrEnum


TalonMap = tuple[str, str]


def _normalize_git_url(url: str) -> str:
    """
    Normalize a git remote URL to a browsable HTTPS URL.

    git@github.com:org/repo.git  ->  https://github.com/org/repo
    https://github.com/org/repo.git  ->  https://github.com/org/repo
    """
    url = url.strip().rstrip("/")
    if url.endswith(".git"):
        url = url[: -len(".git")]
    if url.startswith("git@"):
        host, _, rest = url[len("git@"):].partition(":")
        return f"https://{host}/{rest}"
    return url


def get_submodule_urls(gitmodules_path: pathlib.Path) -> dict[str, str]:
    """
    Parse a `.gitmodules` file and return {submodule_path: https_url}.

    The submodule `path` values (e.g. "community", "rango") match the
    top-level context directory names used by the cheatsheet, so they can be
    used to link those context groups to their upstream repositories.
    """
    if not gitmodules_path.exists():
        return {}
    # .gitmodules occasionally mixes tabs and spaces for indentation, which
    # makes configparser treat indented keys as continuations of the previous
    # value. Normalize by stripping leading whitespace from every line (safe
    # here because .gitmodules values are always single-line).
    text = "\n".join(line.strip() for line in gitmodules_path.read_text().splitlines())
    parser = configparser.ConfigParser()
    parser.read_string(text)
    result: dict[str, str] = {}
    for section in parser.sections():
        if not section.lower().startswith("submodule"):
            continue
        path = parser.get(section, "path", fallback=None)
        url = parser.get(section, "url", fallback=None)
        if path and url:
            result[path.strip()] = _normalize_git_url(url)
    return result

# Lists whose contents are personal/sensitive. They are still listed in the
# cheatsheet (so you can see that they exist) but their entries are replaced
# with a redacted message instead of the real values.
# Extend this set as needed. See the "Censor sensitive info" TODO in
# cheatsheet.py.
SENSITIVE_LISTS: frozenset[str] = frozenset({
    "user.running",
    "user.contact_names",
    "user.contact_emails",
    "user.contact_full_names",
})

# Placeholder shown in place of each entry of a sensitive list.
# Keep this string in sync with _REDACTED_MARKER in mdbook_content.py.
REDACTED_MARKER = "🚫 Confidential — contents redacted"
REDACTED_ENTRY: TalonMap = ("—", REDACTED_MARKER)


def get_talon_settings() -> list[tuple[str, str]]:
    """
    [(setting_name, setting_value), ...]
    """
    settings: list[tuple[str, str]] = list()
    registry_settings = registry.settings
    for setting_name in sorted(registry_settings):
        setting_val: list[str] | Any = registry_settings[setting_name]
        # Saving to str simplifies later processing, but is it worth it?
        settings.append((setting_name, json.dumps(setting_val, indent=4)))
    return settings


def get_talon_tags() -> list[str]:
    """
    Sorted list of registered tag names.
    """

    return sorted(registry.tags)


def get_talon_lists() -> list[tuple[str, list[TalonMap]]]:
    """
    [(list_name, [(spoken form, command), ...]), ...

    user.snippet
    user.prose_snippets
    user.cursorless_insert_snippet_action
    user.cursorless_wrapper_snippet
    user.cursorless_insertion_snippet_no_phrase
    user.cursorless_insertion_snippet_single_phrase
    user.snippet_with_phrase
    user.snippet_wrapper

    """
    reg_talonmaps: list[tuple[str, list[TalonMap]]] = list()
    registry_lists = registry.lists
    for reg_list_name in sorted(registry_lists):
        # Skip: Too much spam
        if "cursorless" in reg_list_name:
            continue

        # For formatters, you can trigger them from python to get an example using:
        # eg. key='all cap'
        # actions.user.formatted_text(f"example of formatting with {key}", key)

        list_name = reg_list_name.lower()

        # Sensitive lists: keep the file/summary entry, but redact contents.
        if reg_list_name in SENSITIVE_LISTS:
            reg_talonmaps.append((list_name, [REDACTED_ENTRY]))
            continue

        reg_list_val: list[dict[str, str]] = [dict(l) for l in registry_lists[reg_list_name]]

        if not reg_list_val:
            # empty list
            continue

        d: dict[str, str] = reg_list_val[0]
        for other_d in reg_list_val[1:]:
            d.update(other_d)

        if not d:
            # empty dict
            continue

        list_talonmap = [(voice_cmd.lower(), d[voice_cmd].strip()) for voice_cmd in sorted(d) if not voice_cmd.startswith("#")]
        reg_talonmaps.append((list_name, list_talonmap))

    return reg_talonmaps


def get_talon_captures() -> list[tuple[str, list[TalonMap]]]:
    """
    [(capture_name, [("rule", rule_string), ("description", desc), ...]), ...]

    Captures are referenced from commands as `<user.capture_name>`. Each entry
    holds the capture's grammar rule (from `registry.captures[name][-1].rule.rule`;
    `[-1]` is the active override) and, when available, its docstring (from
    `registry.decls.captures[name].desc`).
    """
    captures: list[tuple[str, list[TalonMap]]] = list()
    decls = registry.decls.captures
    for capture_name in sorted(registry.captures):
        # Skip cursorless captures (too much spam, same as lists).
        if "cursorless" in capture_name:
            continue

        entries: list[TalonMap] = list()
        try:
            rule = registry.captures[capture_name][-1].rule.rule
        except Exception:
            rule = ""
        entries.append(("rule", rule.strip()))

        try:
            desc = decls[capture_name].desc
        except Exception:
            desc = None
        if desc:
            desc = re.sub(r"\s+", " ", desc.strip())
            entries.append(("description", desc))

        captures.append((capture_name.lower(), entries))

    return captures


def get_talon_contexts(
    ignored_paths: Iterable[str] = tuple(),
) -> list[tuple[str, list[TalonMap]]]:
    """
    Sorted
    Returns [(context_name, [(spoken form, command), ...]), ...]
    """
    context_map: list[tuple[str, list[TalonMap]]] = list()
    to_ignore = set(s.lower() for s in ignored_paths)

    for context_key in sorted(registry.contexts):
        commands = registry.contexts[context_key].commands
        ctx_name = context_key.lower()

        if not commands or any(ctx_name.lower().startswith(c) for c in to_ignore):
            continue

        # Remove commented and empty lines
        ctx_cmds = []
        remove_empty_lines = True
        remove_line_comments = True
        for key in sorted(commands):
            spoken = commands[key].rule.rule.lower()

            backend = commands[key].script.code.strip()
            backend = backend.replace(r"\n", r"\\n")
            backend_lines = backend.split("\n")
            backend_lines = [l.strip() for l in backend_lines]
            if remove_empty_lines:
                backend_lines = [l for l in backend_lines if l]
            if remove_line_comments and len(backend_lines) > 1:
                backend_lines = [l for l in backend_lines if not l.startswith("#")]

            ctx_cmds.append((spoken, "\n".join(backend_lines)))
        context_map.append((ctx_name, ctx_cmds))

    return context_map
