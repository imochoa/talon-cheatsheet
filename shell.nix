let
  nixpkgs = fetchTarball "https://github.com/NixOS/nixpkgs/tarball/nixos-25.05";
  pkgs = import nixpkgs { config = { }; overlays = [ ]; };
in

pkgs.mkShellNoCC {
  # pkgs.mkShell
  packages = with pkgs; [
    mdbook
    plantuml
  ];

  shellHook = ''
    hello
  '';
}

