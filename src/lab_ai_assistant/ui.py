"""Rich terminal UI components for the Lab AI Assistant chat experience."""

from contextlib import contextmanager
from typing import Optional

from rich.align import Align
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.rule import Rule
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

# Canonical-inspired theme
THEME = Theme(
    {
        "ai.name": "bold bright_magenta",
        "ai.body": "white",
        "ai.reasoning": "dim italic",
        "user.name": "bold bright_cyan",
        "user.body": "white",
        "phase.active": "bold bright_yellow",
        "phase.done": "bold bright_green",
        "tool.name": "bold bright_blue",
        "tool.status": "dim",
        "confirm.prompt": "bold bright_yellow",
        "error": "bold red",
        "header.title": "bold white",
        "header.subtitle": "dim white",
    }
)

AI_ICON = "🤖"
USER_ICON = "👤"
TOOL_ICON = "⚙️"
PHASE_ICONS = {
    "thinking": "💭",
    "analyzing": "🔍",
    "planning": "📐",
    "executing": "🚀",
    "confirming": "❓",
    "done": "✅",
}


class ChatUI:
    """Manages the rich terminal UI for AI assistant chat sessions."""

    def __init__(self, console: Optional[Console] = None):
        self.console = console or Console(theme=THEME, highlight=False)
        self._turn_count = 0

    def print_welcome(self):
        """Display a clean welcome banner."""
        banner = Table.grid(padding=(0, 1))
        banner.add_column(justify="center")

        banner.add_row("")
        banner.add_row(
            Text("Canonical AI Lab Assistant", style="bold bright_white")
        )
        banner.add_row(
            Text("MicroCloud Topology Planner & Deployer", style="dim")
        )
        banner.add_row("")
        banner.add_row(
            Text(
                f"{AI_ICON}  AI engine connected — ready to assist",
                style="bright_green",
            )
        )
        banner.add_row("")

        panel = Panel(
            Align.center(banner),
            border_style="bright_magenta",
            padding=(1, 2),
        )
        self.console.print(panel)
        self.console.print(
            Text(
                "  Type your request naturally • 'help' for commands • 'quit' to exit\n",
                style="dim",
            )
        )

    def get_user_input(self) -> str:
        """Display a styled user prompt and get input."""
        self._turn_count += 1
        self.console.print()  # breathing room
        prompt_text = Text()
        prompt_text.append(f" {USER_ICON} You ", style="user.name")
        prompt_text.append("› ", style="dim")
        self.console.print(prompt_text, end="")

        try:
            user_input = input("").strip()
        except EOFError:
            user_input = "quit"
        return user_input

    def print_user_echo(self, message: str):
        """Optionally re-print the user message in a styled panel (for clarity in scrollback)."""
        # We rely on the terminal echoing input; no need for a separate panel.
        pass

    @contextmanager
    def thinking_indicator(self, label: str = "Thinking", timeout: Optional[int] = None):
        """Show an animated spinner while the AI is processing."""
        suffix = f" (timeout: {timeout}s)" if timeout else ""
        spinner_text = Text()
        spinner_text.append(f" {PHASE_ICONS['thinking']} ", style="phase.active")
        spinner_text.append(f"{label}{suffix}", style="phase.active")

        with Live(
            Spinner("dots", text=spinner_text, style="bright_magenta"),
            console=self.console,
            refresh_per_second=10,
            transient=True,
        ):
            yield

    def print_phase(self, phase: str, detail: str = ""):
        """Show a short phase indicator (analyzing, planning, executing)."""
        icon = PHASE_ICONS.get(phase, "•")
        phase_text = Text()
        phase_text.append(f" {icon} ", style="phase.active")
        phase_text.append(phase.capitalize(), style="phase.active")
        if detail:
            phase_text.append(f" — {detail}", style="dim")
        self.console.print(phase_text)

    def print_tool_call(self, tool_name: str, description: str = ""):
        """Display a tool execution indicator."""
        tool_text = Text()
        tool_text.append(f" {TOOL_ICON}  ", style="tool.name")
        tool_text.append(tool_name, style="tool.name")
        if description:
            tool_text.append(f"  {description}", style="tool.status")

        self.console.print(
            Panel(
                tool_text,
                border_style="bright_blue",
                padding=(0, 1),
                expand=False,
            )
        )

    def print_ai_plan(self, plan_text: str):
        """Display intermediate AI reasoning/plan in a subtle box."""
        if not plan_text.strip():
            return

        content = Text(plan_text.strip(), style="ai.reasoning")
        self.console.print(
            Panel(
                content,
                title=f"{PHASE_ICONS['planning']} AI Plan",
                title_align="left",
                border_style="dim",
                padding=(0, 1),
            )
        )

    def print_ai_response(self, response: str, reasoning: str = ""):
        """Display the main AI assistant response."""
        if not response.strip():
            self.console.print(
                Text(f" {AI_ICON}  (no response)", style="dim")
            )
            return

        # Build the response content
        body = Text()
        body.append(response.strip(), style="ai.body")

        panel = Panel(
            body,
            title=f"{AI_ICON} AI Assistant",
            title_align="left",
            border_style="bright_magenta",
            padding=(1, 2),
        )
        self.console.print(panel)

        # Show reasoning as a subtle note if provided
        if reasoning and reasoning.strip():
            reasoning_text = Text()
            reasoning_text.append("  💡 Reasoning: ", style="dim bold")
            reasoning_text.append(reasoning.strip(), style="ai.reasoning")
            self.console.print(reasoning_text)

    def print_confirmation_prompt(self, message: str, prompt: str):
        """Display a confirmation request prominently."""
        content = Text()
        if message.strip():
            content.append(message.strip())
            content.append("\n\n")
        content.append(prompt, style="confirm.prompt")
        content.append("\n")
        content.append("(reply 'yes' to confirm)", style="dim")

        self.console.print(
            Panel(
                content,
                title=f"{PHASE_ICONS['confirming']} Confirmation Required",
                title_align="left",
                border_style="bright_yellow",
                padding=(1, 2),
            )
        )

    def print_error(self, message: str):
        """Display an error message."""
        self.console.print(
            Panel(
                Text(message, style="error"),
                title="Error",
                title_align="left",
                border_style="red",
                padding=(0, 1),
                expand=False,
            )
        )

    def print_help(self):
        """Display styled help information."""
        help_table = Table(
            show_header=True,
            header_style="bold",
            border_style="dim",
            padding=(0, 1),
            expand=False,
        )
        help_table.add_column("Command", style="bold bright_cyan", min_width=12)
        help_table.add_column("Description")

        help_table.add_row("sizing", "Show sizing tier reference")
        help_table.add_row("help", "Show this help")
        help_table.add_row("quit", "Exit the assistant")

        self.console.print()
        self.console.print(help_table)
        self.console.print()

        tips = Text()
        tips.append("  💡 Tips\n", style="bold")
        tips.append(
            '  • Ask naturally: "I need a staging cluster for 20 devs"\n',
            style="dim",
        )
        tips.append("  • AI will inspect host, propose topology, explain trade-offs\n", style="dim")
        tips.append("  • Deployment only happens after your explicit confirmation\n", style="dim")
        self.console.print(tips)

    def print_operation_progress(self, label: str, message: str):
        """Print a progress message during long operations."""
        progress_text = Text()
        progress_text.append(f" {PHASE_ICONS['executing']} ", style="phase.active")
        progress_text.append(f"[{label}] ", style="bold")
        progress_text.append(message, style="dim")
        self.console.print(progress_text)

    def print_separator(self):
        """Print a subtle visual separator between turns."""
        self.console.print(Rule(style="dim"))

    def print_ai_status(self, status: str):
        """Show a brief AI status line (for the agentic loop)."""
        status_text = Text()
        status_text.append(f"  {AI_ICON} ", style="dim")
        status_text.append(status, style="dim italic")
        self.console.print(status_text)
