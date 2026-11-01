set positional-arguments := true

# -e Exit immediately if a command exits with a non-zero status.
# -u Treat unbound variables as an error when substituting.
# -c If set, commands are read from string. This option is used to provide commands that don't come from a file.
# -o pipefail If any command in a pipeline fails, that return code will be used as the return code of the whole pipeline.

set shell := ["bash", "-euco", "pipefail"]

# Open fzf picker
[no-cd]
_default:
    @just --choose

# Render every generated mindmap (.puml) under src/ to .svg next to it
render-plantuml:
  find ./cheatsheets/src -name '*.puml' -exec plantuml -tsvg {} +

build-book:
  mdbook clean ./cheatsheets
  mdbook build ./cheatsheets

watch-book:
  mdbook serve ./cheatsheets

# SVGs must exist in src/ before mdbook copies src/ into book/
copy-html-to-server: render-plantuml build-book
  rsync -avczP --delete ./cheatsheets/book/ herbert:~/vps/podman/containers/systemd/nginx/mounts/talon
