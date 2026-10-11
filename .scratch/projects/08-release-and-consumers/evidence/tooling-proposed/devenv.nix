# Templateer devenv. Testee checks each commit in its own workspace and keeps one verdict per
# check. `devenv.yaml` loads the Testee module from a locked input. The host `testee` command
# runs the checks below. `testee.prepare` fills the devenv venv in that workspace, so the
# absolute tool paths resolve. The check tools (pytest, ruff, ty) come from the `dev` extra.
{ pkgs, lib, config, inputs, ... }:

let
  # Checks run in a clean shell with no ambient PATH. Use absolute tool paths.
  venvBin = "${config.devenv.state}/venv/bin";
in
{
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
          export PATH="$PATH:/run/current-system/sw/bin:/etc/profiles/per-user/$(id -un)/bin"
          ${venvBin}/python -m pytest -q --junitxml="$TESTEE_RUN_DIR/pytest.junit.xml"
        ''
      ];
      profiles = [ "full" ];
      timeout_s = 900;
      structured = { parser = "junit-xml"; file = "pytest.junit.xml"; };
    };
  };

  # https://devenv.sh/basics/
  env.GREET = "devenv";

  # https://devenv.sh/packages/
  packages = [ 
    pkgs.git 
    pkgs.uv
    ];

  # https://devenv.sh/languages/
  # languages.rust.enable = true;
  languages = {
      python = {
          enable = true;
          version = "3.13";
          venv.enable = true;
          uv.enable = true;
        };
    };

  # https://devenv.sh/processes/
  # processes.cargo-watch.exec = "cargo-watch";

  # https://devenv.sh/services/
  # services.postgres.enable = true;

  # https://devenv.sh/scripts/
  scripts.hello.exec = ''
    echo hello from $GREET
  '';

  enterShell = ''
    hello
    git --version
  '';

  # https://devenv.sh/tasks/
  #
  # The two task names the `base` group calls (groups/base/README.md). devenv
  # owns each implementation; Dagu owns the composition (§6). `uv run` rather
  # than bare names: the venv bin is on the interactive shell's PATH but not on
  # the task runner's PATH (STAGE_7_LOG.md, wave 2b).
  tasks = {
    "templateer_v2:lint".exec = "testee verify --profile quick";
    "templateer_v2:test".exec = "testee verify";

    "base:check".after = [ "templateer_v2:lint" ];
    "base:test".after = [ "templateer_v2:test" ];
  };

  # https://devenv.sh/tests/
  enterTest = ''
    echo "Running tests"
    git --version | grep --color=auto "${pkgs.git.version}"
  '';

  # https://devenv.sh/pre-commit-hooks/
  # pre-commit.hooks.shellcheck.enable = true;

  # See full reference at https://devenv.sh/reference/options/
}
