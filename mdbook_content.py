from talon import Module, actions, registry
from typing import Any, NamedTuple, Iterable
import pathlib
import logging
import json
import shutil

from enum import StrEnum

TalonMap = tuple[str, str]

# `out_dir_map` / `title_map` are keyed by these namespaced prefixes so that
# entities of different types can share a name without colliding (e.g. a list
# and a capture both called `user.letter`).
_KEY_LIST = "list:"
_KEY_CAP = "cap:"
_KEY_SET = "set:"
_KEY_CTX = "ctx:"

# Marker string placed in the backend of redacted list entries. Also defined
# in talon_getters.py — keep them in sync. (A cross-module import was causing
# a load error under Talon's module loader, so we duplicate the string.)
_REDACTED_MARKER = "🚫 Confidential — contents redacted"

# Tree
#
# ./book.toml
# ./src
# ├── SUMMARY.md
# ├── tags.md
# ├── settings
# │   ├── user.md
# │   ├── speech.md
# │   └── ...
# ├── lists
# │   ├── user.list1.md
# │   └── user.list2.md
# └── contexts
#     └── user
#         ├── community
#         │   ├── ...
#         │   └── ...
#         └── user.context2.md
#             ├── ...
#             └── ...
# ./book/ (generated)


class mdDirNames(StrEnum):
    contexts = "contexts"
    lists = "lists"
    settings = "settings"
    captures = "captures"


class mdFileNames(StrEnum):
    tags = "tags.md"
    summary = "SUMMARY.md"


def escape_md(c: str) -> str:
    """
    Escape characters that are special in GFM tables.

    Only used for text NOT inside a code span (GFM treats `|` and `<` inside
    `` `...` `` as literal). `|` would split the table cell, and `<` would let
    captures such as `<number>` or `<phrase>` parse as raw HTML tags and vanish
    from the rendered page. `+` is not special here, so escaping it just leaks a
    visible backslash. Backticks themselves are left alone because callers wrap
    code in double backticks.
    """
    return c.replace("|", r"\|").replace("<", r"\<")


def format_cmds_md(cmds: list[TalonMap]) -> str:
    """
    Escape talon-syntax commands for markdown
    """

    def fmt1(c: str) -> str:
        return f"*{escape_md(c)}*"

    def fmt2(c: str) -> str:
        # Double backticks form a code span, which already protects `|` and
        # other punctuation in GFM tables. Escaping here would leak literal
        # backslashes (e.g. ``\|``), so we do NOT call escape_md.
        c_lines = [l for l in c.split("\n") if l]
        return r"``" + r"``<br>``".join(c_lines) + r"``"

    return "Spoken | Backend\n-- | --\n" + "\n".join(f"{fmt1(t)} | {fmt2(c)}" for t, c in cmds)


def plantuml_mindmap(cmds: list[TalonMap]) -> str:
    """
    Returns the text for a .puml file that represents a mindmap of the commands
    """

    def cmds2map(cmds: list[TalonMap]) -> dict[str, str]:
        """
        Take a list of commands (spoken, cmd) and build a tree structure by splitting the spoken phrase
        """
        cmd_map = dict()

        def tokenize(s: str) -> list[str]:
            for c1, c2 in [
                ("(", ")"),
                ("[", "]"),
                ("{", "}"),
                ("<", ">"),
            ]:
                if c1 in s and c2 in s:
                    idx1 = s.index(c1)
                    idx2 = len(s) - s[::-1].index(c2)

                    return tokenize(s[:idx1]) + [s[idx1:idx2].strip()] + tokenize(s[idx2:])

            return [sub_s.strip() for sub_s in s.split()]

        # Build tree
        for spoken, backend in cmds:
            d_iter = cmd_map
            for tok in tokenize(spoken):
                d_iter = d_iter.setdefault(tok, dict())

        # Set values
        for spoken, backend in cmds:
            d_iter = cmd_map
            tok_path = tokenize(spoken)
            for tok in tok_path[:-1]:
                d_iter = d_iter[tok]

            # Last component
            leaf = d_iter.get(tok_path[-1])
            if isinstance(leaf, dict):
                if not leaf:
                    # on first visit
                    d_iter[tok_path[-1]] = backend
                else:
                    # This command could end here or have children
                    d_iter[tok_path[-1]]["<U+2192>"] = backend

        # Collapse children with only one child
        def flatten_single_child_nodes(tree) -> dict[str, Any]:
            def helper(node) -> dict[str, Any]:
                if not isinstance(node, dict):
                    return node

                keys = list(node.keys())

                while len(keys) == 1:
                    single_child_key = keys[0]
                    if not isinstance(node[single_child_key], dict):
                        return {single_child_key: node[single_child_key]}
                    node = {f"{keys[0]} {k}": v for k, v in node[single_child_key].items()}
                    keys = list(node.keys())

                new_node = dict()
                for key in keys:
                    if isinstance(node[key], dict):
                        new_node[key] = helper(node[key])
                    else:
                        new_node[key] = node[key]
                return new_node

            result = dict()
            for key in tree:
                flattened = helper(tree[key])
                if isinstance(flattened, dict):
                    result[key] = flattened
                else:
                    result.update({key: flattened})

            return result

        flat_cmd_map = flatten_single_child_nodes(cmd_map)
        # TODO not flattening user.community.apps.git.git.talon

        return flat_cmd_map

    d = cmds2map(cmds)  # From list to dict

    def dict_to_indented_string(tree, depth: int = 0) -> str:
        def helper(node, depth: int):
            result = ""
            for key, value in node.items():
                result += "+" * (depth + 1) + " " + str(key) + "\n"
                if isinstance(value, dict):
                    result += helper(value, depth + 1)
                else:
                    value = value.replace("\n", r"\n")
                    result += "+" * (depth + 2) + "_ " + str(value) + "\n"

            return result

        return helper(tree, depth)

    return f"@startmindmap\n{dict_to_indented_string(d)}@endmindmap\n"


