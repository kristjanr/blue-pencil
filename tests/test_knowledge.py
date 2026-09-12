"""The core query: is there an information path from this event to this character?"""

from bp.knowledge import KnowledgeGraph


def day(profile, text):
    return profile.calendar.parse(text).lo


def test_observer_knows_immediately(world):
    graph, profile = world
    k = KnowledgeGraph(graph).earliest_knowledge("E-001", "Cyra")
    assert k.reachable and k.day == day(profile, "2180-06-01")


def test_light_lag_is_computed_not_asserted(world):
    """Vela is twelve light years from Sol, so news takes twelve years.

    Nothing in the fixture states this arrival date; it is derived from the
    profile's distance table and the channel's speed.
    """
    graph, profile = world
    k = KnowledgeGraph(graph).earliest_knowledge("E-001", "Ana")
    assert profile.calendar.format(k.day) == "2192-06-01"
    assert any(h.computed for h in k.path)


def test_relay_cannot_forward_what_it_does_not_know(world):
    """Sol learns at six years, so Sol's relay onward cannot depart earlier."""
    graph, profile = world
    kg = KnowledgeGraph(graph)
    boro = kg.earliest_knowledge("E-002", "Boro")
    ana = kg.earliest_knowledge("E-002", "Ana")
    assert boro.day < ana.day
    assert ana.day - boro.day >= 6 * 365


def test_no_survivors_means_no_path(world):
    """Dael's death has no observer who lives to report it, so nobody can know."""
    graph, _ = world
    k = KnowledgeGraph(graph).earliest_knowledge("E-003", "Ana")
    assert not k.reachable and not k.indeterminate
    assert k.reason == "no information path"


def test_instant_channel_removes_the_delay(world):
    graph, profile = world
    k = KnowledgeGraph(graph).earliest_knowledge("E-005", "Boro")
    assert profile.calendar.format(k.day) == "2190-01-01"


def test_clone_inherits_only_what_the_parent_knew_at_the_fork(world):
    """Ana-2 forks in 2183. Ana does not learn of the vault until 2187, so the
    copy does not inherit it — divergence, not telepathy."""
    graph, _ = world
    kg = KnowledgeGraph(graph)
    assert not kg.earliest_knowledge("E-002", "Ana-2").reachable
    # But the fork event itself was observed by the copy.
    assert kg.earliest_knowledge("E-004", "Ana-2").reachable


def test_abstains_when_the_event_has_no_date(world):
    graph, profile = world
    from bp.models import Citation, Event

    graph.write_event(Event(event_id="E-undated", summary="something happens",
                            citations=[Citation(scene="LW1.01.1", quote="x")]))
    k = KnowledgeGraph(graph).earliest_knowledge("E-undated", "Ana")
    assert k.indeterminate and "date" in k.reason


def test_belief_snapshot_is_a_checkout(world):
    graph, profile = world
    kg = KnowledgeGraph(graph)
    before = kg.belief_snapshot("Ana", profile.calendar.parse("2185-01-01").lo)
    after = kg.belief_snapshot("Ana", profile.calendar.parse("2195-01-01").lo)
    assert before.get("E-001") == "unaware"
    assert after.get("E-001") == "knows"
