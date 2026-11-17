import argparse
import asyncio
import json
import statistics
from datetime import date
from pathlib import Path
from typing import Any

from voca.app.wiring import make_decider
from voca.config import Settings
from voca.conversation.verdict import Decision, DecisionFailure, Situation

ROOT = Path(__file__).resolve().parents[1]
CORPUS = Path(__file__).resolve().parent / "corpus" / "decision.json"
OUT = ROOT / ".local" / "bench" / "decision" / "decision.json"


def situation_of(base: dict[str, Any], phrase: str) -> Situation:
    return Situation(
        task=base["task"],
        request=base["request"],
        command=base.get("command"),
        announcement=base["announcement"],
        phrase=phrase,
    )


def passed(case: dict[str, Any], result: Decision | DecisionFailure) -> bool:
    return isinstance(result, Decision) and result.verdict in case["expect"]


def percentile(values: list[int], share: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(share * len(ordered)) - 1))
    return ordered[index]


def entry(case: dict[str, Any], result: Decision | DecisionFailure) -> dict[str, Any]:
    status = "PASS" if passed(case, result) else "FAIL"
    if isinstance(result, Decision):
        return {
            "id": case["id"],
            "phrase": case["phrase"],
            "expect": case["expect"],
            "status": status,
            "verdict": result.verdict,
            "picked": result.picked,
            "confidence": result.confidence,
            "ms": result.ms,
        }
    return {
        "id": case["id"],
        "phrase": case["phrase"],
        "expect": case["expect"],
        "status": status,
        "failure": result.reason,
        "ms": result.ms,
    }


async def run(rounds: int) -> int:
    settings = Settings()
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    base = corpus["situation"]
    cases = corpus["cases"]
    decider = make_decider(settings)
    try:
        await decider.decide(situation_of(base, cases[0]["phrase"]))
        first: list[dict[str, Any]] = []
        times: list[int] = []
        failures = 0
        for number in range(rounds):
            for case in cases:
                result = await decider.decide(situation_of(base, case["phrase"]))
                times.append(result.ms)
                failures += isinstance(result, DecisionFailure)
                if number == 0:
                    row = entry(case, result)
                    first.append(row)
                    print(
                        row["status"],
                        row["id"],
                        row.get("verdict", row.get("failure")),
                        row.get("picked", "-"),
                        round(row.get("confidence", 0.0), 3),
                        row["ms"],
                        flush=True,
                    )
    finally:
        await decider.aclose()
    good = sum(row["status"] == "PASS" for row in first)
    p50 = round(statistics.median(times)) if times else 0
    p95 = percentile(times, 0.95)
    print(
        f"decision {good}/{len(cases)} p50 {p50} ms p95 {p95} ms "
        f"(rounds {rounds}, failures {failures})",
        flush=True,
    )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(
            {
                "model": settings.decision.model,
                "url": settings.decision.url,
                "date": date.today().isoformat(),
                "passed": good,
                "total": len(cases),
                "p50_ms": p50,
                "p95_ms": p95,
                "rounds": rounds,
                "failures": failures,
                "cases": first,
                "times_ms": times,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return 0 if good == len(cases) else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Decision model: verdicts on approval replies"
    )
    parser.add_argument("--rounds", type=int, default=3)
    args = parser.parse_args()
    return asyncio.run(run(args.rounds))


if __name__ == "__main__":
    raise SystemExit(main())