# Display name used for the group that holds entries without a namespace
# prefix (i.e. no "." in the name).
_NO_PREFIX = "general"

# Backwards-compat alias for the settings-specific name used above.
_SETTINGS_NO_PREFIX = _NO_PREFIX


def group_by_prefix(
    entries: list[tuple[str, list[TalonMap]]],
) -> list[tuple[str, list[tuple[str, list[TalonMap]]]]]:
    """
    Group `[(name, items), ...]` by the prefix before the first dot of `name`.

    Returns [(prefix, [(name, items), ...]), ...] sorted by prefix. Entries
    with no dot go into the "general" group. This is the same grouping
    strategy used for settings, applied to lists and captures so their (long)
    navigation sections collapse into per-prefix sub-trees.
    """
    groups: dict[str, list[tuple[str, list[TalonMap]]]] = {}
    for name, items in entries:
        prefix = name.split(".", 1)[0] if "." in name else _NO_PREFIX
        groups.setdefault(prefix, []).append((name, items))
    return [(prefix, groups[prefix]) for prefix in sorted(groups)]


def group_settings(
    t_settings: list[tuple[str, str]],
) -> list[tuple[str, list[TalonMap]]]:
    """
    Group settings by the prefix before the first dot.

    Returns [(prefix, [(setting_name, value), ...]), ...] sorted by prefix.
    Settings with no dot go into the "general" group. Setting values are
    JSON dumps of the underlying objects (see get_talon_settings).
    """
    groups: dict[str, list[TalonMap]] = {}
    for name, value in t_settings:
        prefix = name.split(".", 1)[0] if "." in name else _SETTINGS_NO_PREFIX
        groups.setdefault(prefix, []).append((name, value))
    return [(prefix, groups[prefix]) for prefix in sorted(groups)]


def write_settings_group_md(
    parent: pathlib.Path,
    cmds: list[TalonMap],
    prefix: str,
) -> None:
    """
    Write a single settings group page (one per prefix).
    """
    text = f"# Settings: `{prefix}`\n\n"
    if prefix == _SETTINGS_NO_PREFIX:
        text += "> Settings without a namespace prefix\n\n"
    else:
        text += f"> `{prefix}.*`\n\n"
    text += format_cmds_md(cmds)
    with open(parent / f"{prefix}.md", "w") as fp:
        fp.write(text)


def write_group_heading_md(
    parent: pathlib.Path,
    section: str,
    prefix: str,
    members: list[tuple[str, pathlib.Path, str]],
) -> None:
    """
    Write a placeholder/heading page for a grouped navigation section
    (lists or captures). `members` is [(name, rel_path_from_src, title)].
    """
    text = f"# {section}: `{prefix}`\n\n"
    if prefix == _NO_PREFIX:
        text += f"> {section} without a namespace prefix\n\n"
    else:
        text += f"> `{prefix}.*`\n\n"
    if members:
        text += "## Sub-pages\n\n"
        for name, rel, _title in sorted(members):
            text += f"- [{name}]({rel.as_posix()})\n"
    with open(parent / f"{prefix}.md", "w") as fp:
        fp.write(text)


