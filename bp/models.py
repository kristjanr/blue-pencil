"""Record schemas, shared between structured model outputs and the database.

One definition per record type, used in both directions. That is deliberate: if
the extraction schema and the table schema can drift apart, they will, and the
first symptom is a silently dropped field in chapter fifty.

Two conventions run through every graph record:

*Citations are mandatory.* A claim with no citation is rejected at write time.

*A citation is necessary, not sufficient.* It proves the text contains evidence
related to the claim, not that the claim is right. So every record also carries
a ``claim_type`` and a ``confidence``, and contradictions between scenes are
stored as open records rather than resolved. In a series built on unreliable
narrators, the contradiction is often the story.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ClaimType = Literal["explicit", "inferred", "disputed"]
BeliefState = Literal["knows", "unaware", "believes_false", "suspects", "misinformed"]


class Base(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Citation(Base):
    """A pointer at the text that supports a claim."""

    scene: str = Field(description="Scene ID, e.g. 'B1.04.2'")
    quote: str = Field(default="", description="Verbatim span from that scene")


class Claim(Base):
    """Mixin fields carried by every record that asserts something about canon."""

    claim_type: ClaimType = "explicit"
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    citations: list[Citation] = Field(default_factory=list)
    alternatives: list[str] = Field(
        default_factory=list, description="Other defensible readings of the same evidence"
    )

    @field_validator("citations")
    @classmethod
    def _at_least_one(cls, v: list[Citation]) -> list[Citation]:
        return v  # enforced at write time by bp.db, so extraction can stage drafts


class Entity(Claim):
    """A character, faction, place, or organisation."""

    entity_id: str
    name: str
    kind: Literal["character", "faction", "place", "organisation", "ship", "other"] = "character"
    aliases: list[str] = Field(default_factory=list)
    description: str = ""
    #: Where the body is, as distinct from where the mind is. For a series where
    #: minds move between bodies these are genuinely different questions.
    status: Literal["alive", "dead", "missing", "unknown", "archived"] = "unknown"
    substrate: str = Field(default="", description="Body/hardware the character runs on, if the series tracks it")
    parent_id: str = Field(default="", description="For clones and copies: who this diverged from")
    forked_on: str = Field(default="", description="In-world date of the fork")


class Report(Base):
    """One hop of information: who told whom, over what, and when it lands.

    ``arrives`` is normally left empty by extraction and *computed* from the
    profile's space model and channel speed. That is the whole trick — the
    checker does not ask a model whether a character could know yet; it does the
    arithmetic.
    """

    from_: str = Field(alias="from")
    to: str
    channel: str = "unknown"
    departs: str = Field(default="", description="In-world date the message left, if stated")
    arrives: str = Field(default="", description="Computed unless the text states it")
    status: ClaimType = "explicit"
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    distortion: str = Field(default="", description="How the message was garbled, if it was")


class BeliefRecord(Base):
    """What one character holds true about one event, as of a date."""

    character: str
    state: BeliefState
    as_of: str = ""
    detail: str = ""
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class Event(Claim):
    """The fundamental object of the graph.

    Not a fact: an *event*, with the paths by which knowledge of it travelled.
    That is what lets a checker answer the question that matters — is there an
    information path from this event to this character? — rather than merely
    whether the thing is true.
    """

    event_id: str
    summary: str
    when: str = Field(default="", description="In-world date in the profile's calendar")
    when_certainty: Literal["canonical", "inferred", "unknown"] = "inferred"
    where: str = ""
    participants: list[str] = Field(default_factory=list)
    observed_by: list[str] = Field(default_factory=list)
    reports: list[Report] = Field(default_factory=list)
    beliefs: list[BeliefRecord] = Field(default_factory=list)
    reader_state: Literal["shown directly", "reported", "implied", "hidden"] = "shown directly"
    consequences: list[str] = Field(default_factory=list)
    causes: list[str] = Field(default_factory=list, description="Event IDs this one followed from")
    significance: float = Field(default=0.5, ge=0.0, le=1.0)


class ObjectRecord(Claim):
    """Chekhov's inventory. A sword cannot be in two places."""

    object_id: str
    name: str
    aliases: list[str] = Field(default_factory=list)
    location: str = ""
    holder: str = ""
    as_of: str = ""
    significant: bool = False
    state: str = ""


