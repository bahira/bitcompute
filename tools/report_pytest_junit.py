"""Emit failing pytest cases as GitHub Actions annotations from JUnit XML."""
from __future__ import annotations

import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def _escape_command_data(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def main() -> int:
    report_path = Path(sys.argv[1] if len(sys.argv) > 1 else "pytest-results.xml")
    if not report_path.is_file():
        return 0
    root = ET.parse(report_path).getroot()
    failures: list[str] = []
    for case in root.iter("testcase"):
        outcome = case.find("failure")
        if outcome is None:
            outcome = case.find("error")
        if outcome is None:
            continue
        case_name = "::".join(
            part for part in (case.get("classname"), case.get("name")) if part
        )
        details = outcome.get("message", "") + "\n" + (outcome.text or "")
        failures.append(f"{case_name}\n{details}"[-6000:])
    if not failures:
        return 0
    summary = "\n\n".join(failures[:10])
    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_file:
        with Path(summary_file).open("a", encoding="utf-8") as stream:
            stream.write("## Pytest failures\n\n```text\n")
            stream.write(summary)
            stream.write("\n```\n")
    for failure in failures[:10]:
        print(f"::error title=pytest failure::{_escape_command_data(failure)}")
    if len(failures) > 10:
        print(f"::warning::{len(failures) - 10} additional pytest failures omitted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