# Tree node for the context directory hierarchy.
#   name      : directory name (e.g. "community"; "" for the root)
#   dir_path  : absolute path of this node's directory
#   children  : {name: node}
#   leaves    : [(title, rel_path_from_src), ...] for context .md files in this dir
_CtxNode = dict


def build_context_tree(
    path: pathlib.Path,
    ctx_map: list[tuple[str, list[TalonMap]]],
    out_dir_map: dict[str, pathlib.Path],
    title_map: dict[str, str],
) -> _CtxNode:
    """
    Build a nested tree mirroring the `contexts/user/<module>/...` directory
    structure. The root is the `contexts/user` directory (the common `user`
    segment is not shown in the summary), so top-level children are the
    modules: community, rango, talon_hud, my-overrides, etc.
    """
    root_dir = path / mdDirNames.contexts / "user"
    root: _CtxNode = {"name": "", "dir_path": root_dir, "children": {}, "leaves": []}

    for ctx_name, _ in ctx_map:
        key = f"{_KEY_CTX}{ctx_name}"
        d = out_dir_map[key]
        rel = d.relative_to(root_dir)
        parts = rel.parts if rel.parts and rel.parts != (".",) else ()
        node = root
        for p in parts:
            node = node["children"].setdefault(
                p,
                {"name": p, "dir_path": node["dir_path"] / p, "children": {}, "leaves": []},
            )
        leaf_rel = (d / f"{ctx_name}.md").relative_to(path)
        node["leaves"].append((title_map[key], leaf_rel))

    return root


def _node_link(node: _CtxNode, path: pathlib.Path, submodule_urls: dict[str, str], is_top_level: bool) -> str:
    """
    Markdown link target for a node: an external repo URL for top-level git
    submodules, otherwise a placeholder page inside the node's directory.
    """
    if is_top_level and node["name"] in submodule_urls:
        return submodule_urls[node["name"]]
    return (node["dir_path"] / f"{node['name']}.md").relative_to(path).as_posix()


def render_context_tree_lines(
    node: _CtxNode,
    path: pathlib.Path,
    submodule_urls: dict[str, str],
    indent: str = "",
    is_top_level: bool = True,
) -> list[str]:
    """
    Render a node and its descendants as nested mdbook SUMMARY.md list lines.
    """
    lines: list[str] = []
    if node["name"]:
        link = _node_link(node, path, submodule_urls, is_top_level)
        lines.append(f"{indent}- [{node['name']}]({link})")
        child_indent = indent + "    "
    else:
        # Root is not rendered; its children sit at the top indent level.
        child_indent = indent

    for child_name in sorted(node["children"]):
        lines.extend(
            render_context_tree_lines(
                node["children"][child_name],
                path,
                submodule_urls,
                child_indent,
                is_top_level=(not node["name"]),  # top-level only for root's children
            )
        )
    for title, leaf_rel in sorted(node["leaves"]):
        lines.append(f"{child_indent}- [{title}]({leaf_rel.as_posix()})")
    return lines


def write_context_placeholders(
    node: _CtxNode,
    path: pathlib.Path,
    submodule_urls: dict[str, str],
    is_top_level: bool = True,
) -> None:
    """
    Write a placeholder .md page for every directory node that is not a
    top-level git submodule (those link externally and need no page).
    """
    name = node["name"]
    if name and not (is_top_level and name in submodule_urls):
        fp = node["dir_path"] / f"{name}.md"
        text = f"# {name}\n\n> Context directory\n\n"
        child_names = sorted(node["children"])
        leaves = sorted(node["leaves"])
        if child_names or leaves:
            text += "## Sub-pages\n\n"
            for c in child_names:
                child = node["children"][c]
                if c in submodule_urls and not node["name"]:
                    text += f"- [{c}]({submodule_urls[c]}) (repository)\n"
                else:
                    rel = (child["dir_path"] / f"{c}.md").relative_to(path).as_posix()
                    text += f"- [{c}]({rel})\n"
            for title, leaf_rel in leaves:
                text += f"- [{title}]({leaf_rel.as_posix()})\n"
        fp.write_text(text)

    for c in sorted(node["children"]):
        write_context_placeholders(node["children"][c], path, submodule_urls, is_top_level=(not node["name"]))


