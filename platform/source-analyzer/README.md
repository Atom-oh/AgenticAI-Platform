# Source analyzer

Trusted static extraction for the project's canonical ontology.
Input is a bounded JSON manifest of source text/hash/path entries and an approved
resolver profile. Nothing in the manifest is evaluated or imported.

```bash
npm ci --ignore-scripts
npm test
node --max-old-space-size=384 analyze.cjs input.json output.json
```

The CLI limits request/result size and writes a sanitized error on failure.
The host supplies a 60-second deadline and a credential-free process environment.
Production execution belongs in the configured AgentCore Code Interpreter;
running this CLI locally does not prove that service is configured.

Use `schemaVersion: 1`, `files: [{path,kind,sha256,text?}]` and optional
`resolver: {aliases,packages,jsonAssetFields}`. Kinds are code, style, HTML,
JSON and asset; binary asset entries carry hashes without executable text.
Source paths must be canonical, relative and unique without case collisions.
Declared package resolution requires exact package version and hash.
Previous CSS source-map loading is disabled, including local map files.
Function calls, constructors, transforms and active HTML behavior that the
extractor does not inspect remain location-bound unknown observations.

Output includes input/resolver/result hashes, parser identity, import/JSX/
resource observations, exported symbols, unresolved references and bounded
diagnostics. Structural manifest coverage is not runtime completeness,
customer-package approval or an executed UX test. See the
[ontology contract](../workspace/ONTOLOGY_CONTRACT.md).

Coverage remains incomplete even when every recorded literal reference resolves:
these rules cannot certify all program behavior, opaque resources or unconfigured
transforms. Reference counts and location-bound unknown observations describe
what was actually inspected.
Generic call/constructor observations aggregate counts per file and reason,
preserving the first location. When a parser, reference, export or observation
budget is exhausted, `coverage.truncatedFiles` identifies each affected file.
Uninspected locations are never represented as complete coverage.
Observations also share a 3,500,000-byte serialized budget, reserving space within
the 4,000,000-byte result limit for coverage and hashes. Budget exhaustion marks
the affected files instead of discarding the entire analysis. HTML with an
unconfigured `base` retains each literal resource as an unresolved observation.
