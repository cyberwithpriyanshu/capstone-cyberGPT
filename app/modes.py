# app/modes.py
#
# PURPOSE: Defines CyberGPT's "modes". A mode changes HOW the assistant answers, so the
# same app can act as a teacher, an analyst, a defender, or a report writer.
#
# HOW IT WORKS: each mode is just some extra instructions that we add to the end of the
# base system prompt (app/prompts.py). The base safety rules are ALWAYS included first,
# so no mode can switch them off.
#
# WHY IT IS SAFE TO KEEP THIS IN ONE FILE: the web page asks the server for the list of
# modes (see /modes in routes.py), so when you add or edit a mode here, the UI updates
# itself. You never have to edit the UI to add a mode.

from __future__ import annotations

from dataclasses import dataclass

from app.prompts import SYSTEM_PROMPT


@dataclass(frozen=True)
class Mode:
    id: str  # short machine name, e.g. "analyze" (this is what the UI sends)
    label: str  # name shown on the button
    tagline: str  # one line describing the mode, shown in the UI
    placeholder: str  # grey hint text inside the message box
    instructions: str  # the extra rules given to the AI (never shown to the user)
    suggestions: tuple[str, ...]  # example questions shown as clickable cards


# The order here is the order the buttons appear in the UI.
_ALL_MODES = (
    Mode(
        id="learn",
        label="Learn",
        tagline="Understand a concept from the ground up",
        placeholder="Ask anything about cybersecurity...",
        instructions=(
            "Mode: Learning. The user is learning. Explain from first principles in plain "
            "language, define jargon the first time you use it, and include one concrete "
            "analogy or small example. Explain how an attack works at a conceptual level and "
            "give equal attention to how to prevent and detect it. Do not provide weaponized "
            "exploit code. End with one short question the learner can use to check their "
            "understanding."
        ),
        suggestions=(
            "Explain SQL injection like I'm a beginner",
            "What is the difference between authentication and authorization?",
            "How does TLS protect a website?",
            "What is the OWASP Top 10 and why does it matter?",
        ),
    ),
    Mode(
        id="analyze",
        label="Analyze",
        tagline="Interpret scan output, requests and findings",
        placeholder="Paste Nmap output, an HTTP request, or a finding to analyze...",
        instructions=(
            "Mode: Analyst. The user will paste evidence such as scan output, HTTP requests, "
            "logs or tool findings. Work only from what they actually provided. Structure "
            "your answer with these headings: Observations (facts visible in the evidence), "
            "Interpretation (what they might mean, clearly labelled as interpretation), "
            "Unknowns (what is missing), Confidence (High, Medium or Low, with one sentence "
            "of reasoning), and Next validation steps (to perform only within authorized "
            "scope). Never say a vulnerability is confirmed unless the evidence proves it; "
            "say 'insufficient evidence' instead."
        ),
        suggestions=(
            "Analyze this Nmap result: 22/tcp open ssh, 80/tcp open http, 443/tcp open https",
            "I found POST /login with username and password parameters. Is it potentially vulnerable?",
            "What evidence would I need to confirm an outdated TLS configuration?",
            "Which should I fix first: weak TLS, open SSH, or missing security headers?",
        ),
    ),
    Mode(
        id="pentest",
        label="Pentest",
        tagline="Work through findings from an authorized assessment",
        placeholder="Describe a finding from your authorized assessment or lab...",
        instructions=(
            "Mode: Authorized assessment. Assume the user is working on a scoped, authorized "
            "engagement or on their own lab. Help them understand findings, judge impact and "
            "likelihood, decide what to validate next, and plan remediation. If scope or "
            "authorization is unclear for a request, ask about it first. Describe validation "
            "approaches conceptually and defensively. Do not write ready-to-run exploit code "
            "or step-by-step intrusion instructions aimed at a specific target."
        ),
        suggestions=(
            "Help me understand this authorized finding: reflected XSS in a search field on my lab app",
            "How do I assess the impact of an exposed admin panel?",
            "What should I document before I start testing a web application?",
            "How should I plan remediation retesting?",
        ),
    ),
    Mode(
        id="defend",
        label="Defend",
        tagline="Detect, investigate and harden",
        placeholder="Ask how to detect, investigate or prevent something...",
        instructions=(
            "Mode: Defender. Help the user detect, investigate and prevent. Suggest which "
            "log sources to check, detection ideas (what to look for and why), hardening "
            "steps, and how to verify each control actually works. Mention MITRE ATT&CK "
            "technique IDs only when you are confident they are correct; otherwise describe "
            "the behavior without an ID. Put the highest-impact steps first."
        ),
        suggestions=(
            "How can I detect brute-force attempts against SSH?",
            "Which logs matter most when investigating a suspected phishing compromise?",
            "How do I harden an nginx web server?",
            "What does a good incident response checklist look like?",
        ),
    ),
    Mode(
        id="report",
        label="Report",
        tagline="Turn findings into professional write-ups",
        placeholder="Describe the finding you want written up...",
        instructions=(
            "Mode: Report writer. Produce professional, ready-to-paste security findings. "
            "Use these sections: Title, Severity (only if the evidence supports one, with "
            "the reasoning), Affected assets, Description, Evidence, Impact, Remediation, "
            "References. Use only facts the user provided. Write 'Not provided' for anything "
            "missing instead of inventing details. Never invent CVE IDs, CVSS scores or "
            "references."
        ),
        suggestions=(
            "Write a finding for missing HTTP security headers on a lab web app",
            "Draft an executive summary for a scan that found 2 high and 5 medium issues",
            "Write a remediation plan for outdated TLS settings",
            "What sections belong in a vulnerability report?",
        ),
    ),
)

# A dictionary for quick lookup by id:  MODES["analyze"]
MODES: dict[str, Mode] = {m.id: m for m in _ALL_MODES}

# The mode used when the user does not pick one.
DEFAULT_MODE = "learn"


def get_mode(mode_id: str) -> Mode:
    try:
        return MODES[mode_id]
    except KeyError:
        raise ValueError(f"Unknown mode: {mode_id}") from None


def build_system_prompt(mode_id: str) -> str:
    """Base safety rules first, then the extra instructions for the chosen mode."""
    mode = get_mode(mode_id)
    return f"{SYSTEM_PROMPT}\n{mode.instructions}\n"