def _render_prefixed_section(
    path: pathlib.Path,
    section_title: str,
    groups: list[tuple[str, list[tuple[str, list[TalonMap]]]]],
    key_prefix: str,
    out_dir_map: dict[str, pathlib.Path],
    title_map: dict[str, str],
) -> str:
    """
    Render a Lists/Captures section as nested per-prefix sub-trees:

        - [prefix](<prefix dir>/<prefix>.md)
            - [name](<prefix dir>/<name>.md)
            - ...

    The group heading page (`<prefix>.md`) makes the collapsible parent
    link somewhere instead of 404ing.
    """
    lines: list[str] = [section_title, ""]
    for prefix, members in groups:
        group_dir = out_dir_map[f"{key_prefix}grp:{prefix}"]
        heading_rel = (group_dir / f"{prefix}.md").relative_to(path)
        lines.append(f"- [{prefix}]({heading_rel})")
        for name, _ in members:
            key = f"{key_prefix}{name}"
            rel_p = (out_dir_map[key] / f"{name}.md").relative_to(path)
            n = title_map[key]
            lines.append(f"    - [{n}]({rel_p})")
    return "\n".join(lines) + "\n"


def write_summary_md(
    path: pathlib.Path,
    reglist_map: list[tuple[str, list[TalonMap]]],
    title_map: dict[str, str],
    ctx_map: list[tuple[str, list[TalonMap]]],
    out_dir_map: dict[str, pathlib.Path],
    settings_groups: list[tuple[str, list[TalonMap]]] = (),
    ctx_tree: _CtxNode | None = None,
    submodule_urls: dict[str, str] | None = None,
    captures: list[tuple[str, list[TalonMap]]] = (),
) -> None:
    """
    points to all other files!
    """
    submodule_urls = submodule_urls or {}
    with open(path / mdFileNames.summary, "w") as fp:
        summary_txt = "# Summary\n\n"

        # Settings (one entry per prefix group)
        summary_txt += "# Talon Settings\n"
        for prefix, _ in settings_groups:
            rel_p = (out_dir_map[f"{_KEY_SET}{prefix}"] / f"{prefix}.md").relative_to(path)
            summary_txt += f"- [{prefix}]({rel_p})\n"
        summary_txt += "---\n"

        # Tags
        summary_txt += "# Talon Tags\n"
        summary_txt += f"- [Tags]({mdFileNames.tags})\n\n"
        summary_txt += "---\n"

        # Lists (grouped by prefix before the first dot)
        summary_txt += _render_prefixed_section(
            path=path,
            section_title="# Talon Lists",
            groups=group_by_prefix(reglist_map),
            key_prefix=_KEY_LIST,
            out_dir_map=out_dir_map,
            title_map=title_map,
        )
        summary_txt += "---\n"

        # Captures (grouped by prefix before the first dot)
        summary_txt += _render_prefixed_section(
            path=path,
            section_title="# Talon Captures",
            groups=group_by_prefix(captures),
            key_prefix=_KEY_CAP,
            out_dir_map=out_dir_map,
            title_map=title_map,
        )
        summary_txt += "---\n"

        # Contexts (nested tree, collapsible in mdbook)
        summary_txt += "# Contexts\n"
        if ctx_tree is not None:
            summary_txt += "\n".join(
                render_context_tree_lines(ctx_tree, path, submodule_urls)
            )
        else:
            # Fallback: flat list (should not happen in normal use)
            for ctx_name, _ in ctx_map:
                key = f"{_KEY_CTX}{ctx_name}"
                n = title_map[key]
                rel_p = (out_dir_map[key] / f"{ctx_name}.md").relative_to(path)
                summary_txt += f"- [{n}]({rel_p})\n"
        summary_txt += "\n"

        fp.write(summary_txt + "\n")


def write_tags_md(
    parent: pathlib.Path,
    t_tags: list[tuple[str, str]],
    filename: str = mdFileNames.tags,
) -> None:
    text = "# Talon Tags\n\n"
    text += "\n- ".join(["In alphabetical order:"] + t_tags)
    with open(parent / filename, "w") as fp:
        fp.write(text)


