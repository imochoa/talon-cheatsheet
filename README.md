```

<!-- - [Chapter 1](./chapter_1.md) -->
plantuml -tsvg *
mdbook build ./cheatsheets
nix shell github:nix-community/talon-nix

rsync -avczP ./cheatsheets/book/ hetzner1:/home/mumad/docker/nginx-web-servers/mounts/html-talon/
```