class Promise(Claim):
    """A prophecy, vow, foreshadowing beat, or dangling setup.

    The raw material for invention: a good twist cashes a promise the text
    already made.
    """

    promise_id: str
    summary: str
    kind: Literal["prophecy", "vow", "foreshadow", "setup", "threat", "question"] = "setup"
    planted_in: list[str] = Field(default_factory=list, description="Scene IDs")
    owed_by: list[str] = Field(default_factory=list)
    status: Literal["open", "paid", "broken", "abandoned"] = "open"
    paid_in: str = ""
    weight: float = Field(default=0.5, ge=0.0, le=1.0)


class Thread(Claim):
    """A plotline with its last known state. The book outline is a schedule for
    advancing these."""

    thread_id: str
    name: str
    state: str = ""
    characters: list[str] = Field(default_factory=list)
    last_scene: str = ""
    status: Literal["open", "closed", "dormant"] = "open"


class Relationship(Claim):
    """A directed, dated edge: owes a debt, swore an oath, suspects, loved once."""

    source: str
    target: str
    kind: str
    sentiment: float = Field(default=0.0, ge=-1.0, le=1.0)
    since: str = ""
    until: str = ""
    detail: str = ""


class TechniqueSpec(Base):
    """How a POV is written, in two layers.

    The *measured* layer is numbers a checker can compute and compare. The
    *mechanism* layer is what those numbers are evidence of — narrative
    distance, how exposition is delivered, how emotion is disclosed, which
    devices recur. The target is never "sounds like the author"; it is "behaves
    like this POV in this situation".
    """

    pov: str
    scenes: int = 0
    words: int = 0
    # measured layer
    mean_sentence_len: float = 0.0
    sd_sentence_len: float = 0.0
    dialogue_ratio: float = 0.0
    interiority_ratio: float = 0.0
    sensory_density: float = 0.0
    paragraph_len: float = 0.0
    type_token_ratio: float = 0.0
    exclaim_ratio: float = 0.0
    question_ratio: float = 0.0
    # mechanism layer (model-authored, cited)
    narrative_distance: str = ""
    exposition_mode: str = ""
    emotion_mode: str = ""
    devices: list[str] = Field(default_factory=list)
    tics: list[str] = Field(default_factory=list)
    drifted_from: str = Field(default="", description="For POVs that began as one voice: what changed")
    notes: str = ""


class Contradiction(Base):
    """Two readings the text will not reconcile. Stored, never flattened."""

    contradiction_id: str
    subject: str
    reading_a: str
    reading_b: str
    citations_a: list[Citation] = Field(default_factory=list)
    citations_b: list[Citation] = Field(default_factory=list)
    note: str = ""
    resolved: bool = False


class ChapterCard(Base):
    """The contract between planning and drafting.

    Structured, not prose, and binding in one direction: the drafter may not add
    a reveal the card does not contain. Uncarded reveals are the easiest way to
    corrupt the ledger, because acceptance writes them into the graph.
    """

    card_id: str
    book: str
    chapter: int
    pov: str
    date_inworld: str = ""
    location: str = ""
    cast: list[str] = Field(default_factory=list)
    goal: str = ""
    turn: str = ""
    feel: str = Field(
        default="", description="What this chapter should feel like, e.g. 'quiet, and ends badly' — "
                                "prose guidance for the drafter, not a structural obligation the "
                                "card checker verifies")
    reveals: list[dict[str, str]] = Field(
        default_factory=list, description="[{what, to_whom, channel}] — writes to the belief graph on accept"
    )
    threads_advanced: list[str] = Field(default_factory=list)
    seeds_planted: list[str] = Field(default_factory=list)
    seeds_paid: list[str] = Field(default_factory=list)
    consequences_expected: list[str] = Field(default_factory=list)
    word_budget: int = 3000
    scenes: list[dict[str, Any]] = Field(default_factory=list, description="[{beats, word_budget}]")


