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
NODES = S.get("NODE_COUNT", "5")
INSTANCES = S.get("INSTANCE_COUNT", "3")


def words(n: str) -> str:
    """'5' -> 'five' for spoken narration; other values are returned unchanged."""
    names = "zero one two three four five six seven eight nine ten eleven twelve".split()
    return names[int(n)] if n.isdigit() and int(n) < len(names) else n


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
        "title": f"Pool the resources and provision {INSTANCES} instances",
        "script": "step2_provision_instances.sh",
        "goal": f"Show the {NODES} members' CPU and RAM as one pooled total, then launch {INSTANCES} "
        "instances on demand without choosing where they run; the scheduler places them.",
        "intro": "This is the cloud idea: resource pooling and on-demand provisioning. "
        "We stop thinking about individual servers. We see one pool of CPU, memory and "
        f"storage, ask it for {words(INSTANCES)} instances, and MicroCloud decides where each one runs.",
        "outro": f"{words(INSTANCES).capitalize()} instances came out of the shared pool. Look at the LOCATION column: "
        "MicroCloud spread them across different members by itself, and their disks live on "
        "replicated Ceph storage, so no single server is a single point of failure.",
    },
    {
        "title": "Register the instances in Landscape",
        "script": "step3_register_landscape.sh",
        "goal": "Install landscape-client in each instance and register it with Landscape as "
        f"MC-Instance_1 ... MC-Instance_{INSTANCES}, tagged microcloud-demo.",
        "intro": "New machines need to be managed from day one. Landscape is Canonical's "
        "management tool for Ubuntu: patching, inventory, compliance and scripts for the "
        "whole fleet from one place. Each instance now registers itself as MC-Instance "
        "with its number.",
        "outro": f"All {words(INSTANCES)} instances have registered. In the Landscape web UI, filter by the "
        f"tag microcloud-demo to see MC-Instance_1 to MC-Instance_{INSTANCES}, ready for patching and "
        "reporting.",
    },
    {
        "title": "Harden an instance with USG (CIS)",
        "script": "step4_usg_harden.sh",
        "goal": "On one instance: attach Ubuntu Pro, install the Ubuntu Security Guide (USG), "
        "audit against CIS Level 1 Server, fix exactly one rule, re-audit and compare scores.",
        "intro": "Last, compliance. The Ubuntu Security Guide, part of Ubuntu Pro, checks a "
        "machine against the CIS benchmark and can fix what it finds. We take a baseline "
        "score, apply one CIS rule, and measure again.",
        "outro": "The score went up because one failing rule now passes. USG fixed it and "
        "proved the fix with a fresh audit. Applied to the full profile, the same tool "
        "hardens a whole fleet in minutes, and the HTML reports are evidence for auditors.",
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
            f"provision {INSTANCES} instances that the scheduler spreads "
            "across members (resource pooling and on-demand provisioning); (3) register the "
            "instances with Landscape as MC-Instance_1..N; (4) audit one instance with the "
            "Ubuntu Security Guide (USG, Ubuntu Pro) against CIS Level 1, fix one rule and "
            "show the higher score.\n"
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
               f"That's the full story: {words(NODES)} machines became one cloud, we provisioned instances "
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
    sub.add_parser("check", help="pre-flight checks for host, settings and AI")
    sub.add_parser("setup", help="one-time host preparation (installs LXD)")
    sub.add_parser("status", help="show the cluster and instances")
    sub.add_parser("cleanup", help="delete everything the demo created")
    args = parser.parse_args()

    ai = CoPresenter(enabled=not args.no_ai)
    command = args.command or "run"
    if command == "run" and not args.command:
        args.start = 1
    handlers = {"run": cmd_run, "step": cmd_step, "ask": cmd_ask, "check": cmd_check}
    if command in handlers:
        return handlers[command](args, ai)
    script = {"setup": "setup_host.sh", "status": "status.sh", "cleanup": "cleanup.sh"}[command]
    return subprocess.call(["bash", str(SCRIPTS / script)])


if __name__ == "__main__":
    sys.exit(main())
