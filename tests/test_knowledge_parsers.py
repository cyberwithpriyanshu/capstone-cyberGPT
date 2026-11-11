# tests/test_knowledge_parsers.py
#
# PURPOSE: Check that each source's raw data is turned into correct chunks.
#
# We feed the parsers tiny hand-written samples shaped like the real downloads. That way the
# tests run instantly, need no internet, and we know exactly what the right answer is.

from app.knowledge.parsers import (
    clean_attack_text,
    owasp_domains,
    parse_attack,
    parse_kev,
    parse_owasp,
    split_text,
)


# ---------------------------------------------------------------- helpers

def test_split_text_keeps_short_text_whole():
    assert split_text("one short paragraph") == ["one short paragraph"]
    assert split_text("   ") == []


def test_split_text_breaks_between_paragraphs_and_respects_the_limit():
    text = "\n\n".join(f"Paragraph {i}. " + "word " * 30 for i in range(10))

    pieces = split_text(text, limit=400)

    assert len(pieces) > 1
    assert all(len(p) <= 400 for p in pieces)
    # Nothing is lost: every paragraph still appears somewhere.
    assert all(f"Paragraph {i}." in "\n".join(pieces) for i in range(10))


def test_split_text_cuts_one_giant_paragraph_at_sentence_ends():
    text = "This is a sentence. " * 100

    pieces = split_text(text, limit=300)

    assert len(pieces) > 1
    assert all(len(p) <= 300 for p in pieces)
    assert all(p.endswith(".") for p in pieces)


def test_clean_attack_text_removes_website_markup():
    raw = (
        "Adversaries use [PowerShell](https://attack.mitre.org/techniques/T1059/001) "
        "<code>Start-Process</code> to run code. (Citation: Microsoft PS 2014)<br>Done."
    )

    cleaned = clean_attack_text(raw)

    assert cleaned == "Adversaries use PowerShell `Start-Process` to run code.Done."
    assert "Citation" not in cleaned and "http" not in cleaned


# ---------------------------------------------------------------- KEV

KEV_SAMPLE = {
    "catalogVersion": "2026.01.01",
    "vulnerabilities": [
        {
            "cveID": "CVE-2021-44228",
            "vendorProject": "Apache",
            "product": "Log4j2",
            "vulnerabilityName": "Apache Log4j2 Remote Code Execution Vulnerability",
            "dateAdded": "2021-12-10",
            "shortDescription": "JNDI features do not protect against attacker-controlled endpoints.",
            "requiredAction": "Apply updates.",
            "dueDate": "2021-12-24",
            "knownRansomwareCampaignUse": "Known",
            "notes": "https://nvd.nist.gov/vuln/detail/CVE-2021-44228",
            "cwes": ["CWE-20", "CWE-502"],
        },
        {
            "cveID": "CVE-2020-0001",
            "vendorProject": "Acme",
            "product": "Widget",
            "vulnerabilityName": "Acme Widget Flaw",
            "dateAdded": "2020-01-01",
            "shortDescription": "A flaw.",
            "requiredAction": "Patch.",
            "dueDate": "2020-02-01",
            "knownRansomwareCampaignUse": "Unknown",
            "notes": "",
            "cwes": [],
        },
        {"vulnerabilityName": "an entry with no CVE ID must be skipped"},
    ],
}


def test_kev_makes_one_chunk_per_vulnerability_and_skips_entries_without_an_id():
    chunks = parse_kev(KEV_SAMPLE)

    assert [c.id for c in chunks] == ["kev:CVE-2021-44228", "kev:CVE-2020-0001"]


def test_kev_chunk_carries_source_info_for_citations():
    chunk = parse_kev(KEV_SAMPLE)[0]

    assert chunk.source == "kev" and chunk.tier == 1
    assert chunk.ref_id == "CVE-2021-44228"
    assert chunk.url == "https://nvd.nist.gov/vuln/detail/CVE-2021-44228"
    assert "CC0" in chunk.license
    assert chunk.domains == ("vuln-intel",)
    assert chunk.as_of == "2021-12-10"
    assert "CWE-20, CWE-502" in chunk.text
    assert chunk.meta["cwes"] == ["CWE-20", "CWE-502"]
    assert chunk.meta["in_kev"] is True


