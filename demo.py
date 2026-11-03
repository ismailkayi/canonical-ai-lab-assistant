#!/usr/bin/env python3
"""MicroCloud demo runner with an AI co-presenter powered by OpenRouter.

Usage:  ./demo.py [command]      (run ./demo.py --help for the full list)

Uses only the Python standard library, so there is nothing to pip install.
"""
import argparse
import collections
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
import webbrowser
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCRIPTS = ROOT / "scripts"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

BOLD, DIM, CYAN, GREEN, RED, RESET = (
    ("\033[1m", "\033[2m", "\033[36m", "\033[32m", "\033[31m", "\033[0m")
    if sys.stdout.isatty()
    else ("",) * 6
)
ANSI = re.compile(r"\x1b\[[0-9;]*m")
# Web UI links as printed by scripts/ui_links.sh: "  <label>   <url>" lines.
URL = re.compile(r"^  [^│\n]*?((?:https?|file)://\S+)[ \t]*$", re.M)


# --------------------------------------------------------------------------
# Settings: demo.conf.example (defaults) overridden by demo.conf, then env
# --------------------------------------------------------------------------
def load_settings() -> dict:
    settings = {}
    for name in ("demo.conf.example", "demo.conf"):
        path = ROOT / name
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            value = re.split(r"\s+#", value, maxsplit=1)[0].strip().strip("'\"")
            settings[key.strip()] = value
    for key in ("OPENROUTER_API_KEY", "OPENROUTER_MODEL"):
        if os.environ.get(key):
            settings[key] = os.environ[key]
    return settings


S = load_settings()
NODES = S.get("NODE_COUNT", "3")
INSTANCES = S.get("INSTANCE_COUNT", "1")
ONE = INSTANCES == "1"


def words(n: str) -> str:
    """'5' -> 'five' for spoken narration; other values are returned unchanged."""
    names = "zero one two three four five six seven eight nine ten eleven twelve".split()
    return names[int(n)] if n.isdigit() and int(n) < len(names) else n


def count(n: str, noun: str) -> str:
    """('1', 'instance') -> '1 instance'; ('3', 'instance') -> '3 instances'."""
    return f"{n} {noun}" if n == "1" else f"{n} {noun}s"


# Wording for the instances, which can be a single one or several.
INST = "an instance" if ONE else f"{words(INSTANCES)} instances"
TITLES = "MC-Instance_1" if ONE else f"MC-Instance_1 ... MC-Instance_{INSTANCES}"


