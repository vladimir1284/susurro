# Profile fixtures

Two profiles used by the integration tests. The people and companies in them are
invented; the size, structure and register are not. Context assembly and token budgeting
are easy to get wrong against three lines of placeholder text and much harder to get wrong
against something shaped like what a user will actually load.

| Fixture | Language | Mode | Exercises |
|---|---|---|---|
| `en-technical-backend.json` | `en` | `technical` | English recognizer path, long résumé, code-shaped hints |
| `es-business-industrial.json` | `es` | `business` | Spanish recognizer path, shorter context, meeting-shaped hints |

Having one of each language is deliberate: the recognizer is selected from the profile's
`language` field at `session.start`, so a single-language fixture set would leave half of
that selection untested.

## Why these are synthetic

Committed fixtures are public. Real profiles contain a real person's employment history,
and a public test fixture is not the place for one — regardless of whether the same
information is published elsewhere by its owner.

`local/` holds real profiles for manual testing and is in `.gitignore`. Put personal
material there, never here. Hint quality is worth judging against a real profile; the
automated tests only need the right shape and size.

## Keeping the shape honest

The synthetic profiles are sized to match real ones:

| Fixture | Static context characters | Rough token estimate |
|---|---|---|
| `en-technical-backend.json` | ~3.0k | ~750 |
| `es-business-industrial.json` | ~2.1k | ~530 |

Both sit well inside the 2–3k token static-context budget assumed in
`docs/architecture/inference.md`, which is what lets v1 skip retrieval entirely. If a
fixture ever grows past that budget, that is a signal about the design, not a reason to
quietly raise the number.

The token figures are characters divided by four. That rule of thumb understates Spanish,
and no real tokenizer has been run against them yet.
