# gzp-finanace

Personal-finance transaction classifier. The intended pipeline is:

`bank export -> bank normalizer -> deterministic rules -> ML -> manual review -> labelled history`

## V0 implementation

The app imports the three bank formats, persists in PostgreSQL, detects duplicate
statements and overlapping transactions, applies field-level deterministic rules,
and offers an authenticated review UI. User confirmations retain immutable audit
snapshots and optionally become training examples or explicitly confirmed rules.
The original rules' match/set contract and precedence are preserved.
The original `MisFinanzasMovimientos202604.xlsx` workbook is imported directly as
the canonical historical ledger. Its rows and per-bank totals are separate from
bank-statement transactions, which are staged for review rather than added again
to the historical net.

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.lock
.venv/bin/pip install --no-deps -e '.[test]'
cp .env.example .env
# Configure private credentials in .env, then:
docker compose up -d --build
```

Open http://127.0.0.1:8088. Follow [deployment and administration](docs/DEPLOYMENT.md)
for SSH tunnels, private rule loading, migrations, backups and batch ML training.
The PostgreSQL port is private by default. Existing VPS services are independent.
The app refuses to start without configured credentials.

Do not put real financial data in fixtures or Git. Store it outside the checkout,
or in ignored `data/private/`. Private validation files and metrics stay there too.
`.dockerignore` excludes private data and environment files from image builds.

## Validation

```bash
.venv/bin/pytest -q
# Run the same pipeline tests against a disposable PostgreSQL database:
TEST_DATABASE_URL=postgresql+psycopg://USER@localhost/QA_DATABASE .venv/bin/pytest -q
# Upgrade an empty app database and verify model/schema agreement:
DATABASE_URL=postgresql+psycopg://USER@localhost/APP_DATABASE .venv/bin/alembic upgrade head
DATABASE_URL=postgresql+psycopg://USER@localhost/APP_DATABASE .venv/bin/alembic check
```

PostgreSQL tests use isolated temporary schemas and remove only those schemas.
Without TEST_DATABASE_URL, tests use an in-memory SQLite database; PostgreSQL is
the deployment database. All committed fixtures are synthetic.

Financial amounts use Decimal and NUMERIC. Trade Republic fees/taxes are included
in net cash amounts; BUY/SELL fills of the same account/security/type/day within
two seconds are grouped with their original rows and component IDs. MIGRATION
records are security-unit movements and counted separately, without creating EUR
cash transactions. Partially overlapping grouped fills are rejected atomically.
For banks without an external transaction ID, the fallback preserves identical
payments within a statement with an occurrence index. Disjoint partial statements
of indistinguishable same-day payments cannot be disambiguated: prefer full exports
when such payments occur. Multiple MyInvestor accounts without an account identifier
are outside V0; its delivered format contains no account column.

Baseline ML is trained and activated explicitly through the CLI. It never overwrites
rule fields or manual confirmations. Low-confidence predictions remain suggestions.
See deployment notes for thresholds, evaluation limits and the May-Sep holdout.
The UI's "complete" status describes the three main fields, not human approval.
Review uses historical dropdowns for transaction type, expense type, taxation,
general category and subtype (filtered by category). Asset and detail fields offer
autocomplete from existing labels. New labels can be entered explicitly; opening
or filtering a review never discards its current values.

## Deterministic rules v1

Rules are deliberately conservative. A generated rule is allowed to fill a target field only when:

- the normalized bank concept has appeared at least 3 times (configurable), and
- every supporting training row has the same non-empty value for that target field, and
- by default the source match confidence is `Alta`.

Rules are **field-level**. A merchant can deterministically set `tipo_transaccion` and `categoria_general` while leaving `activo` or `detalle` for ML/manual review.

More-specific rules (`concept + bank_type`, etc.) run before concept-only rules. Lower-priority rules may fill missing fields but cannot overwrite a field already produced by a higher-priority rule.

## Generate private rules

Do not commit the dataset or generated personal rules: this repository is public and those files can contain private financial information.

```bash
python scripts/generate_rules.py \
  data/Dataset_entrenamiento_finanzas.csv \
  rules/generated_private.json
```

To include medium-confidence historical matches as well:

```bash
python scripts/generate_rules.py \
  data/Dataset_entrenamiento_finanzas.csv \
  rules/generated_private.json \
  --confidence Media
```

## Run tests

```bash
python -m unittest discover -s tests
```