def test_kev_mentions_ransomware_only_when_it_is_known():
    known, unknown = parse_kev(KEV_SAMPLE)

    assert "ransomware" in known.text.lower()
    # If every entry said "ransomware: Unknown", searching for ransomware would match them all.
    assert "ransomware" not in unknown.text.lower()
    assert unknown.meta["ransomware"] == "Unknown"  # the fact is still kept in structured form


# ---------------------------------------------------------------- ATT&CK

def _ref(external_id, kind="techniques"):
    return [{"source_name": "mitre-attack", "external_id": external_id,
             "url": f"https://attack.mitre.org/{kind}/{external_id}"}]


ATTACK_SAMPLE = {
    "objects": [
        {"type": "attack-pattern", "id": "attack-pattern--1", "name": "Command and Scripting Interpreter",
         "external_references": _ref("T1059"), "modified": "2026-01-02T00:00:00Z",
         "description": "Adversaries may abuse command interpreters. (Citation: X 2020)",
         "kill_chain_phases": [{"kill_chain_name": "mitre-attack", "phase_name": "execution"}],
         "x_mitre_platforms": ["Windows", "Linux"]},
        {"type": "attack-pattern", "id": "attack-pattern--2", "name": "Cloud Storage Object Discovery",
         "external_references": _ref("T1619"), "modified": "2026-01-02T00:00:00Z",
         "description": "Adversaries may enumerate objects in cloud storage.",
         "kill_chain_phases": [{"kill_chain_name": "mitre-attack", "phase_name": "discovery"}],
         "x_mitre_platforms": ["IaaS"]},
        {"type": "attack-pattern", "id": "attack-pattern--revoked", "name": "Old", "revoked": True,
         "external_references": _ref("T1000"), "description": "Retired."},
        {"type": "attack-pattern", "id": "attack-pattern--deprecated", "name": "Older",
         "x_mitre_deprecated": True, "external_references": _ref("T1001"), "description": "Dropped."},
        {"type": "malware", "id": "malware--1", "name": "Emotet",
         "external_references": _ref("S0367", "software"), "modified": "2026-01-03T00:00:00Z",
         "description": "Emotet is a modular banking trojan.", "x_mitre_aliases": ["Emotet", "Geodo"],
         "x_mitre_platforms": ["Windows"]},
        {"type": "tool", "id": "tool--1", "name": "PsExec",
         "external_references": _ref("S0029", "software"), "description": "PsExec runs processes remotely.",
         "x_mitre_platforms": ["Windows"]},
        {"type": "course-of-action", "id": "course-of-action--1", "name": "Execution Prevention",
         "external_references": _ref("M1038", "mitigations"), "description": "Block unwanted code."},
        {"type": "relationship", "relationship_type": "uses", "source_ref": "malware--1",
         "target_ref": "attack-pattern--1"},
        {"type": "relationship", "relationship_type": "mitigates", "source_ref": "course-of-action--1",
         "target_ref": "attack-pattern--1"},
        # A technique with no description cannot be cited reliably, so it is skipped.
        {"type": "attack-pattern", "id": "attack-pattern--empty", "name": "Empty",
         "external_references": _ref("T1002"), "description": ""},
    ]
}


def _by_ref(chunks):
    return {c.ref_id: c for c in chunks}


def test_attack_skips_revoked_deprecated_and_empty_entries():
    refs = {c.ref_id for c in parse_attack(ATTACK_SAMPLE)}

    assert refs == {"T1059", "T1619", "S0367", "S0029", "M1038"}


def test_attack_technique_text_is_clean_and_lists_its_mitigations():
    chunk = _by_ref(parse_attack(ATTACK_SAMPLE))["T1059"]

    assert "Citation" not in chunk.text
    assert "Tactics: execution" in chunk.text
    assert "Mitigations: M1038 Execution Prevention" in chunk.text
    assert chunk.url == "https://attack.mitre.org/techniques/T1059"
    assert chunk.tier == 1 and chunk.as_of == "2026-01-02"
    assert chunk.domains == ("threat-behavior",)


def test_attack_malware_lists_techniques_and_is_tagged_malware():
    chunk = _by_ref(parse_attack(ATTACK_SAMPLE))["S0367"]

    assert "malware" in chunk.domains
    assert "Also known as: Geodo" in chunk.text
    assert "T1059 Command and Scripting Interpreter" in chunk.text


def test_attack_tools_are_not_tagged_malware():
    chunk = _by_ref(parse_attack(ATTACK_SAMPLE))["S0029"]

    assert "malware" not in chunk.domains


