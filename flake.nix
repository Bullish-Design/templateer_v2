{
  description = "Templateer: typed, constrained artifact generation for AI agents.";

  # The development environment is driven by `devenv.yaml`. It is not restated as a
  # `devShells` output.
  #
  # Outputs:
  #   packages.default / packages.templateer   the `templateer` command
  #   packages.templateer-lib                  the Python package, for other Python packages
  #   packages.minijinja                       the MiniJinja binding that nixpkgs lacks
  #   overlays.default                         adds `minijinja` and `templateer` to
  #                                            pythonPackagesExtensions
  #   checks.templateer                        the package build; it runs the suite and
  #                                            `templateer --help`
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/e7439b6b14ad3cc35d05608ebca9bce01a25f5f8";
  };

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
      forAllSystems = nixpkgs.lib.genAttrs systems;

      overlay = final: prev: {
        pythonPackagesExtensions = (prev.pythonPackagesExtensions or [ ]) ++ [
          (pyFinal: _pyPrev: {
            minijinja = pyFinal.callPackage ./nix/minijinja.nix { };
            templateer = final.callPackage ./nix/package.nix { python3Packages = pyFinal; };
          })
        ];
      };

      # `requires-python` is ">=3.12". Name the interpreter so a nixpkgs bump cannot move it.
      pkgsFor = system: import nixpkgs { inherit system; overlays = [ overlay ]; };
    in
    {
      overlays.default = overlay;

      packages = forAllSystems (system:
        let
          pkgs = pkgsFor system;
          python = pkgs.python313Packages;
        in
        {
          templateer-lib = python.templateer;
          templateer = python.toPythonApplication python.templateer;
          default = python.toPythonApplication python.templateer;
          minijinja = python.minijinja;
        });

      apps = forAllSystems (system: {
        default = {
          type = "app";
          program = "${self.packages.${system}.templateer}/bin/templateer";
        };
      });

      checks = forAllSystems (system: {
        templateer = self.packages.${system}.templateer-lib;
      });
    };
}
