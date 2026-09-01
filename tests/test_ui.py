from io import StringIO

from rich.console import Console

from lab_ai_assistant.ui import ChatUI


def test_confirmation_panel_has_one_consistent_yes_instruction() -> None:
    output = StringIO()
    ui = ChatUI(Console(file=output, force_terminal=False, width=100))

    ui.print_confirmation_prompt(
        "OVERCOMMIT WARNING",
        "Approve this exact overcommit risk-bound plan?",
    )

    rendered = output.getvalue()
    assert "Approve this exact overcommit risk-bound plan?" in rendered
    assert "(reply 'yes' to confirm)" in rendered
    assert "approve overcommit" not in rendered.lower()
