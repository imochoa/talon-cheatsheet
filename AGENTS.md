# AGENTS.md - Talon Cheatsheet Generator

A Talon Voice action that introspects the live Talon registry and generates a
browsable [mdBook](https://rust-lang.github.io/mdBook/) reference of all
commands, lists, captures, settings, tags, and contexts in this user config.

## What it produces

Saying **`print cheatsheet`** (defined in `cheatsheet.talon` → `cheatsheet.py`)
regenerates `cheatsheets/src/` from the live registry and rebuilds the mdBook
into `cheatsheets/book/`. Output tree:

```
cheatsheets/
├── book.toml                 # mdBook config (fold enabled, collapse-nav.js)
├── src/
│   ├── SUMMARY.md            # generated table of contents
│   ├── tags.md
│   ├── settings/<prefix>.md  # one page per settings prefix (user, speech, ...)
│   ├── lists/<prefix>/…      # lists grouped by prefix; <prefix>.md is a heading page
│   ├── captures/<prefix>/…   # captures grouped by prefix; <prefix>.md is a heading page
│   ├── contexts/user/<module>/…  # nested per .talon file; placeholder pages per dir
│   └── collapse-nav.js       # collapses the sidebar by default (see below)
└── book/                     # built HTML site (generated)
```

## Files

| File | Purpose |
|------|---------|
| `cheatsheet.talon` | Voice command: `print cheatsheet: user.cheatsheet()` |
| `cheatsheet.py` | The action. Fetches registry data, calls `write_md_tree`, then runs `mdbook clean` + `mdbook build` (falls back to `nix run nixpkgs#mdbook` if `mdbook` isn't on PATH). |
| `talon_getters.py` | Reads the live Talon `registry`: settings, tags, lists, captures, contexts, and submodule URLs from `../.gitmodules`. |
| `mdbook_content.py` | Renders the markdown tree and `SUMMARY.md`; writes per-entity pages and plantuml mindmaps. |
| `justfile` | `build-book`, `watch-book`, `render-plantuml`, `copy-html-to-server`. |
| `shell.nix` | Provides `mdbook` + `plantuml` via `nix shell` / direnv. |

## Architecture & key invariants

### Entity grouping (settings, lists, captures)

Settings, lists, and captures are all **grouped by the prefix before the first
dot** (`group_by_prefix` / `group_settings` in `mdbook_content.py`). Names
without a dot go into a `general` group. Each group gets:
- its own subdirectory (`settings/<prefix>.md`, `lists/<prefix>/`, `captures/<prefix>/`),
- a heading/placeholder page (`<prefix>.md`) so the collapsible nav parent links somewhere,
- a nested, indented entry in `SUMMARY.md`.

### Namespace collision handling

A list and a capture (or any two entity types) can share a name, e.g.
`user.letter` exists as both a list and a capture. `out_dir_map` / `title_map`
are keyed by **namespaced keys** to prevent clobbering:

```python
_KEY_LIST = "list:"
_KEY_CAP  = "cap:"
_KEY_SET  = "set:"
_KEY_CTX  = "ctx:"
```

Always use `f"{_KEY_LIST}{name}"` etc. when reading/writing these maps. A bare
name key will collide and produce duplicate `SUMMARY.md` paths, which mdBook
rejects with `Duplicate file in SUMMARY.md`.

### Sensitive lists

`talon_getters.py` defines `SENSITIVE_LISTS` (currently `user.running`,
`user.contact_names`, `user.contact_emails`, `user.contact_full_names`).
Sensitive lists are **kept in the TOC** but their entries are replaced with a
single redacted row (`REDACTED_ENTRY`), and `write_list_md` renders a
"⚠️ Confidential" banner. The redaction marker string is duplicated as
`_REDACTED_MARKER` in `mdbook_content.py` (kept in sync deliberately — a
cross-module import caused a load error under Talon's module loader).

### Context tree & git submodules

Contexts are nested into a tree mirroring `contexts/user/<module>/…`
(`build_context_tree`). Top-level modules that are git submodules (parsed from
`../.gitmodules` via `get_submodule_urls`) link **externally** to their repo
and get no placeholder page; other directory headings get a placeholder page
(`write_context_placeholders`) so collapsible parents don't 404.

`get_submodule_urls` strips leading whitespace per line before parsing, because
`.gitmodules` can mix tab/space indentation (which otherwise makes
`configparser` drop entries — `rango` was silently missing until this was
fixed).

### Sidebar collapse-by-default

mdBook has no native config to fold chapters by default (`[output.html.fold]`
only has `enable` + `level`; chapters always render expanded). We use a small
JS shim, `cheatsheets/src/collapse-nav.js`, registered via
`additional-js = ["src/collapse-nav.js"]` in `book.toml`.

Key implementation details (mdBook 0.5.2):
- The TOC lives in a separate **iframe** (`<iframe class="sidebar-iframe-outer">`
  loading `toc.html`), not inline in content pages. The shim runs in the
  **parent** page, waits for the iframe's `load` event, and operates on
  `iframe.contentDocument`.
- mdBook's own `toc-*.js` (a custom element, `connectedCallback`) marks the
  active page link with `a.active` and adds `expanded` to its ancestor
  chapters during iframe parse — *before* `load` fires. The shim preserves
  that active trail by walking up from `a.active` and skipping those
  ancestors when removing `expanded`.
- Fold-toggle elements use class **`toggle`** (not `chapter-fold-toggle`).
- `additional-js` paths are resolved relative to the book root (where
  `book.toml` lives), so the value is `src/collapse-nav.js`, not
  `collapse-nav.js`. mdBook copies the file to `book/src/collapse-nav-<hash>.js`.

### mdBook version

Pinned via nix to **0.5.2**. `book.toml` must not use fields removed/renamed
in this version (e.g. `[book] multilingual` is invalid; `site-url` belongs
under `[output.html]`, not `[book]`).

## Markdown escaping

`escape_md` only escapes `|` (needed in the italic spoken-form column, where
Talon alternation `|` would break the GFM table). It is **not** called for the
backend column, which is wrapped in double-backtick code spans — GFM already
treats `|` and other punctuation as literal inside code spans, so escaping
there only leaks visible backslashes (e.g. `status_bar\|4`). Do not re-add
`+` or `` ` `` escaping to `escape_md`.

Known unrelated warning: mdBook logs `unclosed HTML tag <number>/<phrase>`
for some command backends that contain literal `<…>` in tables. These are
cosmetic and do not fail the build.

## Development workflow

- **Hot reload**: Talon reloads `.py`/`.talon` on save. `print cheatsheet`
  regenerates `src/` **and** rebuilds the book, so no manual step is needed
  after changes.
- **Manual rebuild** (without regenerating content): `just build-book`
  (runs `mdbook clean` + `mdbook build ./cheatsheets`).
- **Live preview**: `just watch-book` (`mdbook serve`).
- **PlantUML mindmaps**: `just render-plantuml` renders `**/*.puml` → SVG.
  Context pages embed `![mindmap](./<name>.svg)`.
- **Publish**: `just copy-html-to-server` (builds, renders plantuml, rsyncs
  `book/` to `herbert`).

### Testing changes without Talon

The `.py` files import `talon`, which only exists inside Talon. To test
`mdbook_content.py` logic standalone, stub `talon` and
`cheatsheet.talon_getters` with `types.ModuleType`, then
`importlib.util.spec_from_file_location` the module. Several examples of this
pattern are in the session history; the key stubs are:

```python
talon_stub = types.ModuleType("talon")
talon_stub.Module = types.SimpleNamespace(action_class=lambda c: c)
talon_stub.actions = types.SimpleNamespace()
talon_stub.registry = types.SimpleNamespace()
sys.modules["talon"] = talon_stub
# plus a fake registry with .captures (list, [-1].rule.rule),
# .decls.captures ([name].desc), .lists, .tags, .settings, .contexts
```

## TODOs (from the code)

- Add parrot noises
- Add snippets
- Link `{user.list_name}` references in command backends to list pages
- Link `<user.capture_name>` references to capture pages
