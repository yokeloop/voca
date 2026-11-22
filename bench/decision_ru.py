import argparse
import asyncio
import contextlib
import json
import os
import statistics
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any

import httpx

from voca.config import DecisionConfig
from voca.conversation.approval_judge import deny_marked, interrogative
from voca.conversation.verdict import Sorted, drop

ROOT = Path(__file__).resolve().parents[1]
CORPUS = Path(__file__).resolve().parent / "corpus" / "decision.json"
OUT = ROOT / ".local" / "bench" / "decision" / "decision.json"
URL_DEFAULT = os.environ.get("VOCA_DECISION__URL", "http://127.0.0.1:8086")
MODEL_DEFAULT = "d1-2B"
LLM_URL_DEFAULT = "http://127.0.0.1:8081"
PATH = "/v1/systemone"
THRESHOLDS = (0.6, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95)
SELECTED = {
    "a": ("a",),
    "b": ("b",),
    "cd": ("c", "d"),
    "danger": ("danger",),
    "all": ("a", "b", "c", "d", "danger"),
}
LEVELS = ("low", "medium", "high")
Answers = dict[str, tuple[str, float]]
Questions = dict[str, dict[str, Any]]
PROMPT = (
    "Расскажи подробно, как устроен голосовой ассистент: распознавание речи, "
    "языковая модель и синтез речи. Ответь по-русски."
)
SORTER = DecisionConfig().sorter_prompt()


def state_of(case: dict[str, Any], requests: dict[str, Any]) -> dict[str, str]:
    state = {"последняя фраза агента": case["agent_last"]}
    chosen = [requests[name] for name in case["requests"]]
    if len(chosen) == 1:
        state["запрос"] = chosen[0]["description"]
        state["команда"] = chosen[0]["command"]
    else:
        for number, request in enumerate(chosen, 1):
            state[f"запрос {number}"] = request["description"]
            state[f"команда {number}"] = request["command"]
    state["фраза пользователя"] = case["phrase"]
    return state


def danger_state(request: dict[str, Any]) -> dict[str, str]:
    return {"описание запроса": request["description"], "команда": request["command"]}


def percentile(values: list[int], share: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(share * len(ordered)) - 1))
    return ordered[index]


