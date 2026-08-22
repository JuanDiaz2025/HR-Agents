#!/usr/bin/env python3
"""Write a demo CSV so you can see the app before wiring up real data.

    python scripts/seed_demo.py demo.csv
    HR_AGENTS_CSV_PATH=demo.csv hr-agents serve

The evaluations here are handwritten, not model output. Nothing in this file is
used by the pipeline.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

HEADERS = [
    "Timestamp", "Submission ID", "Name", "Email", "Role", "Answers", "Video Link",
    "Status", "AI Score", "AI Decision", "Confidence", "Feedback", "Strengths",
    "Areas to Improve", "Flags", "Criterion Scores", "Rubric Version", "Evaluated At",
    "Error", "Reviewer", "Reviewer Decision", "Reviewer Notes", "Reviewed At",
]


DETAIL = {
    "communication": (
        "Steady pace, clear structure, easy to follow throughout.",
        "00:14", "Opened by naming the three questions before answering them.",
    ),
    "completeness": (
        "Addressed each of the three prompts with substance.",
        "02:41", "Closed out the third prompt without prompting.",
    ),
    "relevant_experience": (
        "Named specific systems and described their own role in the work.",
        "01:12", "Walked through the payroll migration end to end.",
    ),
    "consistency": (
        "Video matches the written application.",
        "01:58", "Stated the same six-year tenure given on the form.",
    ),
    "compliance": (
        "Covered the required topics, within the stated length.",
        "03:20", "Finished at 3:20 against a four-minute limit.",
    ),
    "professionalism": (
        "Prepared, on topic, appropriate setting.",
        "00:03", "Quiet room, camera at eye level, notes to hand.",
    ),
}


def criteria(**scores) -> str:
    return json.dumps([
        {
            "id": key,
            "score": value,
            "rationale": DETAIL[key][0],
            "evidence": [{"timestamp": DETAIL[key][1], "observation": DETAIL[key][2]}],
        }
        for key, value in scores.items()
    ])


ROWS = [
    {
        "Submission ID": "SUB-1001", "Name": "Jordan Avery", "Email": "jordan@example.com",
        "Role": "Operations Coordinator",
        "Answers": "Six years in operations, most recently running a payroll migration for 400 staff.",
        "Status": "DONE", "AI Score": "87", "AI Decision": "PASS", "Confidence": "92",
        "Feedback": "Clear, well-organized answers with specific examples from recent work.",
        "Strengths": "Concrete examples; clear structure",
        "Areas to Improve": "Could quantify outcomes",
        "Criterion Scores": criteria(
            communication=90, completeness=85, relevant_experience=90,
            consistency=85, compliance=100, professionalism=80,
        ),
        "Evaluated At": "2026-05-20T10:22:00+00:00",
    },
    {
        "Submission ID": "SUB-1002", "Name": "Sam Okafor", "Email": "sam@example.com",
        "Role": "Operations Coordinator",
        "Answers": "Three years in a support role, looking to move into operations.",
        "Status": "DONE", "AI Score": "62", "AI Decision": "NOT PASS", "Confidence": "84",
        "Feedback": "Answered every prompt, but described interest rather than experience.",
        "Strengths": "Answered all prompts; clear audio",
        "Areas to Improve": "Give specific examples of work done; name the systems used",
        "Criterion Scores": criteria(
            communication=75, completeness=70, relevant_experience=40,
            consistency=70, compliance=100, professionalism=55,
        ),
        "Evaluated At": "2026-05-20T10:31:00+00:00",
    },
    {
        "Submission ID": "SUB-1003", "Name": "Priya Raman", "Email": "priya@example.com",
        "Role": "Operations Coordinator", "Answers": "Eight years, two as a team lead.",
        "Status": "DONE", "AI Score": "71", "AI Decision": "NEEDS REVIEW", "Confidence": "48",
        "Feedback": "Audio drops out between 01:40 and 02:30, so a third of the response could not be assessed.",
        "Strengths": "Strong opening; relevant background",
        "Areas to Improve": "Re-record with working audio",
        "Flags": "unintelligible_audio",
        "Criterion Scores": criteria(
            communication=45, completeness=60, relevant_experience=85,
            consistency=80, compliance=100, professionalism=75,
        ),
        "Evaluated At": "2026-05-20T11:02:00+00:00",
    },
    {
        "Submission ID": "SUB-1004", "Name": "Dana Whitfield", "Email": "dana@example.com",
        "Role": "Operations Coordinator", "Answers": "Five years in logistics coordination.",
        "Status": "DONE", "AI Score": "79", "AI Decision": "PASS", "Confidence": "88",
        "Feedback": "Solid, specific answers across all three prompts.",
        "Strengths": "Specific examples; good pacing",
        "Areas to Improve": "Ran slightly over the stated length",
        "Criterion Scores": criteria(
            communication=80, completeness=85, relevant_experience=75,
            consistency=80, compliance=50, professionalism=85,
        ),
        "Evaluated At": "2026-05-20T11:40:00+00:00",
        "Reviewer": "hr@example.com", "Reviewer Decision": "PASS",
        "Reviewer Notes": "Agreed — strong logistics background.",
        "Reviewed At": "2026-05-20T14:02:00+00:00",
    },
    {
        "Submission ID": "SUB-1005", "Name": "Chris Lindqvist", "Email": "chris@example.com",
        "Role": "Operations Coordinator", "Answers": "Career changer from hospitality management.",
        "Status": "DONE", "AI Score": "68", "AI Decision": "NOT PASS", "Confidence": "81",
        "Feedback": "Limited directly relevant experience described.",
        "Strengths": "Very clear communicator",
        "Areas to Improve": "Connect hospitality experience to operations work explicitly",
        "Criterion Scores": criteria(
            communication=95, completeness=80, relevant_experience=35,
            consistency=75, compliance=100, professionalism=90,
        ),
        "Evaluated At": "2026-05-20T12:15:00+00:00",
        "Reviewer": "hr@example.com", "Reviewer Decision": "PASS",
        "Reviewer Notes": "Rubric undervalues transferable experience. Hospitality ops is close enough; advancing.",
        "Reviewed At": "2026-05-20T14:20:00+00:00",
    },
    {
        "Submission ID": "SUB-1006", "Name": "Alex Moreau", "Email": "alex@example.com",
        "Role": "Operations Coordinator", "Answers": "Four years in vendor management.",
        "Status": "ERROR", "Error": "MediaError: Video is 1140s, over the 900s limit.",
        "Evaluated At": "2026-05-20T12:40:00+00:00",
    },
    {
        "Submission ID": "SUB-1007", "Name": "Robin Tate", "Email": "robin@example.com",
        "Role": "Operations Coordinator", "Answers": "Two years as an operations assistant.",
        "Status": "PENDING",
    },
]


def main(destination: str = "demo.csv") -> None:
    path = Path(destination)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=HEADERS)
        writer.writeheader()
        for row in ROWS:
            row.setdefault("Timestamp", "2026-05-20T09:00:00+00:00")
            row.setdefault("Video Link", "https://example.com/video.mp4")
            row.setdefault("Rubric Version", "1")
            writer.writerow({h: row.get(h, "") for h in HEADERS})
    print(f"wrote {path} with {len(ROWS)} submissions")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "demo.csv")