def write_list_md(
    parent: pathlib.Path,
    cmds: list[tuple[str, list[TalonMap]]],
    name: str,
    title_name: str,
) -> None:
    text = f"# List: `{{{title_name}}}`\n\n"
    text += f"> `{{{name}}}`\n\n"
    if len(cmds) == 1 and cmds[0][1] == _REDACTED_MARKER:
        text += (
            "> ⚠️ **Confidential** — this list contains personal/sensitive "
            "information, so its contents have been redacted.\n\n"
        )
    text += format_cmds_md(cmds)
    with open(parent / f"{name}.md", "w") as fp:
        fp.write(text)


def write_capture_md(
    parent: pathlib.Path,
    cmds: list[TalonMap],
    name: str,
    title_name: str,
) -> None:
    """
    Write a single capture page. `cmds` is [("rule", rule), ("description", desc)].
    """
    text = f"# Capture: `<{title_name}>`\n\n"
    text += f"> `<{name}>`\n\n"
    text += format_cmds_md(cmds)
    with open(parent / f"{name}.md", "w") as fp:
        fp.write(text)


def write_ctx_md(
    parent: pathlib.Path,
    cmds: list[tuple[str, list[TalonMap]]],
    name: str,
    title_name: str = "",
) -> None:
    text = f"# {title_name}\n\n"
    text += f"> `<{name}>`\n\n"
    text += format_cmds_md(cmds)

    # Mind map SVG (rendered from puml)
    text += "\n\n# Mindmap\n\n"
    text += f"\n\n[![](./{name}.svg)](./{name}.svg)\n\n"

    with open(parent / f"{name}.md", "w") as fp:
        fp.write(text)
    with open(parent / f"{name}.puml", "w") as fp_uml:
        fp_uml.write(plantuml_mindmap(cmds))


def _cleanup_generated(path: pathlib.Path) -> None:
    """
    Remove previously generated content so renamed/removed contexts and lists
    don't leave orphan files behind. Only touches paths we generate.
    """
    for sub in (mdDirNames.contexts, mdDirNames.lists, mdDirNames.settings, mdDirNames.captures):
        d = path / sub
        if d.exists():
            shutil.rmtree(d)
    for fn in (mdFileNames.tags, mdFileNames.summary):
        f = path / fn
        if f.exists():
            f.unlink()


