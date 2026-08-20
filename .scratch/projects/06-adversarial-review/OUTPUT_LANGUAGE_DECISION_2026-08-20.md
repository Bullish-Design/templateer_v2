# Output language decision — 2026-08-20

## Decision

Do not add Nix, HCL, or Dockerfile syntax in this follow-up.

Templateer treats each output language as a complete safety feature. The
repository does not contain a concrete template or consumer for these three
languages. The current development template produces TOML. The current
behavioral contract does not require another language.

The historical concept document names Nix files and Dockerfiles as possible
artifacts. The round-2 review also names Nix, HCL, and Dockerfile syntax. These
references do not define a template schema, a consumer, an output path, or an
interpolation contract. They are not enough to select a language safely.

## Repository evidence

- `src/templateer/models.py` closes the structured set over TOML, JSON, YAML,
  and Python. It closes the unstructured set over Markdown and text.
- `templates/pyproject-uv/metadata.yml` is the only development template. It
  declares TOML.
- `pyproject.toml` has no Nix, HCL, or Dockerfile parser dependency.
- The Python environment has no `hcl2`, `tree_sitter`, `dockerfile_parse`, or
  Nix parser module.
- The development shell provides Nix 2.34.7, Docker 29.6.0, and Buildx 0.31.1.
  It does not provide Terraform or OpenTofu.
- `nix-instantiate --parse --expr` parsed a sample attribute set. This proves
  that a local Nix syntax gate is possible. It does not define the product
  contract for generated Nix files.
- Repository searches for the three candidate names found historical design
  examples, review text, and negative tests. They found no owned template or
  consumer integration.

## Candidate analysis

### Nix

Nix would be a structured language. The available `nix-instantiate --parse`
command can provide syntax validation. A complete implementation would pin the
command and its supported Nix version.

The first safe scope should use double-quoted Nix strings. It must escape a
double quote, a backslash, and `${`. It must define newline, carriage-return,
tab, and Unicode behavior. Nix also has indented strings with different escape
rules, so the template contract must either forbid them at interpolation sites
or add a second finalizer. The [Nix string literal grammar](https://nix.dev/manual/nix/2.34/language/string-literals.html)
documents both forms.

Booleans would render as `true` and `false`. Null would render as `null` only
at a site that declares a Nix value. Lists and attribute sets need explicit
element rendering. Direct container interpolation would remain an error.

### HCL

HCL would be a structured language. HashiCorp maintains the HCL 2 parser as a
Go API. The [official HCL repository](https://github.com/hashicorp/hcl) exposes
native and JSON parsers. Templateer has no compatible parser dependency or
parser command in its pinned environment.

Quoted HCL strings must escape quotes, backslashes, control characters, `${`,
and `%{`. The last two sequences start HCL template processing. HashiCorp
documents `$${` and `%%{` as the literal forms in its
[string and template reference](https://developer.hashicorp.com/terraform/language/expressions/strings).

Booleans would render as `true` and `false`. Null would render as `null` only
at a declared HCL value site. Lists and objects need HCL element rendering.
The [type and value reference](https://developer.hashicorp.com/terraform/language/expressions/types)
defines those literal forms. Direct container interpolation would remain an
error.

### Dockerfile syntax

Dockerfile syntax has a structured instruction grammar, but it has no single
string literal grammar. Shell-form instructions delegate quoting and expansion
to the selected shell. Exec-form instructions use JSON arrays. The `SHELL`
instruction and `# escape=` directive change interpretation. The
[Dockerfile reference](https://docs.docker.com/reference/dockerfile) documents
these forms.

The installed Docker and Buildx commands are not a sufficient parser contract.
A build check can require a daemon, a build context, or a syntax frontend. A
generic string finalizer cannot safely cover shell form, JSON form, Linux
shells, Windows shells, heredocs, and parser directives at once.

A future Dockerfile feature must first restrict the supported instructions and
forms. It must select the parser frontend and version. It must then define an
escape grammar for each allowed interpolation context.

## Evidence required to unblock implementation

Implementation can start when one candidate has all of this evidence:

1. An owned template or consumer names the concrete artifact.
2. A product specification defines the supported dialect and version.
3. The specification identifies every allowed interpolation context.
4. The specification defines string, Boolean, null, and container behavior.
5. The pinned environment provides a deterministic parser or syntax checker.
6. Tests can run without a provider key or network access.
7. The planned audit payloads target the language's escape and interpolation
   operators.
8. The implementation plan includes exhaustive, fuzz, round-trip, CLI, Python
   API, vulnerable-template, and mutation coverage.

For Dockerfile syntax, the evidence must also select shell or exec form, the
target operating system, the shell, and the `# escape=` policy.
