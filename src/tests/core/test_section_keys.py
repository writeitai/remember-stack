"""D140 §4: heading attribute blocks, section keys and section content hashes."""

import hashlib
import json

import pytest

from rememberstack.core import blockize
from rememberstack.core import deterministic_section_role
from rememberstack.core import duplicate_section_key_warnings
from rememberstack.core import heading_attributes
from rememberstack.core import parse_heading_skeleton
from rememberstack.core import resolve_fallback_skeleton
from rememberstack.core import skeleton_hash
from rememberstack.core import with_content_hashes
from rememberstack.model import FallbackAnchor


def _parse(source: str):
    blocks = blockize(document_md=source)
    sections = parse_heading_skeleton(
        blocks=blocks, title="Document", markdown_chars=len(source)
    )
    return blocks, sections


def test_atx_heading_attribute_block_becomes_key_and_leaves_title() -> None:
    _, sections = _parse(
        "## 4. Per-diem allowance {#per-diem}\n\nbody\n\n"
        "### § 5 Scope {#par_5 .provision}\n\nbody\n"
    )
    per_diem, scope = sections[1:]
    assert (per_diem.title, per_diem.section_key) == (
        "4. Per-diem allowance",
        "per-diem",
    )
    assert per_diem.normalized_title == "4. per-diem allowance"
    assert (scope.title, scope.section_key) == ("§ 5 Scope", "par_5")


def test_setext_heading_attribute_block_is_recognized() -> None:
    _, sections = _parse("Approvals {#approvals}\n----------------------\n\nbody\n")
    assert sections[1].title == "Approvals"
    assert sections[1].section_key == "approvals"


def test_role_classification_sees_the_stripped_title() -> None:
    _, sections = _parse("# References {#refs}\n\nA. Author, 2020.\n")
    assert sections[1].normalized_title == "references"
    assert (
        deterministic_section_role(normalized_title=sections[1].normalized_title)
        == "references"
    )


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Scope {#a:b/c.d-e_f}", ("Scope", "a:b/c.d-e_f")),
        ("Scope {.unnumbered}", ("Scope", None)),
        ("Scope {#k lang=cs data-x='a b'}", ("Scope", "k")),
        ("Scope {#k}  ", ("Scope", "k")),
        ("Set {x} {#k}", ("Set {x}", "k")),
        ("Scope {#" + "a" * 200 + "}", ("Scope", "a" * 200)),
    ],
)
def test_valid_attribute_blocks(title: str, expected: tuple[str, str | None]) -> None:
    assert heading_attributes(title=title) == expected


@pytest.mark.parametrize(
    "title",
    [
        "Scope",
        "Scope {}",
        "Scope {#}",
        "Scope {#bad key!}",
        "Scope {#bad!}",
        "Scope {#a #b}",
        "Scope {#k} trailing",
        "Scope {#" + "a" * 201 + "}",
        "Scope {#k}}",
        "Scope {per-diem}",
    ],
)
def test_invalid_attribute_blocks_leave_the_heading_untouched(title: str) -> None:
    assert heading_attributes(title=title) is None


def test_invalid_block_keeps_title_verbatim_and_no_key() -> None:
    _, sections = _parse("# Scope {#bad!}\n\nbody\n")
    assert sections[1].title == "Scope {#bad!}"
    assert sections[1].section_key is None


def test_duplicate_key_keeps_the_first_heading_and_warns() -> None:
    blocks, sections = _parse(
        "# One {#dup}\n\nbody\n\n## Two {#dup}\n\nbody\n\n# Three {#dup}\n"
    )
    assert [(s.title, s.section_key) for s in sections[1:]] == [
        ("One", "dup"),
        ("Two", None),
        ("Three", None),
    ]
    assert duplicate_section_key_warnings(sections=sections, blocks=blocks) == (
        {
            "kind": "duplicate_section_key",
            "section_key": "dup",
            "node_path": "0.0.0",
            "kept_node_path": "0.0",
        },
        {
            "kind": "duplicate_section_key",
            "section_key": "dup",
            "node_path": "0.1",
            "kept_node_path": "0.0",
        },
    )


def test_fallback_sections_get_no_key_and_no_warning() -> None:
    source = "Intro text.\n\n# Heading {#k}\n\nbody\n"
    blocks = blockize(document_md=source)
    sections = resolve_fallback_skeleton(
        proposed=(
            FallbackAnchor(anchor="Intro", occurrence_index=0),
            FallbackAnchor(anchor="Heading", occurrence_index=0),
        ),
        blocks=blocks,
        document_md=source,
        title="Document",
    )
    assert len(sections) > 1
    assert all(section.section_key is None for section in sections)
    assert duplicate_section_key_warnings(sections=sections, blocks=blocks) == ()


def test_skeleton_hash_is_unchanged_for_keyless_trees_and_sees_keys() -> None:
    _, keyless = _parse("# A\n\nbody\n")
    pre_d140 = [
        {
            field: getattr(section, field)
            for field in (
                "node_path",
                "parent_path",
                "title",
                "normalized_title",
                "heading_level",
                "block_start",
                "block_end",
                "char_start",
                "char_end",
            )
        }
        for section in keyless
    ]
    encoded = json.dumps(
        pre_d140, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    assert skeleton_hash(sections=keyless) == hashlib.sha256(encoded).hexdigest()

    _, keyed = _parse("# A {#a}\n\nbody\n")
    stripped = tuple(
        section.model_copy(update={"section_key": None}) for section in keyed
    )
    assert skeleton_hash(sections=keyed) != skeleton_hash(sections=stripped)


def _hashed(source: str):
    blocks, sections = _parse(source)
    return {
        section.title: section
        for section in with_content_hashes(sections=sections, blocks=blocks)
    }


def test_changed_child_paragraph_changes_subtree_not_own_hash() -> None:
    before = _hashed("# Parent\n\nIntro.\n\n## Child\n\nOld text.\n")
    after = _hashed("# Parent\n\nIntro.\n\n## Child\n\nNew text.\n")
    assert before["Parent"].own_content_hash == after["Parent"].own_content_hash
    assert before["Parent"].subtree_content_hash != after["Parent"].subtree_content_hash
    assert before["Child"].own_content_hash != after["Child"].own_content_hash
    assert before["Child"].subtree_content_hash != after["Child"].subtree_content_hash


def test_changed_own_paragraph_changes_both_hashes() -> None:
    before = _hashed("# Parent\n\nIntro.\n\n## Child\n\nText.\n")
    after = _hashed("# Parent\n\nIntro changed.\n\n## Child\n\nText.\n")
    assert before["Parent"].own_content_hash != after["Parent"].own_content_hash
    assert before["Parent"].subtree_content_hash != after["Parent"].subtree_content_hash
    assert before["Child"].own_content_hash == after["Child"].own_content_hash


def test_leaf_own_and_subtree_hashes_agree_and_reflow_is_stable() -> None:
    hashed = _hashed("# Leaf\n\nOne long\nline.\n")
    reflowed = _hashed("# Leaf\n\nOne long line.\n")
    assert hashed["Leaf"].own_content_hash == hashed["Leaf"].subtree_content_hash
    assert hashed["Leaf"].subtree_content_hash == reflowed["Leaf"].subtree_content_hash


def test_empty_document_root_gets_hashes() -> None:
    hashed = _hashed("")
    root = hashed["Document"]
    assert root.own_content_hash is not None
    assert root.own_content_hash == root.subtree_content_hash
