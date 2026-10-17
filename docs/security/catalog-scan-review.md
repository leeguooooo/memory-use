# Catalog scan review (2026-10-17)

Source revision: `9a5c4495fc08358757be1b1756bc86a94c7262a1`. With plugin-scanner 3.0.123, the original local score was 74; after these changes it is 91. These are local preflight results. Listing still requires the catalog's own completed centralized score of at least 80.

## Changes and review

Actions are pinned to immutable SHAs (the Rust action uses upstream master history with an explicit stable toolchain input where applicable), a vulnerability reporting policy and Dependabot are added, and a read-only, offline source scan runs on pushes and pull requests using the catalog's exact action SHA and score threshold. No scanner suppressions or baseline are configured.

The scan detects packaging/openai as a plugin even though it is a source template for an assembled ZIP. The template now includes a README, license, security policy and packaging exclusions, and the build ships that guidance. Its declared skills tree is assembled by build.sh; we do not duplicate the bundled helper source to satisfy a source-template check. Omitted optional Codex terms/screenshot fields remain informational. No secret findings were reported.

## Reproduction

```sh
# In an isolated Python environment:
python -m pip install plugin-scanner==3.0.123
plugin-scanner scan . --format json --output /tmp/memory-use-scan.json --min-score 80 --fail-on-severity none
```

## Remaining rule locations

| Rule | Severity | Location |
| --- | --- | --- |
| `SKILLS_DIR_MISSING` | medium | `packaging/openai/.codex-plugin/plugin.json` |
| `PLUGIN_JSON_INTERFACE_ASSET_TERMSOFSERVICEURL` | info | `packaging/openai/.codex-plugin/plugin.json` |
| `PLUGIN_JSON_INTERFACE_ASSET_SCREENSHOTS` | info | `packaging/openai/.codex-plugin/plugin.json` |
