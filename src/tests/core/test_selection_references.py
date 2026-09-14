"""Provider-free D122 catalog resolution, distinct cards, and complete support."""

from uuid import uuid4

from rememberstack.core.selection_references import catalog_by_label
from rememberstack.core.selection_references import claimify_input_hash
from rememberstack.core.selection_references import GroundedCard
from rememberstack.core.selection_references import GroundedPassage
from rememberstack.core.selection_references import PassageSupport
from rememberstack.core.selection_references import publish_selection_cards
from rememberstack.core.selection_references import rank_and_fit_cards
from rememberstack.core.selection_references import remap_card
from rememberstack.core.selection_references import render_cards_for_claimify
from rememberstack.model.chunks import ChunkForEmbedding
from rememberstack.model.claims import SourceReferenceCard


def _chunk(*, ordinal: int, start: int, end: int, chunk_id=None) -> ChunkForEmbedding:
    """Minimal extract-window chunk."""
    return ChunkForEmbedding(
        chunk_id=chunk_id or uuid4(),
        doc_id=uuid4(),
        version_id=uuid4(),
        ordinal=ordinal,
        char_start=start,
        char_end=end,
        chunk_content_hash=f"h{ordinal}",
        extraction_input_hash=f"e{ordinal}",
        section_role="body",
        section_path="/root",
    )


def _catalog(*passages: PassageSupport) -> dict[str, PassageSupport]:
    """Engine-supplied label map."""
    return catalog_by_label(passages=passages)


def test_unknown_label_rejects_the_whole_card() -> None:
    """A forged citation is not dropped so the remaining supports can keep the name."""
    document = "Nate won the Riverside Cup today."
    owner = _chunk(ordinal=0, start=0, end=len(document))
    catalog = _catalog(
        PassageSupport(label="S1", char_start=13, char_end=26, region="target")
    )
    published, truncated, diagnostics = publish_selection_cards(
        cards=(
            SourceReferenceCard(name="Riverside Cup", source_refs=("S1", "S-forged")),
        ),
        catalog=catalog,
        document_md=document,
        owner_chunk=owner,
    )
    assert published == ()
    assert truncated is False
    assert diagnostics[0].gate == "unknown_source_ref"


def test_neighbour_only_support_is_not_published() -> None:
    """Neighbour passages may clarify; they cannot publish the card alone."""
    document = "Earlier the cup was named. Nate won that tournament today."
    owner = _chunk(ordinal=1, start=26, end=len(document))
    catalog = _catalog(
        PassageSupport(label="S1", char_start=8, char_end=25, region="previous")
    )
    published, _, diagnostics = publish_selection_cards(
        cards=(SourceReferenceCard(name="the cup", source_refs=("S1",)),),
        catalog=catalog,
        document_md=document,
        owner_chunk=owner,
    )
    assert published == ()
    assert diagnostics[0].gate == "no_target_passage"


def test_same_name_distinct_ranges_are_both_published() -> None:
    """Equal names are not identity; two tournaments stay two cards."""
    document = "The Open on Monday. The Open on Friday."
    owner = _chunk(ordinal=0, start=0, end=len(document))
    catalog = _catalog(
        PassageSupport(label="S1", char_start=0, char_end=18, region="target"),
        PassageSupport(label="S2", char_start=20, char_end=39, region="target"),
    )
    published, truncated, diagnostics = publish_selection_cards(
        cards=(
            SourceReferenceCard(name="The Open", source_refs=("S1",)),
            SourceReferenceCard(name="The Open", source_refs=("S2",)),
        ),
        catalog=catalog,
        document_md=document,
        owner_chunk=owner,
    )
    assert truncated is False
    assert diagnostics == ()
    assert [card.ordinal for card in published] == [0, 1]
    assert [card.passages[0].char_start for card in published] == [0, 20]


