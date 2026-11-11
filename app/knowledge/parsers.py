# app/knowledge/parsers.py
#
# PURPOSE: Turns each source's raw download into our standard "chunks".
#
# DESIGN CHOICE: every function here is PURE: it takes data in and returns chunks out. It
# never downloads anything and never touches files. That makes it easy to test with tiny
# hand-written samples (see tests/test_knowledge_parsers.py), and the download logic lives
# separately in scripts/ingest_knowledge.py.
#
# THREE SOURCES (all free, and all on GitHub so the download is reliable):
#   KEV    CISA's list of vulnerabilities that attackers are ACTUALLY exploiting   (tier 1)
#   ATT&CK MITRE's catalogue of attacker techniques, malware, tools and mitigations (tier 1)
#   OWASP  the OWASP Cheat Sheet Series: practical secure-coding and defense advice (tier 2)

from __future__ import annotations

import re
from typing import Any, Iterable

from app.knowledge.models import TIER_AUTHORITATIVE, TIER_COMMUNITY, Chunk

KEV_LICENSE = "CC0 1.0 (CISA Known Exploited Vulnerabilities catalog)"
ATTACK_LICENSE = (
    "(c) The MITRE Corporation. ATT&CK is reproduced with permission; "
    "see https://attack.mitre.org/resources/terms-of-use/"
)
OWASP_LICENSE = "CC BY-SA 4.0 (OWASP Cheat Sheet Series)"

# Chunks longer than this are split, so each one stays focused and prompts stay short.
MAX_CHUNK_CHARS = 1800


# =========================================================================================
# Small text helpers
# =========================================================================================

