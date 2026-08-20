# Round-2 follow-ups

The remediation leaves these items outside its implementation scope.

## New output languages

Add each new language as a deliberate feature. Define its escape grammar,
parser validator, audit payloads, and tests together. Candidate languages
include Nix, HCL, and Dockerfile syntax.

## Argentic Phase 6 consumer loop

Coordinate the consumer loop in argentic.space Phase 6. The consumer must read
`GenerationResult.kind` and `GenerationResult.region`. It must apply a region
result to the addressed page block.

## Region fence grammar

Pin the 05 D6 fence grammar against argentic's `replace_range` behavior. Test
the exact boundary syntax before the two projects ship the integration.

## Schema-driven audit fixtures

Generate audit values for nullable and optional schema fields that an example
fixture omits. The current audit reports fixture-shaped coverage through
`fields_probed`, but it does not synthesize absent fields.

## Internal failure reason

Consider an `INTERNAL_ERROR` failure reason for unexpected pipeline exceptions.
The current boundary returns `RENDER_FAILED` and includes the exception type in
`error_detail`. A new reason requires a public exit-code and JSON-contract
change.