def write_md_tree(
    path: pathlib.Path,
    reglist_map: list[tuple[str, list[TalonMap]]],
    ctx_map: list[tuple[str, list[TalonMap]]],
    t_settings: list[tuple[str, str]],
    t_tags: list[str],
    cleanup: bool = True,
    submodule_urls: dict[str, str] | None = None,
    captures: list[tuple[str, list[TalonMap]]] = (),
) -> None:
    """

    TODO: link {user.list_name} to list page
    TODO: link <user.capture_name> in command backends to the captures page
    """
    submodule_urls = submodule_urls or {}
    if path.is_file():
        raise ValueError(f"Path {path} is not a directory")

    if cleanup:
        _cleanup_generated(path)

    # Group settings by their prefix before the first dot.
    settings_groups = group_settings(t_settings)

    # Build tree (dicionary with all parent directory paths). Keys are
    # namespaced by entity type (see _KEY_* constants) so a list and a
    # capture sharing a name don't clobber each other.
    #
    # Lists and captures are grouped by the prefix before the first dot
    # (e.g. `user.letter` -> prefix `user`), so each group lives in its own
    # subdirectory and becomes a collapsible entry in the navigation instead
    # of one giant flat list.
    list_groups = group_by_prefix(reglist_map)
    capture_groups = group_by_prefix(captures)

    out_dir_map = dict()
    title_map = dict()
    for prefix, _ in settings_groups:
        title_map[f"{_KEY_SET}{prefix}"] = prefix
        out_dir_map[f"{_KEY_SET}{prefix}"] = path / mdDirNames.settings
    for prefix, members in list_groups:
        group_dir = path / mdDirNames.lists / prefix
        out_dir_map[f"{_KEY_LIST}grp:{prefix}"] = group_dir
        for reg_name, _ in members:
            title_map[f"{_KEY_LIST}{reg_name}"] = reg_name
            out_dir_map[f"{_KEY_LIST}{reg_name}"] = group_dir
    for prefix, members in capture_groups:
        group_dir = path / mdDirNames.captures / prefix
        out_dir_map[f"{_KEY_CAP}grp:{prefix}"] = group_dir
        for cap_name, _ in members:
            title_map[f"{_KEY_CAP}{cap_name}"] = cap_name
            out_dir_map[f"{_KEY_CAP}{cap_name}"] = group_dir

    # TODO Build summary structure into directory tree
    # print summary from that tree instead of along the way

    max_depth: int = 3
    for ctx_name, _ in ctx_map:
        ctx_parts = (
            ctx_name.removeprefix(".user")
            # .removesuffix(".talon")
            # .removesuffix(".script")
            .split(".")
        )
        ctx_parts = ctx_parts[:-1]  # remove the file extension
        title_map[f"{_KEY_CTX}{ctx_name}"] = ctx_parts[-1]
        ctx_parts = ctx_parts[:-1]  # remove the filename
        ctx_parts = ctx_parts[:max_depth]  # avoid nesting too deeply
        out_dir_map[f"{_KEY_CTX}{ctx_name}"] = path / mdDirNames.contexts / "/".join(ctx_parts)

    # Create directories
    for d in set(out_dir_map.values()):
        d.mkdir(parents=True, exist_ok=True)

    # Build the nested context tree from the directory structure so the
    # navigation can be rendered as a collapsible hierarchy.
    ctx_tree = build_context_tree(path, ctx_map, out_dir_map, title_map)

    write_summary_md(
        path=path,
        reglist_map=reglist_map,
        title_map=title_map,
        ctx_map=ctx_map,
        out_dir_map=out_dir_map,
        settings_groups=settings_groups,
        ctx_tree=ctx_tree,
        submodule_urls=submodule_urls,
        captures=captures,
    )

    # Placeholder pages for context directory headings (so the collapsible
    # part-headers in the navigation link somewhere instead of 404ing). Top-
    # level git submodules link to their repository and need no page.
    write_context_placeholders(ctx_tree, path, submodule_urls)

    # Settings (one page per prefix group)
    for prefix, group_cmds in settings_groups:
        write_settings_group_md(
            parent=out_dir_map[f"{_KEY_SET}{prefix}"],
            cmds=group_cmds,
            prefix=prefix,
        )

    write_tags_md(path, t_tags)

    # Lists: one heading page per prefix group, then the member pages.
    for prefix, members in list_groups:
        group_dir = out_dir_map[f"{_KEY_LIST}grp:{prefix}"]
        heading_members = [
            (reg_name, (out_dir_map[f"{_KEY_LIST}{reg_name}"] / f"{reg_name}.md").relative_to(path), reg_name)
            for reg_name, _ in members
        ]
        write_group_heading_md(group_dir, "Lists", prefix, heading_members)
    for reg_name, reg_cmds in reglist_map:
        key = f"{_KEY_LIST}{reg_name}"
        write_list_md(
            parent=out_dir_map[key],
            name=reg_name,
            cmds=reg_cmds,
            title_name=title_map[key],
        )

    # Captures: one heading page per prefix group, then the member pages.
    for prefix, members in capture_groups:
        group_dir = out_dir_map[f"{_KEY_CAP}grp:{prefix}"]
        heading_members = [
            (cap_name, (out_dir_map[f"{_KEY_CAP}{cap_name}"] / f"{cap_name}.md").relative_to(path), cap_name)
            for cap_name, _ in members
        ]
        write_group_heading_md(group_dir, "Captures", prefix, heading_members)
    for cap_name, cap_cmds in captures:
        key = f"{_KEY_CAP}{cap_name}"
        write_capture_md(
            parent=out_dir_map[key],
            name=cap_name,
            cmds=cap_cmds,
            title_name=title_map[key],
        )

    # Contexts
    for ctx_name, ctx_cmds in ctx_map:
        key = f"{_KEY_CTX}{ctx_name}"
        write_ctx_md(
            parent=out_dir_map[key],
            name=ctx_name,
            cmds=ctx_cmds,
            title_name=title_map[key],
        )

    # Skipped!
    # - [Community]()
    # - [1password.]()
    #     - [1password](contexts/user/community/apps/user.community.apps.1password.1password.talon.md)

    # # Skipped
    # written_ctx_names = {c for c, _ in ctx_map}
    # skipped_ctx_names = {
    #     c for c in registry.contexts if c.lower() not in written_ctx_names
    # }
    # logging.info(f"\n\n# Empty or skip.eped contexts:\n")
    # for context_name in sorted(skipped_ctx_names):
    #     logging.info(f"\n- **{context_name}**")