# --------------------------------------------------------------------------
# The four demo steps
# --------------------------------------------------------------------------
STEPS = [
    {
        "title": f"Build a {NODES}-node MicroCloud",
        "script": "step1_build_microcloud.sh",
        "goal": f"Create {NODES} VMs on one host and join them into a single MicroCloud cluster "
        "(LXD for compute, MicroCeph for distributed storage, MicroOVN for networking).",
        "intro": f"We start with {words(NODES)} separate machines. Here they are VMs on one host, but "
        f"they could just as well be {words(NODES)} physical servers. MicroCloud installs LXD, Ceph and "
        "OVN on each of them and joins them into one cluster with a single command, so "
        f"compute, storage and networking are shared across all {words(NODES)}.",
        "outro": f"{words(NODES).capitalize()} machines are now one cloud. Every member shows as ONLINE in the "
        "cluster list, and storage and networking are spread across all of them.",
    },
    {
        "title": f"Pool the resources and provision {count(INSTANCES, 'instance')}",
        "script": "step2_provision_instances.sh",
        "goal": f"Show the {NODES} members' CPU and RAM as one pooled total, then launch "
        f"{count(INSTANCES, 'instance')} on demand without choosing where "
        f"{'it runs; the scheduler places it' if ONE else 'they run; the scheduler places them'}, "
        "and show how much of the pool is now allocated and how much is still free. Each "
        "instance runs on one member, so a single instance is never bigger than one member; "
        "the pool is the capacity all instances share.",
        "intro": "This is the cloud idea: resource pooling and on-demand provisioning. "
        "We stop thinking about individual servers. We see one pool of CPU, memory and "
        f"storage, ask it for {INST}, and MicroCloud decides where "
        f"{'it runs' if ONE else 'each one runs'}. Pooling does not glue the machines into "
        "one giant computer: each instance lives on one member, and the pool is the total "
        "capacity that all instances draw from.",
        "outro": (
            "Our instance came out of the shared pool. We never said where it should run: the "
            "LOCATION column shows the member MicroCloud chose, the allocation table shows "
            "what it took from the pool and what is still free, and its disk lives on Ceph "
            "storage replicated across all members, so no single server is a single point of "
            "failure."
            if ONE else
            f"{words(INSTANCES).capitalize()} instances came out of the shared pool. Look at the "
            "LOCATION column: MicroCloud spread them across different members by itself, and "
            "their disks live on replicated Ceph storage, so no single server is a single point "
            "of failure."
        ),
    },
    {
        "title": f"Register the {'instance' if ONE else 'instances'} in Landscape",
        "script": "step3_register_landscape.sh",
        "goal": f"Attach {'the' if ONE else 'each'} instance to Ubuntu Pro first, then install "
        f"landscape-client and register it with Landscape as {TITLES}, tagged microcloud-demo. "
        "USG is also installed quietly so step 4 runs in seconds.",
        "intro": "New machines need to be managed from day one. Landscape is Canonical's "
        "management tool for Ubuntu: patching, inventory, compliance and scripts for the "
        "whole fleet from one place. "
        + ("Our new instance is first attached to Ubuntu Pro, then registers itself as "
           "MC-Instance_1." if ONE else
           "Each instance is first attached to Ubuntu Pro, then registers itself as "
           "MC-Instance with its number."),
        "outro": ("The instance has registered." if ONE else
                  f"All {words(INSTANCES)} instances have registered.")
        + f" In the Landscape web UI, filter by the tag microcloud-demo to see {TITLES}, "
        "ready for patching and reporting.",
    },
    {
        "title": "Harden an instance with USG (CIS)",
        "script": "step4_usg_harden.sh",
        "goal": "On one instance (attached to Ubuntu Pro in step 3): enable and install the "
        "Ubuntu Security Guide (USG), "
        "audit against CIS Level 1 Server, fix exactly one rule, re-check it and compare the "
        "pass rate.",
        "intro": "Last, compliance. The Ubuntu Security Guide, part of Ubuntu Pro, checks a "
        "machine against the CIS benchmark and can fix what it finds. We take a baseline "
        "score, apply one CIS rule, and measure again.",
        "outro": "The pass rate went up because one failing rule now passes. USG fixed it and "
        "proved the fix with a fresh check. Applied to the full profile, the same tool "
        "hardens a whole fleet, and the HTML reports are evidence for auditors.",
    },
]


