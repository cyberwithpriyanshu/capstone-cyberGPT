# app/knowledge/models.py
#
# PURPOSE: Defines what ONE piece of knowledge looks like. We call a piece a "chunk": a small
# passage (one CVE entry, one ATT&CK technique, one section of an OWASP cheat sheet) plus
# the information about where it came from.
#
# WHY SMALL CHUNKS: When someone asks a question, we will later hand the AI only the few
# chunks that match, not whole books. Small, focused chunks find better matches and keep
# the prompt short. And because EVERY chunk remembers its source, URL and license, CyberGPT
# can say "this came from CISA KEV" instead of "trust me".

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# ---- Source tiers: how much we trust a kind of source ------------------------------------
# When two sources disagree, the lower number wins.
TIER_AUTHORITATIVE = 1  # official databases and standards (CISA, MITRE)
TIER_COMMUNITY = 2      # respected community guidance (OWASP)
TIER_OTHER = 3          # vendor blogs, write-ups, etc. (not used yet)

TIER_NAMES = {
    TIER_AUTHORITATIVE: "authoritative",
    TIER_COMMUNITY: "community",
    TIER_OTHER: "other",
}

# ---- Domains: the topic areas CyberGPT covers --------------------------------------------
# A chunk can belong to several. They let us search inside one area ("only cloud").
DOMAINS = (
    "web-app",         # web application security
    "network",         # network scanning, TLS, segmentation
    "vuln-intel",      # vulnerabilities that are known and exploited
    "defense",         # how to defend / harden / fix
    "cloud",           # cloud, containers, CI/CD, identity
    "malware",         # malware families and analysis context
    "threat-behavior", # what attackers do (MITRE ATT&CK)
)


@dataclass(frozen=True)
class Chunk:
    id: str                 # unique, e.g. "kev:CVE-2021-44228" or "attack:T1059.001"
    source: str             # short source name: "kev", "attack" or "owasp"
    tier: int               # one of the TIER_ numbers above
    title: str              # a human-readable heading
    text: str               # the passage itself
    url: str                # where the original lives (for citations)
    license: str            # the source's license / terms (we must credit sources)
    domains: tuple[str, ...]  # which topic areas this belongs to
    as_of: str              # when the SOURCE says this was published or updated
    ref_id: str = ""        # an official identifier, e.g. "CVE-2021-44228", "T1059", "S0061"
    meta: dict[str, Any] = field(default_factory=dict)  # extra structured facts

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["domains"] = list(self.domains)  # JSON has lists, not tuples
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Chunk":
        return cls(
            id=data["id"],
            source=data["source"],
            tier=int(data["tier"]),
            title=data["title"],
            text=data["text"],
            url=data.get("url", ""),
            license=data.get("license", ""),
            domains=tuple(data.get("domains", ())),
            as_of=data.get("as_of", ""),
            ref_id=data.get("ref_id", ""),
            meta=dict(data.get("meta", {})),
        )