# Testee checks for this project.
#
# The machine (nix-meta) supplies the Testee module at the `machine` path.
# On a host without that module, the checks are skipped and the shell still
# evaluates. This project owns its checks here. It never pins Testee itself.
# Use `lib.optionalAttrs` for the settings. A module-system condition fails
# when the option is absent.
{ lib, options, pkgs, config, ... }:

let
  machine = /run/current-system/sw/share/testee/module/testee.nix;
  # Checks run in a clean shell with no ambient PATH. Use absolute tool paths.
  venvBin = "${config.devenv.state}/venv/bin";
in
{
  imports = lib.optional (builtins.pathExists machine) machine;

  config = lib.optionalAttrs (options ? testee) {
    testee.prepare = [ [ "uv" "sync" "--locked" "--extra" "dev" ] ];

    # `quick` is ruff. `full` adds ty and pytest. These match the gate in AGENTS.md.
    testee.checks = {
      ruff = {
        argv = [ "${venvBin}/ruff" "check" "--output-format" "json" "src" "tests" "templates" ];
        profiles = [ "quick" "full" ];
        structured = { parser = "ruff-json"; file = "ruff.stdout.log"; };
      };
      ty = {
        argv = [ "${venvBin}/ty" "check" "--python" "${venvBin}/python3" "src" ];
        profiles = [ "full" ];
      };
      pytest = {
        argv = [
          "${pkgs.bash}/bin/bash"
          "-c"
          ''
            # The Testee shell drops the host PATH. The `needs_nix` tests run nix-instantiate.
            export PATH="$PATH:${pkgs.nix}/bin"
            ${venvBin}/python -m pytest -q --junitxml="$TESTEE_RUN_DIR/pytest.junit.xml"
          ''
        ];
        profiles = [ "full" ];
        timeout_s = 900;
        structured = { parser = "junit-xml"; file = "pytest.junit.xml"; };
      };
    };
  };
}
