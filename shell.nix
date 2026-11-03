let
  # nixos-26.05 ships mdbook 0.5.2, the version book.toml and collapse-nav.js target.
  nixpkgs = fetchTarball "https://github.com/NixOS/nixpkgs/tarball/nixos-26.05";
  pkgs = import nixpkgs { config = { }; overlays = [ ]; };
in

pkgs.mkShellNoCC {
  packages = with pkgs; [
    mdbook
    plantuml
  ];
}