class Move(Base):
    """A candidate for what happens next, scored by separate judges."""

    move_id: str = ""
    summary: str
    thread_id: str = ""
    setup: float = Field(default=0.0, ge=0.0, le=1.0, description="Cashes promises already in the ledger")
    cost: float = Field(default=0.0, ge=0.0, le=1.0, description="Irreversible; someone loses something real")
    character_truth: float = Field(default=0.0, ge=0.0, le=1.0)
    inevitable_in_hindsight: float = Field(default=0.0, ge=0.0, le=1.0)
    futures_opened: float = Field(
        default=0.0, ge=0.0, le=1.0, description="How many interesting states this creates — the closest thing to a computable 'interesting'"
    )
    promises_cashed: list[str] = Field(default_factory=list)
    consequences: list[str] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    rationale: str = ""
    children: list["Move"] = Field(default_factory=list, description="Two-deep expansion at act breaks")

    @property
    def score(self) -> float:
        """Unweighted mean of the five judges, plus a look-ahead bonus.

        Deliberately not a single tunable weighting: the scores go to a human
        alongside the total, and the total exists only to order the shortlist.
        """
        own = (self.setup + self.cost + self.character_truth + self.inevitable_in_hindsight + self.futures_opened) / 5.0
        if not self.children:
            return own
        best_child = max(c.score for c in self.children)
        return 0.7 * own + 0.3 * best_child


class EndingHypothesis(Base):
    """A full-series resolution, scored on evidence from the promise ledger."""

    hypothesis_id: str
    summary: str
    pays: list[str] = Field(default_factory=list)
    orphans: list[str] = Field(default_factory=list)
    fits_author_statements: float = Field(default=0.5, ge=0.0, le=1.0)
    structural_symmetry: float = Field(default=0.5, ge=0.0, le=1.0)
    rationale: str = ""
    citations: list[Citation] = Field(default_factory=list)

    def graph_evidence(self, weights: dict[str, float]) -> float:
        """Fraction of the ledger's total weight this hypothesis pays off, net
        of what it orphans. The only term in :meth:`evidence_score` the graph
        can verify — ``weights`` maps every open promise's id to its weight,
        so a promise id the model invented (not a key in ``weights``) counts
        for nothing rather than crashing the lookup.

        ``pays``/``orphans`` are *ids*, not weight, so this must sum the
        weights they name rather than count them: counting them against a
        *summed* total_weight silently collapses coverage toward zero no
        matter how good the hypothesis is, because a promise count and a
        summed weight are not the same unit.
        """
        total = sum(weights.values()) or 1.0
        paid_weight = sum(weights.get(pid, 0.0) for pid in self.pays)
        orphan_weight = sum(weights.get(pid, 0.0) for pid in self.orphans)
        return max(0.0, (paid_weight - 0.5 * orphan_weight) / total)

    def evidence_score(self, weights: dict[str, float]) -> float:
        """Ranking score: mostly graph evidence, a fifth each self-reported.

        The two self-reported terms are the model's own opinion of its
        hypothesis, not something the graph checked — call
        :meth:`graph_evidence` directly to show the verifiable part on its
        own rather than blended into this one.
        """
        return (self.graph_evidence(weights) * 0.6
                + 0.2 * self.fits_author_statements + 0.2 * self.structural_symmetry)


class Marginalium(Base):
    """One blue-pencil mark: where, how bad, why, and what to do about it.

    The fixes list matters as much as the complaint. A checker that says only
    "this is wrong" makes the human do the diagnosis twice.
    """

    check: str
    severity: Literal["hard", "soft", "floor", "note"] = "soft"
    scene_ref: str = ""
    line: int = 0
    excerpt: str = ""
    message: str
    citations: list[Citation] = Field(default_factory=list)
    fixes: list[str] = Field(default_factory=list)
    metric: dict[str, float] = Field(default_factory=dict)


Move.model_rebuild()
