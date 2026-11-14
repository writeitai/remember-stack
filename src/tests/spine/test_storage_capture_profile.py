"""Generated D152 profile pins and the explicit live-tree commercial bases."""

import json

from scripts.generate_storage_capture_fixture import FIXTURE
from scripts.generate_storage_capture_fixture import render

from rememberstack.profiles.storage_capture_profile import current_class_rules


def test_generated_live_profile_is_current() -> None:
    assert FIXTURE.read_text() == render()
    fixture = json.loads(FIXTURE.read_text())
    assert fixture["schema"]["head"] == "p9_52_0073"


def test_corpus_copies_never_count_as_canonical_raw() -> None:
    rules = {
        name: (store, purpose, component)
        for name, store, purpose, component in current_class_rules()
    }
    assert rules["content_objects.raw_uri"] == (
        "raw",
        "canonical_raw",
        "canonical_object",
    )
    for name in (
        "documents.corpus_original",
        "documents.corpus_derivative",
        "corpus.navigation",
    ):
        assert rules[name] == ("corpus", "projection_origin", "p3_origin")
    assert rules["corpus.incomplete_or_orphan"] == ("corpus", "staging", None)
    assert "projection_snapshots.gcs_uri" not in rules
    assert "corpus.unpublished_snapshot" not in rules
