# Finding code

Read this before searching a repository by hand. Use the strongest tool the
environment actually offers, and fall back cleanly when it is absent — never
assume a tool exists, and never refuse to work because one does not.

## Order of preference

1. **A project-specific index**, when the project has one. A `.codegraph/`
   directory means `codegraph_explore` is available for that path: it returns
   the source of the relevant symbols *plus* callers, callees and blast radius
   in one call, which is the whole question a plan needs answered.
2. **A global symbol index** — SymDex (`search_symbols`, `get_symbol`,
   `get_file_outline`, `get_callers`, `get_callees`, `build_context_pack`) or
   any equivalent the environment exposes. Index first if the repo is not
   indexed yet and the tool supports it.
3. **Plain search** — grep and glob. Always available, always correct, just
   slower and noisier. Narrow to matching files first, then read with line
   numbers.

Equivalent tools count. The rule is the capability, not the brand: a symbol
index beats text search, and text search beats reading files in the hope of
finding something.

## The rule that matters

**An index tells you where to look. It never becomes a citation.**

Indexes go stale between a write and a reindex, and a summary is not the line it
summarises. So:

- Use the index to *locate* — symbols, callers, blast radius, file outlines.
- Then **open the file and read the actual line** before quoting it in
  `evidence`, a review finding, or a claim of any kind.
- The `quote` in a citation is text you read from the file in this session, not
  text an index reported and not text you remember.

`wb sdd audit` reopens every citation and will catch the difference. Getting it
right the first time is cheaper than being caught.

## Blast radius

Before claiming a change is contained, ask the index who calls it. "Nothing else
uses this" is a claim like any other: it needs `get_callers` or an equivalent
search behind it, and it belongs in `evidence` with what you ran.

For a change to a public contract — an exported function, a route, a schema, an
event — the callers you cannot see (other repos, other teams) are the ones that
break. Say so explicitly rather than implying the search was exhaustive.
