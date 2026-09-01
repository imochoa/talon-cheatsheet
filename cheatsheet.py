from talon import Module, actions, registry
from typing import Any, NamedTuple, Iterable
import pathlib
import logging
import json
import shutil

import subprocess

from enum import StrEnum

from .talon_getters import (
    get_talon_settings,
    get_talon_tags,
    get_talon_lists,
    get_talon_captures,
    get_talon_contexts,
    get_submodule_urls,
)

from .mdbook_content import write_md_tree

BOOK_DIR = (pathlib.Path(__file__).absolute().parent / "cheatsheets").resolve()


def _run_mdbook(*args: str) -> None:
    """
    Run `mdbook <args>` on the cheatsheets book directory.

    Tries the `mdbook` on PATH first, then falls back to
    `nix run nixpkgs#mdbook` so this works without a nix shell loaded.
    """
    for cmd in (
        ["mdbook", *args],
        ["nix", "run", "nixpkgs#mdbook", "--", *args],
    ):
        try:
            subprocess.run(
                cmd,
                cwd=BOOK_DIR,
                check=True,
                capture_output=True,
                text=True,
            )
            return
        except FileNotFoundError:
            # This command isn't available — try the next one.
            continue
        except subprocess.CalledProcessError as e:
            print(f"mdbook {' '.join(args)} failed: {e.stderr.strip()}")
            raise
    raise FileNotFoundError("mdbook not found on PATH and nix unavailable")


mod = Module()


@mod.action_class
class user_actions:
    def cheatsheet():
        """Print out a sheet of talon commands"""
        print("Generating Talon cheatsheet...")
        actions.menu.open_log()
        this_dir = pathlib.Path(__file__).absolute().parent
        # TODO: add parrot noises
        # TODO: add snippets
        # TODO: Censor sensitive info

        submodule_urls = get_submodule_urls(this_dir.parent / ".gitmodules")

        write_md_tree(
            path=this_dir / "cheatsheets" / "src",
            reglist_map=get_talon_lists(),
            ctx_map=get_talon_contexts(),
            t_settings=get_talon_settings(),
            t_tags=get_talon_tags(),
            submodule_urls=submodule_urls,
            captures=get_talon_captures(),
        )

        # Rebuild the mdbook so the table of contents / navigation reflects
        # the freshly generated `src/`. `clean` removes stale HTML for
        # contexts/lists that no longer exist; `build` regenerates everything.
        try:
            print("Rebuilding mdbook...")
            _run_mdbook("clean")
            _run_mdbook("build")
        except Exception as e:  # noqa: BLE001
            print(f"WARNING: mdbook rebuild skipped: {e}")
            print("Run `just build-book` manually to update the navigation.")

        # actions.user.system_open("cheatsheet.md")
        print("Done!")