def test_two_referents_may_share_one_passage() -> None:
    """One paragraph can introduce Nate and Tournament A; range is not identity."""
    document = "Nate won Tournament A today."
    owner = _chunk(ordinal=0, start=0, end=len(document))
    catalog = _catalog(
        PassageSupport(label="S1", char_start=0, char_end=len(document), region="target")
    )
    published, truncated, diagnostics = publish_selection_cards(
        cards=(
            SourceReferenceCard(name="Nate", source_refs=("S1",)),
            SourceReferenceCard(name="Tournament A", source_refs=("S1",)),
        ),
        catalog=catalog,
        document_md=document,
        owner_chunk=owner,
    )
    assert truncated is False
    assert diagnostics == ()
    assert [card.name for card in published] == ["Nate", "Tournament A"]
    assert [card.ordinal for card in published] == [0, 1]
    assert {card.passages[0].label for card in published} == {"S1"}


def test_cap_keeps_first_four_distinct_cards() -> None:
    """A fifth distinct card is a recorded cap loss, not a merge."""
    names = ("A Cup", "B Cup", "C Cup", "D Cup", "E Cup")
    document = " ".join(names)
    owner = _chunk(ordinal=0, start=0, end=len(document))
    cursor = 0
    passages: list[PassageSupport] = []
    for index, name in enumerate(names, start=1):
        start = document.find(name, cursor)
        passages.append(
            PassageSupport(
                label=f"S{index}",
                char_start=start,
                char_end=start + len(name),
                region="target",
            )
        )
        cursor = start + len(name)
    catalog = _catalog(*passages)
    published, truncated, diagnostics = publish_selection_cards(
        cards=tuple(
            SourceReferenceCard(name=name, source_refs=(f"S{index}",))
            for index, name in enumerate(names, start=1)
        ),
        catalog=catalog,
        document_md=document,
        owner_chunk=owner,
    )
    assert truncated is True
    assert diagnostics == ()
    assert [card.name for card in published] == list(names[:4])
    assert [card.ordinal for card in published] == [0, 1, 2, 3]


def test_claimify_hash_includes_empty_preceding_producers() -> None:
    """A silent earlier chunk is still a negative reuse dependency."""
    with_empty = claimify_input_hash(
        target_selection_input_hash="target",
        preceding_selection_input_hashes=("a", "empty-producer", "c"),
    )
    without = claimify_input_hash(
        target_selection_input_hash="target",
        preceding_selection_input_hashes=("a", "c"),
    )
    competing = claimify_input_hash(
        target_selection_input_hash="target",
        preceding_selection_input_hashes=("a", "new-introduction", "c"),
    )
    assert with_empty != without
    assert with_empty != competing


def test_rank_prefers_overlap_then_nearer_chunk_then_ordinal() -> None:
    """Zero overlap can still admit a nearby pronoun-needed card."""
    owner_a = uuid4()
    owner_b = uuid4()
    near_no_overlap = GroundedCard(
        name="Riverside Cup",
        aliases=(),
        passages=(
            GroundedPassage(
                label="S-near",
                char_start=0,
                char_end=7,
                region="target",
                text="the cup",
            ),
        ),
        ordinal=1,
        owner_chunk_id=owner_a,
        owner_ordinal=6,
    )
    far_overlap = GroundedCard(
        name="City Open",
        aliases=(),
        passages=(
            GroundedPassage(
                label="S-far",
                char_start=0,
                char_end=9,
                region="target",
                text="City Open",
            ),
        ),
        ordinal=0,
        owner_chunk_id=owner_b,
        owner_ordinal=1,
    )
    same_nearer = GroundedCard(
        name="City Open Final",
        aliases=(),
        passages=(
            GroundedPassage(
                label="S-nearer",
                char_start=0,
                char_end=9,
                region="target",
                text="City Open",
            ),
        ),
        ordinal=0,
        owner_chunk_id=owner_b,
        owner_ordinal=5,
    )
    chosen, truncated = rank_and_fit_cards(
        cards=(near_no_overlap, far_overlap, same_nearer),
        target_text="Nate won the City Open yesterday.",
        target_ordinal=7,
    )
    assert truncated is False
    assert [card.name for card in chosen] == [
        "City Open Final",
        "City Open",
        "Riverside Cup",
    ]


