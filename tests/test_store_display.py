from hr_agents.store import Submission


def test_application_answers_drops_what_the_page_already_shows():
    submission = Submission(
        row_number=2,
        values={
            "Timestamp": "2026-05-20",
            "Submission ID": "SUB-1",
            "Name": "Jane Smith",
            "Email": "jane@example.com",
            "Role": "Ops Coordinator",
            "Answers": "Six years in operations.",
            "Video Link": "x.mp4",
            "AI Score": "87",
        },
    )
    assert submission.application_answers() == {
        "Role": "Ops Coordinator",
        "Answers": "Six years in operations.",
    }


def test_form_response_keeps_the_name_for_the_evaluator():
    # The model checks the video against the application, so the name stays in.
    submission = Submission(row_number=2, values={"Timestamp": "t", "Name": "Jane", "Role": "Ops"})
    assert submission.form_response() == {"Name": "Jane", "Role": "Ops"}