# --------------------------------------------------------------------------
# AI co-presenter
# --------------------------------------------------------------------------
class CoPresenter:
    """Talks to an LLM through OpenRouter; falls back to built-in text if unavailable."""

    def __init__(self, enabled: bool = True):
        self.key = S.get("OPENROUTER_API_KEY", "")
        self.model = S.get("OPENROUTER_MODEL") or "anthropic/claude-sonnet-4.5"
        self.enabled = enabled and bool(self.key)
        self.history: list = []
        self.system = (
            "You are the AI co-presenter in a live Canonical technical demo shown to an "
            "audience. The demo: (1) create "
            f"{NODES} VMs and join them into one MicroCloud cluster "
            "(LXD, MicroCeph, MicroOVN); (2) show the members' resources as one pool and "
            f"provision {count(INSTANCES, 'instance')} that the cluster scheduler places on "
            "members by itself (resource pooling and on-demand provisioning); (3) attach "
            f"{'it' if ONE else 'them'} to Ubuntu Pro and register {'it' if ONE else 'them'} with Landscape as {TITLES}; (4) audit one instance with the "
            "Ubuntu Security Guide (USG, Ubuntu Pro) against CIS Level 1, fix one rule and "
            "show the higher pass rate.\n"
            "Facts to get right: resource pooling means the members' capacity is managed and "
            "consumed as one total. An instance always runs on exactly one member, so a single "
            "instance can never be larger than one member (LXD does not merge machines into one "
            "big computer); the pool is the total that all instances share, and the scheduler "
            "places each new instance where there is room. If an instance needs more, it can "
            "be resized up to its member's size, live-migrated, or the cluster grows by adding "
            "members.\n"
            "Web UIs: MicroCloud's web UI is the LXD UI on port 8443 of any member; Landscape has "
            "its own web UI; USG has no web UI, its results are HTML reports, and Pro "
            "subscriptions are visible in the Ubuntu Pro dashboard. './demo.py ui' lists the "
            "links.\n"
            "Style: speak to the audience, plainly and confidently, in 2-5 short sentences. "
            "Plain text only: no markdown, no bullet lists, no headings. Never invent numbers; "
            "use only numbers that appear in the command output you are given. If you are "
            "asked a question, answer it accurately and briefly; if you do not know, say so."
        )

    def ask(self, prompt: str, fallback: str = "") -> str:
        if not self.enabled:
            return fallback or "(The AI is off. Set OPENROUTER_API_KEY in demo.conf to enable it.)"
        messages = [{"role": "system", "content": self.system}]
        messages += self.history[-12:]
        messages.append({"role": "user", "content": prompt})
        try:
            answer = self._complete(messages)
        except KeyboardInterrupt:  # Ctrl+C while the AI is answering: skip the answer
            print(f"{DIM}(AI answer skipped){RESET}")
            return fallback or ""
        except Exception as exc:  # network down, bad key, unknown model...
            print(f"{DIM}(AI unavailable: {exc}. Using built-in narration.){RESET}")
            return fallback or "(The AI could not be reached.)"
        self.history += [{"role": "user", "content": prompt}, {"role": "assistant", "content": answer}]
        return answer

    def _complete(self, messages: list) -> str:
        body = {"model": self.model, "messages": messages, "max_tokens": 500, "temperature": 0.4}
        request = urllib.request.Request(
            OPENROUTER_URL,
            data=json.dumps(body).encode(),
            headers={
                "Authorization": f"Bearer {self.key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/saif-farid-EMEA/MicroCloud_AI_Assistant",
                "X-Title": "MicroCloud Demo",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                data = json.load(response)
        except urllib.error.HTTPError as err:
            detail = err.read().decode(errors="replace")
            try:
                detail = json.loads(detail)["error"]["message"]
            except (ValueError, KeyError, TypeError):
                pass
            raise RuntimeError(f"HTTP {err.code}: {detail}") from None
        return data["choices"][0]["message"]["content"].strip()


def say(text: str) -> None:
    width = min(shutil.get_terminal_size((100, 20)).columns, 100) - 6
    print()
    for i, line in enumerate(textwrap.wrap(text, width) or [""]):
        prefix = f"{CYAN}{BOLD}AI ▸{RESET} " if i == 0 else "     "
        print(f"{prefix}{CYAN}{line}{RESET}")
    print()


# --------------------------------------------------------------------------
# Running steps
# --------------------------------------------------------------------------
def run_script(name: str, *args: str) -> tuple:
    """Run a script, stream its output live, and keep the tail for the AI."""
    tail = collections.deque(maxlen=150)
    proc = subprocess.Popen(
        ["bash", str(SCRIPTS / name), *args],
        # Steps never need keyboard input. Without this, commands such as
        # "lxc launch" run through "lxc exec" see a pipe instead of a terminal
        # and wait forever for optional YAML on stdin.
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    try:
        for line in proc.stdout:
            sys.stdout.write(line)
            tail.append(ANSI.sub("", line))
        proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        proc.wait()
        print(f"\n{RED}Interrupted.{RESET}")
    return proc.returncode == 0, "".join(tail)


def open_links(output: str) -> None:
    """Open every web UI link that a step printed in the default browser."""
    links = list(dict.fromkeys(URL.findall(output)))
    if not links:
        print("No web UI links in this step's output.")
        return
    for link in links:
        print(f"{DIM}Opening {link}{RESET}")
        try:
            webbrowser.open_new_tab(link)
        except Exception as exc:  # no browser on a headless host
            print(f"{RED}Could not open a browser ({exc}). Copy the link instead.{RESET}")
            return


def prompt(choices: str) -> str:
    try:
        return input(f"{BOLD}{choices}{RESET}\n> ").strip()
    except EOFError:
        return "q"


def banner(number: int, step: dict) -> None:
    line = "━" * 70
    print(f"\n{GREEN}{line}{RESET}")
    print(f"{GREEN}{BOLD}  STEP {number} of {len(STEPS)}:  {step['title']}{RESET}")
    print(f"{GREEN}{line}{RESET}")


def do_step(number: int, ai: CoPresenter, interactive: bool = True) -> bool:
    """Present one step. Returns False if the presenter chose to quit."""
    step = STEPS[number - 1]
    banner(number, step)
    say(ai.ask(f"We are about to start step {number}: {step['goal']} Introduce it to the "
               "audience: what we will do and why it matters.", step["intro"]))

    while interactive:
        choice = prompt("[Enter] run this step   [s] skip   [q] quit   or type a question for the AI")
        if choice == "":
            break
        if choice.lower() == "s":
            return True
        if choice.lower() == "q":
            return False
        say(ai.ask(choice))

    while True:
        success, output = run_script(step["script"])
        if success:
            say(ai.ask(f"Step {number} finished successfully. Here is the end of the output:\n"
                       f"{output}\nExplain to the audience what this output proves, pointing "
                       "at the concrete names and numbers in it.", step["outro"]))
            options = "[Enter] next step   [r] run again   [q] quit   or type a question"
            if URL.search(output):
                options = "[Enter] next step   [o] open the web UIs   [r] run again   [q] quit   or type a question"
        else:
            print(f"\n{RED}{BOLD}Step {number} failed.{RESET} Full logs are in the logs/ folder.")
            say(ai.ask(f"Step {number} FAILED. End of the output:\n{output}\nSpeaking to the "
                       "presenter (not the audience), give the most likely cause and the fix.",
                       "Read the last error message above, fix the cause, then press r to retry. "
                       "Every step is safe to run again."))
            options = "[r] retry   [s] skip to next step   [q] quit   or type a question"
        if not interactive:
            return success
        while True:
            choice = prompt(options)
            if choice.lower() == "r":
                break
            if choice.lower() == "q":
                return False
            if choice.lower() == "o":
                open_links(output)
                continue
            if choice == "" and success or choice.lower() == "s":
                return True
            if choice:
                say(ai.ask(choice))


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------
def cmd_run(args, ai):
    print(f"{BOLD}MicroCloud demo{RESET}  —  AI co-presenter: "
          f"{ai.model if ai.enabled else 'off (built-in narration)'}")
    for number in range(args.start, len(STEPS) + 1):
        if not do_step(number, ai):
            print("Demo stopped. Resume any time with: ./demo.py run --from <step>")
            return 0
    say(ai.ask("The demo is complete. Give a short closing summary of all four steps for the "
               "audience and invite questions.",
               f"That's the full story: {words(NODES)} machines became one cloud, we provisioned "
               f"{'an instance' if ONE else 'instances'} "
               "from the shared pool, managed them centrally with Landscape, and measurably "
               "hardened one with USG. Questions?"))
    chat_loop(ai)
    return 0


def cmd_step(args, ai):
    if not 1 <= args.number <= len(STEPS):
        sys.exit(f"Step must be 1-{len(STEPS)}")
    return 0 if do_step(args.number, ai, interactive=not args.yes) else 1


def chat_loop(ai):
    print(f"{DIM}Ask the AI anything (empty line or 'q' to leave).{RESET}")
    while True:
        question = prompt("Question")
        if question.lower() in {"", "q", "quit", "exit"}:
            return 0
        say(ai.ask(question))


def cmd_ask(args, ai):
    if args.question:
        say(ai.ask(" ".join(args.question)))
        return 0
    return chat_loop(ai)


def cmd_ui(args, ai):
    ok, output = run_script("ui_links.sh", args.which)
    if args.open:
        open_links(output)
    elif URL.search(output) and sys.stdin.isatty():
        if prompt("[o] open them in the browser   [Enter] done").lower() == "o":
            open_links(output)
    return 0 if ok else 1


def cmd_check(args, ai):
    ok, _ = run_script("check_host.sh")
    print(f"\n{BOLD}AI co-presenter{RESET}")
    if not ai.key:
        print("!  OPENROUTER_API_KEY not set — built-in narration will be used")
    else:
        try:
            reply = ai._complete([{"role": "user", "content": "Reply with the single word: ready"}])
            print(f"{GREEN}✔{RESET}  OpenRouter reachable, model {ai.model} replied: {reply[:40]}")
        except Exception as exc:
            print(f"{RED}✘{RESET}  OpenRouter call failed: {exc}")
            ok = False
    return 0 if ok else 1


def main():
    parser = argparse.ArgumentParser(
        description="MicroCloud scripted demo with an AI co-presenter (OpenRouter).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            typical flow:
              ./demo.py setup          once: install LXD on this machine
              ./demo.py check          verify the host, settings and AI
              ./demo.py step 1 --yes   before the audience arrives (slow part)
              ./demo.py run            present all four steps
              ./demo.py ui             links to the MicroCloud, LXD, Landscape and USG UIs
              ./demo.py cleanup        afterwards: delete everything
        """),
    )
    parser.add_argument("--no-ai", action="store_true", help="use built-in narration only")
    sub = parser.add_subparsers(dest="command")
    run = sub.add_parser("run", help="present all steps interactively (default)")
    run.add_argument("--from", dest="start", type=int, default=1, help="start at this step")
    step = sub.add_parser("step", help="run a single step (1-4)")
    step.add_argument("number", type=int)
    step.add_argument("--yes", action="store_true", help="no pauses (for preparing beforehand)")
    ask = sub.add_parser("ask", help="ask the AI a question (no question = chat mode)")
    ask.add_argument("question", nargs="*")
    ui = sub.add_parser("ui", help="show (and open) the MicroCloud/LXD, Landscape and USG web UIs")
    ui.add_argument("which", nargs="?", default="all", choices=["all", "microcloud", "landscape", "usg"])
    ui.add_argument("--open", action="store_true", help="open the links in the browser right away")
    sub.add_parser("check", help="pre-flight checks for host, settings and AI")
    sub.add_parser("setup", help="one-time host preparation (installs LXD)")
    sub.add_parser("status", help="show the cluster and instances")
    sub.add_parser("cleanup", help="delete everything the demo created")
    args = parser.parse_args()

    ai = CoPresenter(enabled=not args.no_ai)
    command = args.command or "run"
    if command == "run" and not args.command:
        args.start = 1
    handlers = {"run": cmd_run, "step": cmd_step, "ask": cmd_ask, "check": cmd_check, "ui": cmd_ui}
    if command in handlers:
        return handlers[command](args, ai)
    script = {"setup": "setup_host.sh", "status": "status.sh", "cleanup": "cleanup.sh"}[command]
    return subprocess.call(["bash", str(SCRIPTS / script)])


if __name__ == "__main__":
    sys.exit(main())
