# Font index generator

This workflow-oriented utility scans the repository for font binaries and generates a CSV index that is useful for downstream font matching and cataloging systems.

## Output fields

The CSV includes:

- repository location (`repo_path`, `family_directory`, `license_scope`)
- a raw GitHub download link for each font file (`download_url`)
- naming metadata from the font `name` table
- matching-oriented metrics from `head`, `hhea`, `OS/2`, and `post`
- variable font axis ranges serialized as JSON in `variation_axes`
- quick boolean/style flags such as bold/italic/monospace/regular
- glyph and cmap coverage counts

## Local usage

```bash
python tools/font_index/generate_font_index.py \
  --repo-root . \
  --output tools/font_index/font_index.csv \
  --repo-owner google \
  --repo-name fonts \
  --ref main
```

## GitHub Actions usage

Run the `Generate font index` workflow manually, or let CI execute it when the generator changes. The workflow uploads the generated CSV as a build artifact named `font-index-csv`.