def _squash(text: str) -> str:
    """Collapses runs of blank lines and trims the ends."""
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def split_text(text: str, limit: int = MAX_CHUNK_CHARS) -> list[str]:
    """Splits long text into pieces of at most ~`limit` characters, breaking BETWEEN
    paragraphs where possible (never in the middle of a sentence unless a single paragraph
    is itself longer than the limit)."""
    text = _squash(text)
    if len(text) <= limit:
        return [text] if text else []

    pieces: list[str] = []
    current = ""
    for paragraph in re.split(r"\n\s*\n", text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        # A single huge paragraph: cut it at sentence ends.
        while len(paragraph) > limit:
            cut = paragraph.rfind(". ", 0, limit)
            cut = cut + 1 if cut > limit // 3 else limit
            if current:
                pieces.append(current)
                current = ""
            pieces.append(paragraph[:cut].strip())
            paragraph = paragraph[cut:].strip()
        if current and len(current) + len(paragraph) + 2 > limit:
            pieces.append(current)
            current = paragraph
        else:
            current = f"{current}\n\n{paragraph}" if current else paragraph
    if current:
        pieces.append(current)
    return pieces


def clean_attack_text(text: str) -> str:
    """ATT&CK descriptions contain markup meant for their website. Make it plain text:
         "(Citation: Foo 2019)"        -> removed
         "[PowerShell](https://...)"   -> "PowerShell"
         "<code>ls -la</code>"         -> "`ls -la`"
         other <tags>                  -> removed
    """
    text = re.sub(r"\s*\(Citation:[^)]*\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\((?:[^)]+)\)", r"\1", text)
    text = re.sub(r"</?code>", "`", text)
    text = re.sub(r"<[^>]+>", "", text)
    return _squash(text)


# =========================================================================================
# 1) CISA KEV  (Known Exploited Vulnerabilities)
# =========================================================================================

def parse_kev(data: dict[str, Any]) -> list[Chunk]:
    """One chunk per vulnerability. Being in this list means CISA has evidence it is being
    exploited in real attacks, which is exactly what prioritising fixes needs."""
    chunks: list[Chunk] = []
    catalog_version = data.get("catalogVersion", "")

    for v in data.get("vulnerabilities", []):
        cve = (v.get("cveID") or "").strip()
        if not cve:
            continue  # skip a malformed entry instead of inventing an ID

        cwes = list(v.get("cwes") or [])
        lines = [
            f"{cve}: {v.get('vulnerabilityName', '').strip()}",
            f"Vendor / product: {v.get('vendorProject', '').strip()} / {v.get('product', '').strip()}",
            f"Added to the CISA KEV catalog: {v.get('dateAdded', 'unknown')} "
            f"(remediation due date: {v.get('dueDate', 'unknown')})",
        ]
        # Only say it when it is "Known". If we wrote "Unknown" on every entry, the word
        # "ransomware" would appear in all 1,700+ chunks and a search for ransomware would
        # return random results. (The value is still kept in `meta` below.)
        if v.get("knownRansomwareCampaignUse", "").lower() == "known":
            lines.append("Known to be used in ransomware campaigns: yes")
        if cwes:
            lines.append(f"Weakness type (CWE): {', '.join(cwes)}")
        lines.append(f"Description: {v.get('shortDescription', '').strip()}")
        lines.append(f"Required action: {v.get('requiredAction', '').strip()}")
        if v.get("notes"):
            lines.append(f"Notes: {v['notes'].strip()}")

        chunks.append(
            Chunk(
                id=f"kev:{cve}",
                source="kev",
                tier=TIER_AUTHORITATIVE,
                title=f"{cve} - {v.get('vulnerabilityName', '').strip()}",
                text="\n".join(lines),
                url=f"https://nvd.nist.gov/vuln/detail/{cve}",
                license=KEV_LICENSE,
                domains=("vuln-intel",),
                as_of=v.get("dateAdded", ""),
                ref_id=cve,
                meta={
                    "in_kev": True,
                    "date_added": v.get("dateAdded", ""),
                    "due_date": v.get("dueDate", ""),
                    "ransomware": v.get("knownRansomwareCampaignUse", "Unknown"),
                    "cwes": cwes,
                    "vendor": v.get("vendorProject", ""),
                    "product": v.get("product", ""),
                    "catalog_version": catalog_version,
                },
            )
        )
    return chunks


# =========================================================================================
# 2) MITRE ATT&CK  (techniques, malware, tools, mitigations)
# =========================================================================================

# Platforms that mean "this is about cloud / SaaS / identity / container environments".
CLOUD_PLATFORMS = {"IaaS", "SaaS", "Office Suite", "Office 365", "Identity Provider",
                   "Containers", "Azure AD", "Google Workspace"}


def _attack_ref(obj: dict[str, Any]) -> tuple[str, str]:
    """Returns (official ID like 'T1059.001', web address) for an ATT&CK object."""
    for ref in obj.get("external_references", []):
        if ref.get("source_name") == "mitre-attack":
            return ref.get("external_id", ""), ref.get("url", "")
    return "", ""


def _is_live(obj: dict[str, Any]) -> bool:
    """Skips entries MITRE has retired (revoked = replaced by another, deprecated = dropped)."""
    return not obj.get("revoked") and not obj.get("x_mitre_deprecated")


def _date_only(timestamp: str) -> str:
    return (timestamp or "")[:10]


def parse_attack(bundle: dict[str, Any]) -> list[Chunk]:
    """Builds chunks from MITRE's STIX bundle (a big JSON file of linked objects).

    - technique chunk:  description, tactics, platforms, and which mitigations apply
    - software chunk:   malware / tool description and the techniques it is known to use
    - mitigation chunk: advice, and which techniques it helps against
    """
    objects = bundle.get("objects", [])
    by_id = {o["id"]: o for o in objects if "id" in o}

    # Gather relationships once: "X uses Y" and "X mitigates Y".
    uses: dict[str, list[str]] = {}        # software id   -> technique ids
    mitigates: dict[str, list[str]] = {}   # technique id  -> mitigation ids
    mitigation_targets: dict[str, list[str]] = {}  # mitigation id -> technique ids
    for rel in objects:
        if rel.get("type") != "relationship" or rel.get("revoked") or rel.get("x_mitre_deprecated"):
            continue
        source, target, kind = rel.get("source_ref", ""), rel.get("target_ref", ""), rel.get("relationship_type")
        if kind == "uses" and source.startswith(("malware--", "tool--")) and target.startswith("attack-pattern--"):
            uses.setdefault(source, []).append(target)
        elif kind == "mitigates" and source.startswith("course-of-action--") and target.startswith("attack-pattern--"):
            mitigates.setdefault(target, []).append(source)
            mitigation_targets.setdefault(source, []).append(target)

    def label(stix_id: str) -> str:
        """'T1059.001 PowerShell' for a technique, or 'M1038 Execution Prevention'."""
        obj = by_id.get(stix_id)
        if not obj or not _is_live(obj):
            return ""
        ref_id, _ = _attack_ref(obj)
        return f"{ref_id} {obj.get('name', '')}".strip() if ref_id else ""

    def labels(ids: Iterable[str], limit: int = 25) -> list[str]:
        out = sorted({label(i) for i in ids} - {""})
        return out[:limit]

    chunks: list[Chunk] = []

    for obj in objects:
        kind = obj.get("type")
        if kind not in ("attack-pattern", "malware", "tool", "course-of-action") or not _is_live(obj):
            continue

        ref_id, url = _attack_ref(obj)
        name = obj.get("name", "").strip()
        description = clean_attack_text(obj.get("description", ""))
        if not ref_id or not name or not description:
            continue  # without an official ID or a description there is nothing reliable to cite

        platforms = obj.get("x_mitre_platforms", []) or []
        is_cloud = bool(CLOUD_PLATFORMS & set(platforms))
        as_of = _date_only(obj.get("modified", ""))
        meta: dict[str, Any] = {"platforms": platforms}
        header: list[str]
        domains: list[str]

        if kind == "attack-pattern":
            tactics = [p["phase_name"] for p in obj.get("kill_chain_phases", [])]
            is_sub = bool(obj.get("x_mitre_is_subtechnique"))
            header = [
                f"{ref_id}: {name} (MITRE ATT&CK {'sub-technique' if is_sub else 'technique'})",
                f"Tactics: {', '.join(tactics) or 'n/a'}",
                f"Platforms: {', '.join(platforms) or 'n/a'}",
            ]
            mits = labels(mitigates.get(obj["id"], []))
            tail = [f"Mitigations: {'; '.join(mits)}"] if mits else []
            domains = ["threat-behavior"] + (["cloud"] if is_cloud else [])
            meta.update({"kind": "technique", "tactics": tactics, "mitigations": mits})

        elif kind in ("malware", "tool"):
            aliases = [a for a in obj.get("x_mitre_aliases", []) if a != name]
            header = [
                f"{ref_id}: {name} (MITRE ATT&CK {'malware' if kind == 'malware' else 'tool'})",
                f"Also known as: {', '.join(aliases) or 'n/a'}",
                f"Platforms: {', '.join(platforms) or 'n/a'}",
            ]
            techniques = labels(uses.get(obj["id"], []), limit=40)
            tail = [f"Techniques it is known to use: {'; '.join(techniques)}"] if techniques else []
            domains = (["malware", "threat-behavior"] if kind == "malware" else ["threat-behavior"]) + (
                ["cloud"] if is_cloud else []
            )
            meta.update({"kind": kind, "aliases": aliases, "techniques": techniques})

        else:  # course-of-action = mitigation
            header = [f"{ref_id}: {name} (MITRE ATT&CK mitigation)"]
            techniques = labels(mitigation_targets.get(obj["id"], []), limit=40)
            tail = [f"Helps against: {'; '.join(techniques)}"] if techniques else []
            domains = ["defense", "threat-behavior"]
            meta.update({"kind": "mitigation", "techniques": techniques})

        body = "\n".join(header) + "\n\n" + description
        pieces = split_text(body)
        if tail:
            tail_text = "\n".join(tail)
            if len(pieces[-1]) + len(tail_text) + 2 <= MAX_CHUNK_CHARS * 1.2:
                pieces[-1] += "\n\n" + tail_text  # fits: keep everything about this item together
            else:
                # Too long to merge: its own final piece, so the list is never cut in half.
                pieces.append(f"{ref_id}: {name}\n{tail_text}")

        for index, piece in enumerate(pieces, start=1):
            chunks.append(
                Chunk(
                    id=f"attack:{ref_id}" + (f"#{index}" if len(pieces) > 1 else ""),
                    source="attack",
                    tier=TIER_AUTHORITATIVE,
                    title=f"{ref_id} {name}",
                    text=piece,
                    url=url,
                    license=ATTACK_LICENSE,
                    domains=tuple(domains),
                    as_of=as_of,
                    ref_id=ref_id,
                    meta=meta,
                )
            )
    return chunks


# =========================================================================================
# 3) OWASP Cheat Sheet Series  (Markdown files)
# =========================================================================================

# Which sheets belong to which topic area. Matched against the file name.
CLOUD_SHEET_WORDS = (
    "kubernetes", "docker", "infrastructure_as_code", "secure_cloud", "serverless",
    "secrets_management", "ci_cd", "github_actions", "microservices", "multi_tenant",
    "workload_identity", "zero_trust", "software_supply_chain", "dependency_graph",
    "key_management", "nodejs_docker", "identity_propagation",
)
NETWORK_SHEET_WORDS = (
    "network_segmentation", "tls", "transport_layer", "http_strict_transport",
    "pinning", "denial_of_service", "zero_trust",
)

# Sheets shorter than this are only "see another sheet" redirects.
MIN_SHEET_CHARS = 400


def owasp_domains(sheet_name: str) -> tuple[str, ...]:
    """Every cheat sheet is 'defense' advice. Then we add its topic area by file name;
    anything that is not clearly cloud or network is treated as web application security."""
    lowered = sheet_name.lower()
    domains = ["defense"]
    is_cloud = any(word in lowered for word in CLOUD_SHEET_WORDS)
    is_network = any(word in lowered for word in NETWORK_SHEET_WORDS)
    if is_cloud:
        domains.append("cloud")
    if is_network:
        domains.append("network")
    if not is_cloud and not is_network:
        domains.append("web-app")
    return tuple(domains)


def _strip_front_matter(markdown: str) -> str:
    """Some sheets begin with a '---' metadata block; drop it."""
    if markdown.startswith("---"):
        end = markdown.find("\n---", 3)
        if end != -1:
            return markdown[end + 4:]
    return markdown


def _markdown_sections(markdown: str) -> list[tuple[str, str]]:
    """Splits Markdown into (heading, body) sections at '#' headings, ignoring '#' lines
    that are inside code blocks (shell comments look just like headings)."""
    sections: list[tuple[str, list[str]]] = [("", [])]
    in_code = False
    for line in markdown.splitlines():
        if line.lstrip().startswith("```"):
            in_code = not in_code
        heading = None if in_code else re.match(r"^(#{1,4})\s+(.*\S)\s*$", line)
        if heading:
            sections.append((heading.group(2).strip(), []))
        else:
            sections[-1][1].append(line)
    return [(h, _squash("\n".join(body))) for h, body in sections if _squash("\n".join(body))]


def _plain_links(text: str) -> str:
    """[text](link) -> text, and <https://x> -> https://x.
    Code blocks are left exactly as they are (code can legitimately contain brackets)."""
    def fix(segment: str) -> str:
        segment = re.sub(r"\[([^\]]+)\]\((?:[^)]+)\)", r"\1", segment)
        return re.sub(r"<(https?://[^>]+)>", r"\1", segment)

    # Splitting with a capturing group keeps the code blocks at the odd positions.
    parts = re.split(r"(```.*?```)", text, flags=re.DOTALL)
    return "".join(part if i % 2 else fix(part) for i, part in enumerate(parts))


def _unescape_heading(text: str) -> str:
    """Markdown writes a literal '#' as '\\#'. In a heading we want the plain character."""
    return re.sub(r"\\([\\`*_{}\[\]()#+\-.!|<>~])", r"\1", text)


# A section with less text than this is just a stub (a lone image tag, one word) and is skipped.
MIN_SECTION_CHARS = 60


def parse_owasp(files: dict[str, str], as_of: str = "") -> list[Chunk]:
    """`files` maps a file name (e.g. 'SQL_Injection_Prevention_Cheat_Sheet.md') to its text.
    Each sheet is split at its headings; each section becomes one or more chunks, and each
    chunk starts with the sheet and section names so it still makes sense on its own."""
    chunks: list[Chunk] = []

    for file_name in sorted(files):
        if not file_name.endswith(".md"):
            continue
        markdown = _strip_front_matter(files[file_name])
        if len(markdown.strip()) < MIN_SHEET_CHARS:
            continue

        slug = file_name[: -len(".md")]
        sheet = slug.removesuffix("_Cheat_Sheet")
        sheet_title = sheet.replace("_", " ")
        url = f"https://cheatsheetseries.owasp.org/cheatsheets/{slug}.html"
        domains = owasp_domains(sheet)

        sections = _markdown_sections(markdown)
        counter = 0
        for heading, body in sections:
            # The first section is usually the "# Title Cheat Sheet" line itself; skip a
            # heading that just repeats the sheet name.
            heading = _unescape_heading(heading)
            section_name = heading if heading and heading.lower() != f"{sheet_title} cheat sheet".lower() else ""
            for piece in split_text(_plain_links(body)):
                if len(piece) < MIN_SECTION_CHARS:
                    continue
                counter += 1
                context = f"OWASP Cheat Sheet: {sheet_title}" + (f" > {section_name}" if section_name else "")
                chunks.append(
                    Chunk(
                        id=f"owasp:{sheet}#{counter}",
                        source="owasp",
                        tier=TIER_COMMUNITY,
                        title=f"{sheet_title}" + (f": {section_name}" if section_name else ""),
                        text=f"{context}\n\n{piece}",
                        url=url,
                        license=OWASP_LICENSE,
                        domains=domains,
                        as_of=as_of,
                        ref_id="",
                        meta={"sheet": sheet, "section": section_name},
                    )
                )
    return chunks