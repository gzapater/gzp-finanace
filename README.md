# gzp-finanace

Personal-finance transaction classifier. The intended pipeline is:

\`bank export -> bank normalizer -> deterministic rules -> ML -> manual review -> labelled history\`

## Deterministic rules v1

Rules are deliberately conservative. A generated rule is allowed to fill a target field only when:

- the normalized bank concept has appeared at least 3 times (configurable), and
- every supporting training row has the same non-empty value for that target field, and
- by default the source match confidence is \`Alta\`.

Rules are **field-level**. A merchant can deterministically set \`tipo_transaccion\` and \`categoria_general\` while leaving \`activo\` or \`detalle\` for ML/manual review.

More-specific rules (\`concept + bank_type\`, etc.) run before concept-only rules. Lower-priority rules may fill missing fields but cannot overwrite a field already produced by a higher-priority rule.

## Generate private rules

Do not commit the dataset or generated personal rules: this repository is public and those files can contain private financial information.

\`\`\`bash
python scripts/generate_rules.py \
  data/Dataset_entrenamiento_finanzas.csv \
  rules/generated_private.json
\`\`\`

To include medium-confidence historical matches as well:

\`\`\`bash
python scripts/generate_rules.py \
  data/Dataset_entrenamiento_finanzas.csv \
  rules/generated_private.json \
  --confidence Media
\`\`\`

## Run tests

\`\`\`bash
python -m unittest discover -s tests
\`\`\`
