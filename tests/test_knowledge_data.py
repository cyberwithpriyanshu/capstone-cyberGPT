# tests/test_knowledge_data.py
#
# PURPOSE: Sanity-check the REAL knowledge files in data/knowledge/ (made by
# scripts/ingest_knowledge.py). The other tests use tiny samples; this one makes sure the real
# download did not produce something broken, like duplicate IDs or empty passages.
#
# If you have not run the ingestion yet, these tests are SKIPPED (not failed).

import pytest

from app.knowledge import DEFAULT_DATA_DIR, DOMAINS, SearchIndex, load_directory

CHUNKS = load_directory(DEFAULT_DATA_DIR)

pytestmark = pytest.mark.skipif(
    not CHUNKS, reason="knowledge not ingested yet (run: python scripts/ingest_knowledge.py)"
)


def test_every_source_is_present():
    assert {c.source for c in CHUNKS} == {"kev", "attack", "owasp"}


def test_ids_are_unique():
    ids = [c.id for c in CHUNKS]
    assert len(ids) == len(set(ids))


def test_every_chunk_is_complete_and_citable():
    for c in CHUNKS:
        assert c.title.strip() and len(c.text) >= 40, c.id
        assert c.url.startswith("https://"), c.id
        assert c.license, c.id
        assert c.tier in (1, 2, 3), c.id
        assert c.domains and set(c.domains) <= set(DOMAINS), c.id


def test_the_requested_topic_areas_all_have_content():
    covered = {d for c in CHUNKS for d in c.domains}

    # cloud security and malware analysis were explicitly requested for the project
    assert {"web-app", "vuln-intel", "defense", "cloud", "malware", "threat-behavior"} <= covered


def test_a_famous_cve_and_technique_can_be_found_by_id():
    index = SearchIndex(CHUNKS)

    assert index.lookup("CVE-2021-44228"), "Log4Shell should be in the CISA KEV catalog"
    assert index.lookup("T1059.001"), "PowerShell should be in MITRE ATT&CK"


def test_an_invented_cve_is_not_found():
    result = SearchIndex(CHUNKS).search("CVE-1999-0001")

    assert result.ids_not_found == ["CVE-1999-0001"]
    assert result.hits == []