def share_of(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if not 0 <= value <= 1:
        return None
    return float(value)


def read_answers(data: object, questions: Questions) -> Answers | None:
    answers = data.get("answers") if isinstance(data, dict) else None
    if not isinstance(answers, dict):
        return None
    result: Answers = {}
    for name, question in questions.items():
        answer = answers.get(name)
        if not isinstance(answer, dict):
            return None
        picked = answer.get("choice")
        confidence = share_of(answer.get("confidence"))
        if not isinstance(picked, str):
            return None
        if confidence is None or picked not in question["criteria"]:
            return None
        result[name] = (picked, confidence)
    return result


async def ask(
    client: httpx.AsyncClient,
    model: str,
    state: dict[str, str],
    questions: Questions,
) -> tuple[Answers | str, int, dict[str, Any] | None]:
    body = {
        "model": model,
        "state": state,
        "questions": {
            name: {"type": "choice", **question} for name, question in questions.items()
        },
    }
    started = time.monotonic()
    reason: str | None = None
    data: object = None
    try:
        response = await client.post(PATH, json=body)
        response.raise_for_status()
        data = response.json()
    except httpx.TimeoutException:
        reason = "timeout"
    except httpx.HTTPStatusError as error:
        reason = f"http_{error.response.status_code}"
    except httpx.HTTPError:
        reason = "connection"
    except ValueError:
        reason = "bad_answer"
    ms = round((time.monotonic() - started) * 1000)
    if reason is not None:
        return reason, ms, None
    raw = data.get("answers") if isinstance(data, dict) else None
    raw = raw if isinstance(raw, dict) else None
    parsed = read_answers(data, questions)
    if parsed is None:
        return "bad_answer", ms, raw
    return parsed, ms, raw


def numbered(form: dict[str, Any], number: int) -> dict[str, Any]:
    return {
        "instructions": form["instructions"].replace("{n}", str(number)),
        "criteria": form["criteria"],
    }


def questions_of(name: str, forms: dict[str, Any], case: dict[str, Any]) -> Questions:
    verdict = {
        "instructions": forms["a"]["instructions"],
        "criteria": forms["a"]["criteria"],
    }
    if name == "a":
        if not case["requests"]:
            verdict["criteria"] = forms["a"]["criteria_idle"]
        return {"verdict": verdict}
    if name == "b":
        if not case["requests"]:
            return {"kind": forms["b"]["kind"]}
        return {
            "kind": forms["b"]["kind"],
            "relation": forms["b"]["relation"],
            "answer": forms["b"]["answer"],
        }
    if name == "c":
        return {"verdict": verdict, "target": forms["c"]}
    return {
        "request_1": numbered(forms["d"], 1),
        "request_2": numbered(forms["d"], 2),
    }


def calls(
    name: str, corpus: dict[str, Any]
) -> list[tuple[dict[str, Any], dict[str, str], Questions]]:
    forms = corpus["forms"]
    if name == "danger":
        return [
            (request, danger_state(request), {"danger": forms["danger"]})
            for request in corpus["requests"]
        ]
    requests = {request["id"]: request for request in corpus["requests"]}
    cases = corpus["cases"]
    if name in ("c", "d"):
        cases = [case for case in cases if len(case["requests"]) == 2]
    return [
        (case, state_of(case, requests), questions_of(name, forms, case))
        for case in cases
    ]


def expected(name: str, item: dict[str, Any]) -> Any:
    if name == "danger":
        return item["expected_level"]
    if name == "c":
        return {"verdict": item["expect"], "target": item["target"]}
    if name == "d":
        return item["per_request"]
    return item["expect"]


def wanted(name: str, item: dict[str, Any]) -> int:
    if name == "danger":
        return 0
    if name == "d":
        return sum("approve" in allowed for allowed in item["per_request"].values())
    return int("approve" in item["expect"])


def after_rule(phrase: str, kind: tuple[str, float], label: str) -> str:
    if kind[0] == "noise":
        dropped = drop(phrase, Sorted("noise", kind[1], 0), SORTER, speaking=True)
        return "noise" if dropped else "other"
    if label == "deny" and not deny_marked(phrase):
        return "ask" if interrogative(phrase) else "other"
    return label


def score(name: str, item: dict[str, Any], answers: Answers) -> dict[str, Any]:
    false: list[float] = []
    yes: list[float] = []
    if name == "danger":
        level, confidence = answers["danger"]
        return {
            "picked": level,
            "confidence": confidence,
            "passed": level == item["expected_level"],
            "below": LEVELS.index(level) < LEVELS.index(item["expected_level"]),
            "false": false,
            "yes": yes,
        }
    if name == "d":
        labels = []
        passed = True
        confidences = []
        for number, request in enumerate(item["requests"], 1):
            label, confidence = answers[f"request_{number}"]
            allowed = item["per_request"][request]
            labels.append(label)
            confidences.append(confidence)
            passed = passed and label in allowed
            if label == "approve":
                (yes if "approve" in allowed else false).append(confidence)
        return {
            "picked": "/".join(labels),
            "confidence": min(confidences),
            "passed": passed,
            "false": false,
            "yes": yes,
        }
    if name == "c":
        label, confidence = answers["verdict"]
        target, target_confidence = answers["target"]
        passed = label in item["expect"] and target in item["target"]
        picked = f"{label}@{target}"
        if label == "approve":
            confidence *= target_confidence
            wrong = "approve" not in item["expect"]
            if target in ("request_1", "request_2"):
                request = item["requests"][int(target[-1]) - 1]
                wrong = wrong or "approve" not in item["per_request"][request]
            if wrong:
                false.append(confidence)
            elif passed:
                yes.append(confidence)
        return {
            "picked": picked,
            "confidence": confidence,
            "passed": passed,
            "false": false,
            "yes": yes,
        }
    if name == "a":
        label, confidence = answers["verdict"]
    else:
        label, confidence = answers["kind"]
        if label == "reply" and not item["requests"]:
            label = "other"
        elif label == "reply":
            relation, relation_confidence = answers["relation"]
            confidence *= relation_confidence
            if relation == "asks":
                label = "ask"
            elif relation == "other":
                label = "other"
            else:
                label, answer_confidence = answers["answer"]
                confidence *= answer_confidence
    if label == "approve":
        (yes if "approve" in item["expect"] else false).append(confidence)
    result = {
        "picked": label,
        "confidence": confidence,
        "passed": label in item["expect"],
        "false_deny": label == "deny" and "deny" not in item["expect"],
        "false": false,
        "yes": yes,
    }
    if name == "b":
        after = after_rule(item["phrase"], answers["kind"], label)
        result.update(
            after=after,
            after_passed=after in item["expect"],
            after_false_deny=after == "deny" and "deny" not in item["expect"],
        )
    return result


def entry(
    name: str,
    item: dict[str, Any],
    result: dict[str, Any] | str,
    ms: int,
    raw: dict[str, Any] | None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "form": name,
        "id": item["id"],
        "phrase": item["description"] if name == "danger" else item["phrase"],
        "expect": expected(name, item),
    }
    if isinstance(result, str):
        row.update(status="FAIL", failure=result, ms=ms, answers=raw)
        return row
    confidence = result["confidence"]
    row.update(
        status="PASS" if result["passed"] else "FAIL",
        verdict=result["picked"] if confidence >= 0.6 else "unclear",
        picked=result["picked"],
        confidence=confidence,
        ms=ms,
        answers=raw,
    )
    if "after" in result:
        row["after"] = result["after"]
    return row


async def measure(
    client: httpx.AsyncClient,
    model: str,
    name: str,
    corpus: dict[str, Any],
    rounds: int,
) -> dict[str, Any]:
    items = calls(name, corpus)
    _, state, questions = items[0]
    await ask(client, model, state, questions)
    print(f"form {name}", flush=True)
    first: list[dict[str, Any]] = []
    times: list[int] = []
    failures = 0
    false: list[float] = []
    yes: list[float] = []
    below = 0
    false_deny = 0
    after_false_deny = 0
    after_passed = 0
    for number in range(rounds):
        for item, state, questions in items:
            answers, ms, raw = await ask(client, model, state, questions)
            times.append(ms)
            failures += isinstance(answers, str)
            result = answers if isinstance(answers, str) else score(name, item, answers)
            if not isinstance(result, str):
                false.extend(result["false"])
                yes.extend(result["yes"])
                false_deny += result.get("false_deny", False)
                after_false_deny += result.get("after_false_deny", False)
            if number != 0:
                continue
            if not isinstance(result, str):
                below += result.get("below", False)
                after_passed += result.get("after_passed", False)
            row = entry(name, item, result, ms, raw)
            first.append(row)
            fields = [
                row["status"],
                row["id"],
                row.get("verdict", row.get("failure")),
                row.get("picked", "-"),
                round(row.get("confidence", 0.0), 3),
                row["ms"],
            ]
            if name == "b":
                fields.append(f"after={row.get('after', '-')}")
            print(*fields, flush=True)
    good = sum(row["status"] == "PASS" for row in first)
    p50 = round(statistics.median(times)) if times else 0
    p95 = percentile(times, 0.95)
    summary: dict[str, Any] = {
        "passed": good,
        "total": len(items),
        "p50_ms": p50,
        "p95_ms": p95,
        "failures": failures,
    }
    head = f"{good}/{len(items)} p50 {p50} ms p95 {p95} ms (rounds {rounds}, failures {failures}"
    if name == "danger":
        print(f"danger {head}, below rule {below})", flush=True)
        summary["below_rule"] = below
    else:
        boundary = max(false, default=0.0)
        total = rounds * sum(wanted(name, item) for item, _, _ in items)
        thresholds = {
            str(threshold): sum(value >= threshold for value in yes)
            for threshold in THRESHOLDS
            if threshold >= boundary
        }
        denies = f", false deny {false_deny}" if name in ("a", "b") else ""
        print(
            f"decision {name} {head}, false approve {len(false)}, "
            f"boundary {round(boundary, 3)}{denies})",
            flush=True,
        )
        if name == "b":
            print(
                f"after b {after_passed}/{len(items)}, "
                f"false deny after guard {after_false_deny}",
                flush=True,
            )
        print(
            " ".join(
                [f"thresholds {name}"]
                + [f"{key}:{count}/{total}" for key, count in thresholds.items()]
            ),
            flush=True,
        )
        summary.update(
            false_approve=len(false), boundary=boundary, thresholds=thresholds
        )
        if name in ("a", "b"):
            summary["false_deny"] = false_deny
        if name == "b":
            summary.update(after_passed=after_passed, after_false_deny=after_false_deny)
    summary.update(cases=first, times_ms=times)
    return summary


async def llm_alive(client: httpx.AsyncClient, llm_url: str) -> bool:
    try:
        response = await client.get(f"{llm_url}/health", timeout=5)
    except httpx.HTTPError:
        return False
    return response.status_code == 200


async def generate_load(
    client: httpx.AsyncClient,
    llm_url: str,
    model: str,
    stop: asyncio.Event,
    done: list[int],
) -> None:
    while not stop.is_set():
        response = await client.post(
            f"{llm_url}/v1/chat/completions",
            json={
                "model": model,
                "messages": [{"role": "user", "content": PROMPT}],
                "max_tokens": 1024,
                "temperature": 0,
                "stream": False,
            },
            timeout=300,
        )
        response.raise_for_status()
        done.append(1)


async def run(args: argparse.Namespace) -> int:
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    forms: dict[str, Any] = {}
    done: list[int] = []
    async with (
        httpx.AsyncClient(base_url=args.url, timeout=args.timeout) as client,
        httpx.AsyncClient() as llm,
    ):
        load: asyncio.Task[None] | None = None
        stop = asyncio.Event()
        if args.load:
            if not await llm_alive(llm, args.llm_url):
                print(f"llm не отвечает: {args.llm_url}", file=sys.stderr)
                return 2
            models = (await llm.get(f"{args.llm_url}/v1/models", timeout=10)).json()
            load = asyncio.create_task(
                generate_load(llm, args.llm_url, models["data"][0]["id"], stop, done)
            )
            await asyncio.sleep(1.0)
        try:
            for name in SELECTED[args.form]:
                forms[name] = await measure(
                    client, args.model, name, corpus, args.rounds
                )
        finally:
            if load is not None:
                stop.set()
                load.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await load
    report: dict[str, Any] = {
        "model": args.model,
        "url": args.url,
        "date": date.today().isoformat(),
        "load": args.load,
        "rounds": args.rounds,
        "forms": forms,
    }
    out = OUT
    if args.load:
        print(f"load generations {len(done)}", flush=True)
        report["load_generations"] = len(done)
        out = OUT.with_name("decision-load.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return 0 if all(form["failures"] == 0 for form in forms.values()) else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Decision model: sorter forms, thresholds and danger"
    )
    parser.add_argument("--form", choices=tuple(SELECTED), default="all")
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--url", default=URL_DEFAULT)
    parser.add_argument("--model", default=MODEL_DEFAULT)
    parser.add_argument("--timeout", type=float, default=1.5)
    parser.add_argument("--load", action="store_true")
    parser.add_argument("--llm-url", default=LLM_URL_DEFAULT)
    args = parser.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
