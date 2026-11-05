import argparse
import json
from pathlib import Path

from um_agentic.contracts import SCHEMA_MODELS
from um_agentic.synthetic import generate_members, golden_fixture


def main() -> None:
    parser = argparse.ArgumentParser(description="Synthetic utilization management tools.")
    commands = parser.add_subparsers(dest="command", required=True)
    schemas = commands.add_parser("schemas")
    schemas.add_argument("--output", type=Path, default=Path("docs/schemas"))
    members = commands.add_parser("generate")
    members.add_argument("--count", type=int, default=20)
    members.add_argument("--output", type=Path, default=Path("data/synthetic/members.json"))
    fixtures = commands.add_parser("fixtures")
    fixtures.add_argument("--output", type=Path, default=Path("tests/golden/fixtures"))
    evaluation = commands.add_parser("eval")
    evaluation.add_argument("--live", action="store_true")
    evaluation.add_argument("--cases-per-set", type=int, default=50)
    evaluation.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.command == "schemas":
        args.output.mkdir(parents=True, exist_ok=True)
        for model in SCHEMA_MODELS:
            (args.output / f"{model.__name__}.json").write_text(
                json.dumps(model.model_json_schema(), indent=2) + "\n"
            )
    elif args.command == "generate":
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps([member.model_dump(mode="json") for member in generate_members(args.count)], indent=2)
            + "\n"
        )
    elif args.command == "fixtures":
        args.output.mkdir(parents=True, exist_ok=True)
        for number in range(1, 11):
            (args.output / f"g{number}.json").write_text(
                json.dumps(golden_fixture(f"G{number}"), indent=2) + "\n"
            )
    elif args.command == "eval":
        from um_agentic.evaluation import run_evaluation
        from um_agentic.settings import Settings

        report = run_evaluation(
            Settings(model_backend="foundry" if args.live else "fake"),
            per_set=args.cases_per_set,
        )
        serialized = report.model_dump_json(indent=2) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(serialized)
        print(serialized)
        if not report.passed:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
