# Human supervised utilization management

A reference implementation that helps health plans assemble prior authorization requests, find clinical evidence, and route cases to qualified reviewers. Deterministic rules own case status and deadlines. Agents prepare cited proposals. Authenticated clinicians make every adverse determination.

This project uses synthetic members, records, and criteria only. It is not a medical device or a production clinical system. Clinical validation, regulatory review, security assessment, and policy configuration are required before real use.

## Project status

The first five milestones in [AGENTS.md](AGENTS.md) are implemented and pass offline acceptance tests. This includes all four intake adapters, cited evidence and independent verification, synthetic criteria, recommendations, the authenticated reviewer API, the React workbench, and human approved letters.

The final milestone is in progress. Audit exports, replay, analytics, telemetry, Docker startup, and cloud deployment are being integrated and validated. Hosted CI and live model acceptance remain external gates.

## Local development

Requirements. Python 3.12 and a working virtual environment installation.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[test]"
uvicorn um_agentic.api.app:app --reload
```

Open the API documentation at <http://localhost:8000/docs>. Check readiness at <http://localhost:8000/health>.

Start the reviewer UI in a second terminal:

```bash
npm --prefix ui ci
npm --prefix ui run dev
```

Open <http://localhost:5173>. Sign in with a synthetic development identity, select a golden scenario, and choose **Process synthetic request**. Review the criterion grid and open citations before making a determination. Letters remain drafts until a human confirms release.

Lightweight development uses an explicit SQLite database. Set `UM_DATABASE_URL` to a PostgreSQL connection URL to use PostgreSQL. Production rejects SQLite and development authentication.

Development requests use fixed synthetic identities, not arbitrary caller supplied roles:

```bash
curl -H "Authorization: Bearer dev-physician" http://localhost:8000/me
```

Other development identities are `dev-intake`, `dev-nurse`, `dev-pharmacist`, `dev-supervisor`, `dev-auditor`, and `dev-admin`. Never expose development authentication to an untrusted network.

## Validation

```bash
pytest -m "not live"
ruff check .
mypy um_agentic
npm --prefix ui test
npm --prefix ui run build
```

The default tests block network access. PostgreSQL acceptance tests run when `UM_TEST_DATABASE_URL` is set. Live model evaluations are a separate opt in gate.

Generate synthetic members and export the contract schemas:

```bash
um generate
um schemas
```

## Repository guide

| Location | Purpose |
| --- | --- |
| [AGENTS.md](AGENTS.md) | Complete build specification, safety constraints, and milestone acceptance criteria |
| [um_agentic/](um_agentic/) | Python API, contracts, persistence, workflow, and agent interfaces |
| [tests/](tests/) | Unit, integration, and golden scenario acceptance tests |
| [ui/](ui/) | Accessible React and TypeScript reviewer workbench |
| [data/synthetic/](data/synthetic/) | Invented reference data |
| [docs/DECISIONS.md](docs/DECISIONS.md) | Implementation decisions, alternatives, and reversal paths |
| [docs/schemas/](docs/schemas/) | Generated JSON Schemas for public and agent contracts |

## Safety boundaries

1. No agent can deny, partially approve, modify a determination, change case state, or release a letter.
2. Automatic approval defaults to off and requires deterministic criteria with verified citations.
3. Missing or unverified evidence is unknown, never an invented clinical fact.
4. Regulatory deadlines include weekends. Missing information does not pause a clock by default.
5. Clinical documents are untrusted data. Model and telemetry boundaries must not turn them into instructions.
6. Only synthetic, unlicensed criteria may be committed.

Appeals, concurrent review, real clearinghouse transports, and licensed criteria integrations are future integration seams, not v1 implementations.
