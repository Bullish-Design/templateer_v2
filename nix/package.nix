# Templateer as a Nix-built Python package.
#
# `minijinja` has no nixpkgs package. `./minijinja.nix` builds it. The flake adds it to the
# Python set, so `python3Packages.minijinja` resolves here.
#
# `pydantic-ai-slim` loads on first use. nixpkgs has no `openai` extra on it, so `openai` is
# a direct dependency here.
#
# The build runs the suite. The install check runs the real command.
# The version comes from pyproject.toml.
{ lib
, nix
, python3Packages
}:

let
  pyproject = builtins.fromTOML (builtins.readFile ../pyproject.toml);
in
python3Packages.buildPythonPackage {
  pname = "templateer";
  version = pyproject.project.version;
  pyproject = true;

  # `tests/` and `templates/` are in the fileset because pytest runs both (`testpaths`).
  src = lib.fileset.toSource {
    root = ../.;
    fileset = lib.fileset.unions [
      ../src
      ../tests
      ../templates
      ../pyproject.toml
      ../README.md
    ];
  };

  build-system = [ python3Packages.hatchling ];

  dependencies = with python3Packages; [
    click
    minijinja
    openai
    pydantic
    pydantic-ai-slim
    pyyaml
  ];

  pythonImportsCheck = [ "templateer" ];

  # `nix` provides `nix-instantiate`. The `needs_nix` tests run with it instead of skipping.
  nativeCheckInputs = [ python3Packages.pytestCheckHook nix ];

  # pytestCheckHook runs the suite in installCheckPhase. The smoke command runs after it.
  doInstallCheck = true;
  postInstallCheck = ''
    export HOME=$TMPDIR
    $out/bin/templateer --help > /dev/null
  '';

  meta = {
    description = pyproject.project.description;
    homepage = "https://github.com/Bullish-Design/templateer_v2";
    license = lib.licenses.mit;
    mainProgram = "templateer";
  };
}
