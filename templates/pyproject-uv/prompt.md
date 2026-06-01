You are filling a `PyprojectUvModel`.

Use the user request and provided project facts to choose values.

Rules:

- Prefer `uv`-style dependency groups.
- Do not invent unrelated frameworks.
- If the project uses FastAPI, include `fastapi` and `uvicorn`.
- If tests are requested or detected, include `pytest` in dev dependencies.
- If linting or formatting is requested, include Ruff config.
- Choose a Python version consistent with the project facts.
- Return only data that conforms to the schema.
