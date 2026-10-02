"""Measure how accurately the support agent answers questions with known answers.

Runs every case in scripts/eval_cases.json through the real agent and connector, checks
each final answer, and reports accuracy, tool calls and latency. Expected answers come
from the demo data in scripts/seed.py, so run that first.

    uv run python scripts/evaluate_agent.py
    uv run python scripts/evaluate_agent.py --save    # also write docs/evaluation.md
"""

import argparse
import asyncio
import io
import json
import statistics
import sys
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from support_agent import AgentRun, AgentSettings, agent_session, build_model_chain, run_agent

CASES_PATH = Path(__file__).resolve().parent / "eval_cases.json"
REPORT_PATH = Path(__file__).resolve().parent.parent / "docs" / "evaluation.md"
UNICODE_HYPHEN = chr(0x2010)


def normalise(text: str) -> str:
    """Lower-case and fold typography, so checks compare content, not formatting.

    Models often write narrow no-break spaces (U+202F) between names, non-breaking
    hyphens (U+2011) and Markdown emphasis such as **2**; all are folded to plain text.
    """
    # NFKC maps U+2011 (non-breaking hyphen) to U+2010 (hyphen); fold that to ASCII.
    folded = unicodedata.normalize("NFKC", text).replace(UNICODE_HYPHEN, "-")
    plain = folded.replace("*", "").replace("`", "")
    return " ".join(plain.lower().split())


@dataclass(frozen=True)
class Case:
    id: str
    question: str
    must_include: tuple[str, ...] = ()
    must_include_any: tuple[str, ...] = ()
    must_not_include: tuple[str, ...] = ()

    def problems(self, answer: str | None) -> list[str]:
        """Every way the answer misses the expectation; empty means it passed."""
        if answer is None:
            return ["no final answer"]
        text = normalise(answer)
        found = [f"missing '{s}'" for s in self.must_include if s not in text]
        if self.must_include_any and not any(s in text for s in self.must_include_any):
            found.append(f"missing any of {list(self.must_include_any)}")
        found += [f"contains '{s}'" for s in self.must_not_include if s in text]
        return found


@dataclass(frozen=True)
class Result:
    case: Case
    run: AgentRun
    problems: list[str]

    @property
    def passed(self) -> bool:
        return not self.problems


def load_cases() -> list[Case]:
    raw = json.loads(CASES_PATH.read_text(encoding="utf-8"))["cases"]
    return [
        Case(
            id=c["id"],
            question=c["question"],
            must_include=tuple(c.get("must_include", [])),
            must_include_any=tuple(c.get("must_include_any", [])),
            must_not_include=tuple(c.get("must_not_include", [])),
        )
        for c in raw
    ]


async def evaluate(cases: list[Case], settings: AgentSettings, pause: float) -> list[Result]:
    results = []
    async with agent_session(settings) as session:
        for number, case in enumerate(cases, start=1):
            if number > 1:
                await asyncio.sleep(pause)  # stay under free-tier tokens-per-minute limits
            print(f"[{number}/{len(cases)}] {case.id}: {case.question}")
            run = await run_agent(session, case.question, trace=False)
            result = Result(case, run, case.problems(run.answer))
            if result.passed:
                print("    PASS")
            else:
                print(f"    FAIL: {'; '.join(result.problems)}\n    answer: {run.answer!r}")
            results.append(result)
    return results


def report(results: list[Result], primary_model: str) -> str:
    passed = sum(r.passed for r in results)
    latencies = [r.run.latency_s for r in results]
    tool_calls = [len(r.run.tool_calls) for r in results]
    on_primary = sum(set(r.run.models_used) == {primary_model} for r in results)
    lines = [
        "# Agent evaluation",
        "",
        f"Run on {datetime.now(UTC):%Y-%m-%d} against the demo data from `scripts/seed.py`,",
        "with `scripts/evaluate_agent.py`. Each answer is checked for required facts and for",
        "things it must not contain, such as unmasked phone numbers or invented tickets.",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Correct answers | {passed}/{len(results)} |",
        f"| Tool calls per answer | {statistics.mean(tool_calls):.1f} average, "
        f"{max(tool_calls)} max |",
        f"| Latency per answer | {statistics.median(latencies):.1f} s median, "
        f"{max(latencies):.1f} s max |",
        f"| Answered by the primary model (`{primary_model}`) | {on_primary}/{len(results)} |",
        "",
        "| Case | Result | Tool calls | Latency | Model |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        verdict = "pass" if r.passed else "fail: " + "; ".join(r.problems)
        tools = ", ".join(c.name for c in r.run.tool_calls) or "none"
        model = r.run.models_used[-1] if r.run.models_used else "none"
        lines.append(
            f"| `{r.case.id}` | {verdict} | {tools} | {r.run.latency_s:.1f} s | `{model}` |"
        )
    lines += ["", "Latency includes any waits for free-tier rate limits and model fallbacks."]
    failed = [r for r in results if not r.passed]
    if failed:
        lines += ["", "## Failed answers", ""]
        for r in failed:
            quoted = [f"> {line}" for line in (r.run.answer or "(no answer)").splitlines()]
            lines += [f"**{r.case.id}**: {r.case.question}", "", *quoted, ""]
    return "\n".join(lines) + "\n"


def main() -> None:
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--save", action="store_true", help="write docs/evaluation.md")
    parser.add_argument(
        "--pause", type=float, default=20.0, help="seconds between questions (default 20)"
    )
    args = parser.parse_args()

    settings = AgentSettings()
    results = asyncio.run(evaluate(load_cases(), settings, args.pause))
    markdown = report(results, build_model_chain(settings)[0].label)
    print("\n" + markdown)
    if args.save:
        REPORT_PATH.write_text(markdown, encoding="utf-8", newline="\n")
        print("Wrote docs/evaluation.md")
    sys.exit(0 if all(r.passed for r in results) else 1)


if __name__ == "__main__":
    main()
