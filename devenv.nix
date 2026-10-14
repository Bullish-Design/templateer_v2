# Templateer devenv. Testee checks each commit in its own workspace and keeps one verdict per
# check. The machine supplies the Testee module and the `testee` command. `nix/testee.nix`
# holds the checks. `testee.prepare` fills the devenv venv in that workspace, so the
# absolute tool paths resolve. The check tools (pytest, ruff, ty) come from the `dev` extra.
{ pkgs, lib, config, inputs, ... }:

{
  imports = [ ./nix/testee.nix ];

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
