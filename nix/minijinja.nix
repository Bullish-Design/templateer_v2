# The `minijinja` Python binding, built from the PyPI sdist with maturin.
#
# nixpkgs has no `minijinja` Python package. The version matches the one in `uv.lock`.
# The cargo hash is the one `fetchCargoVendor` reports for this sdist.
{ lib
, buildPythonPackage
, fetchPypi
, rustPlatform
}:

let
  version = "2.22.0";
  src = fetchPypi {
    pname = "minijinja";
    inherit version;
    hash = "sha256-Bh4gpS5i0YJ1XQj105w82I1FUkqIN0m0QRrM6oZ683s=";
  };
in
buildPythonPackage {
  pname = "minijinja";
  inherit version src;
  pyproject = true;

  cargoDeps = rustPlatform.fetchCargoVendor {
    pname = "minijinja";
    inherit version src;
    hash = "sha256-hnWD89auEdZUtBLaScUrj/3k46As6mNhNfo3rXVwY0E=";
  };

  nativeBuildInputs = [
    rustPlatform.cargoSetupHook
    rustPlatform.maturinBuildHook
  ];

  pythonImportsCheck = [ "minijinja" ];

  meta = {
    description = "Python bindings for the MiniJinja template engine";
    homepage = "https://github.com/mitsuhiko/minijinja";
    license = lib.licenses.asl20;
  };
}