def test_attack_cloud_platforms_get_the_cloud_tag():
    chunks = _by_ref(parse_attack(ATTACK_SAMPLE))

    assert "cloud" in chunks["T1619"].domains
    assert "cloud" not in chunks["T1059"].domains


def test_attack_mitigation_is_tagged_defense_and_lists_what_it_helps_against():
    chunk = _by_ref(parse_attack(ATTACK_SAMPLE))["M1038"]

    assert "defense" in chunk.domains
    assert "Helps against: T1059 Command and Scripting Interpreter" in chunk.text


def test_attack_long_entries_are_split_with_unique_ids():
    long_text = "\n\n".join(f"Paragraph {i}. " + "detail " * 60 for i in range(12))
    bundle = {"objects": [
        {"type": "attack-pattern", "id": "attack-pattern--9", "name": "Big", "description": long_text,
         "external_references": _ref("T9999")},
    ]}

    chunks = parse_attack(bundle)

    assert len(chunks) > 1
    assert len({c.id for c in chunks}) == len(chunks)
    assert all(c.ref_id == "T9999" for c in chunks)


# ---------------------------------------------------------------- OWASP

def test_owasp_domains_by_sheet_name():
    assert owasp_domains("Kubernetes_Security") == ("defense", "cloud")
    assert owasp_domains("Transport_Layer_Security") == ("defense", "network")
    assert owasp_domains("SQL_Injection_Prevention") == ("defense", "web-app")
    assert set(owasp_domains("Zero_Trust_Architecture")) == {"defense", "cloud", "network"}


SHEET = """# SQL Injection Prevention Cheat Sheet

## Introduction

A SQL injection attack occurs when untrusted data becomes part of a query. See the [Injection Prevention Cheat Sheet](Injection_Prevention_Cheat_Sheet.md) for more.

## Primary Defenses

Use parameterized queries. This is the most important defense against injection attacks in any language.

```bash
# this comment line must NOT be treated as a heading
echo "[link](kept-as-is)"
```

## RULE \\#1 - Escaping

Escape untrusted values only as a last resort because it is fragile and database specific.

## Tiny

x
"""


def test_owasp_splits_into_sections_with_context_and_citation_info():
    chunks = parse_owasp({"SQL_Injection_Prevention_Cheat_Sheet.md": SHEET}, as_of="2026-11-10")

    titles = [c.title for c in chunks]
    assert "SQL Injection Prevention: Introduction" in titles
    assert "SQL Injection Prevention: Primary Defenses" in titles

    first = chunks[0]
    assert first.source == "owasp" and first.tier == 2
    assert first.url == "https://cheatsheetseries.owasp.org/cheatsheets/SQL_Injection_Prevention_Cheat_Sheet.html"
    assert "CC BY-SA" in first.license
    assert first.domains == ("defense", "web-app")
    assert first.as_of == "2026-11-10"
    # Each chunk starts with where it came from, so it still makes sense when read alone.
    assert first.text.startswith("OWASP Cheat Sheet: SQL Injection Prevention > Introduction")


def test_owasp_turns_links_into_plain_text_but_leaves_code_alone():
    chunks = parse_owasp({"SQL_Injection_Prevention_Cheat_Sheet.md": SHEET})
    text = "\n".join(c.text for c in chunks)

    assert "See the Injection Prevention Cheat Sheet for more." in text
    assert "(Injection_Prevention_Cheat_Sheet.md)" not in text
    # Inside the code block nothing is changed, and its "# comment" is not a heading.
    assert 'echo "[link](kept-as-is)"' in text
    assert not any(c.title.endswith("this comment line must NOT be treated as a heading") for c in chunks)


def test_owasp_unescapes_headings_and_drops_stub_sections():
    chunks = parse_owasp({"SQL_Injection_Prevention_Cheat_Sheet.md": SHEET})
    titles = [c.title for c in chunks]

    assert "SQL Injection Prevention: RULE #1 - Escaping" in titles
    assert not any(t.endswith(": Tiny") for t in titles)  # a one-character section is noise


def test_owasp_skips_redirect_stub_sheets_and_non_markdown_files():
    files = {
        "Injection_Prevention_in_Java_Cheat_Sheet.md": "The content moved to another sheet.",
        "image.png": "binary",
    }

    assert parse_owasp(files) == []


def test_owasp_chunk_ids_are_unique():
    chunks = parse_owasp({"SQL_Injection_Prevention_Cheat_Sheet.md": SHEET})

    assert len({c.id for c in chunks}) == len(chunks)