def test_fit_drops_a_whole_card_rather_than_trimming_passages() -> None:
    """An over-budget card is omitted; its label is not kept without evidence."""
    huge = GroundedCard(
        name="X" * 4000,
        aliases=(),
        passages=(
            GroundedPassage(
                label="S-huge",
                char_start=0,
                char_end=200,
                region="target",
                text="Y" * 200,
            ),
        ),
        ordinal=0,
        owner_chunk_id=uuid4(),
        owner_ordinal=0,
    )
    small = GroundedCard(
        name="Nate",
        aliases=(),
        passages=(
            GroundedPassage(
                label="S-nate", char_start=0, char_end=4, region="target", text="Nate"
            ),
        ),
        ordinal=1,
        owner_chunk_id=uuid4(),
        owner_ordinal=0,
    )
    chosen, truncated = rank_and_fit_cards(
        cards=(huge, small), target_text="Nate", target_ordinal=1, max_chars=100
    )
    assert truncated is True
    assert [card.name for card in chosen] == ["Nate"]


def test_remap_rejects_incomplete_or_rescued_mapping() -> None:
    """D119 refusal or a dropped support invalidates the card; text is not searched."""
    document = "pad pad Riverside Cup then Riverside Cup later"
    second = document.rfind("Riverside Cup")
    length = len("Riverside Cup")
    card = GroundedCard(
        name="Riverside Cup",
        aliases=(),
        passages=(
            GroundedPassage(
                label="S1",
                char_start=20,
                char_end=20 + length,
                region="target",
                text="Riverside Cup",
            ),
        ),
        ordinal=0,
        owner_chunk_id=uuid4(),
        owner_ordinal=1,
    )
    windows = {"target": (second, second + length)}
    assert (
        remap_card(
            card=card, remapped_spans=None, current_md=document, current_windows=windows
        )
        is None
    )
    complete = remap_card(
        card=card,
        remapped_spans=((second, second + length),),
        current_md=document,
        current_windows=windows,
    )
    assert complete is not None
    assert complete.passages[0].char_start == second
    assert complete.passages[0].label == "S1"


def test_claimify_labels_are_request_unique_and_keep_source_ids() -> None:
    """Two owners can both emit R-ordinal 0; the request still gets R1 and R2."""
    owner_a = uuid4()
    owner_b = uuid4()
    nate = GroundedCard(
        name="Nate",
        aliases=(),
        passages=(
            GroundedPassage(
                label="S1", char_start=0, char_end=4, region="target", text="Nate"
            ),
        ),
        ordinal=0,
        owner_chunk_id=owner_a,
        owner_ordinal=1,
    )
    tournament = GroundedCard(
        name="Tournament A",
        aliases=("the cup",),
        passages=(
            GroundedPassage(
                label="S2",
                char_start=9,
                char_end=21,
                region="target",
                text="Tournament A",
            ),
        ),
        ordinal=0,
        owner_chunk_id=owner_b,
        owner_ordinal=2,
    )
    rendered = render_cards_for_claimify(cards=(nate, tournament))
    assert rendered.startswith("R1: Nate")
    assert "R2: Tournament A also called the cup" in rendered
    assert '  S1: "Nate"' in rendered
    assert '  S2: "Tournament A"' in rendered
    assert rendered.count("R1:") == 1
    assert rendered.count("R2:") == 1


def test_character_cap_counts_the_rendered_block() -> None:
    """The 4096 bound includes labels and formatting, not only raw name text."""
    card = GroundedCard(
        name="Nate",
        aliases=(),
        passages=(
            GroundedPassage(
                label="S1", char_start=0, char_end=4, region="target", text="Nate"
            ),
        ),
        ordinal=0,
        owner_chunk_id=uuid4(),
        owner_ordinal=0,
    )
    rendered = render_cards_for_claimify(cards=(card,))
    assert len(rendered) > len("NateNate")
    chosen, truncated = rank_and_fit_cards(
        cards=(card,),
        target_text="Nate",
        target_ordinal=1,
        max_chars=len(rendered) - 1,
    )
    assert truncated is True
    assert chosen == ()
    kept, kept_truncated = rank_and_fit_cards(
        cards=(card,),
        target_text="Nate",
        target_ordinal=1,
        max_chars=len(rendered),
    )
    assert kept_truncated is False
    assert kept == (card,)
