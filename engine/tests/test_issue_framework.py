"""Issue Analysis & Mapping Framework V1.0 — conformance tests.

The framework's controls ARE the framework: a ten-year cut-off, ~500 words per
section and per profile, five stakeholders per segment, analysis derived only
from what came before it. An implementation that quietly exceeds them produces
the sprawl the document exists to prevent, so each control is pinned here.
"""

from datetime import datetime, timedelta

from engine.reports import issue_framework as ifw


def _stakeholder(name, position="for", segment="public", influence=10, **kwargs):
    base = {"name": name, "position": position, "segment": segment,
            "influence": influence, "rationale": "r"}
    base.update(kwargs)
    return base


def _event(title, days_ago=30, domains=2, etype="policy"):
    return {"title": title, "occurred_at": datetime.utcnow() - timedelta(days=days_ago),
            "event_type": etype, "independent_domains": domains}


# --- INPUT 1 ---------------------------------------------------------------

def test_issue_definition_is_the_dependent_variable():
    definition = ifw.build_issue_definition("Digital Services Tax", "Treasury")
    assert definition["dependent_variable"] == "Digital Services Tax"
    assert "invalidates the mapping" in definition["note"]


# --- 1 Background and context ----------------------------------------------

def test_ten_year_cutoff_is_applied_and_disclosed():
    """'Brief outline of issue (10 year cut off)'"""
    events = [_event("Recent development", days_ago=100),
              _event("Ancient history", days_ago=365 * 12)]

    background = ifw.build_background({}, events)

    assert background["controls"]["year_cutoff"] == 10
    assert background["controls"]["events_excluded_by_cutoff"] == 1
    titles = [item["event"] for item in background["timeline_of_major_developments"]]
    assert "Ancient history" not in titles


def test_section_word_limit_is_enforced():
    """'Can we put a word count limit for sections? ... around 500'"""
    long_text = " ".join(["word"] * 900)
    background = ifw.build_background({"summary": long_text}, [])

    assert len(background["outline"].split()) <= ifw.SECTION_WORD_LIMIT + 10
    assert "truncated" in background["outline"]


def test_background_separates_international_and_national():
    background = ifw.build_background(
        {"international_context": "OECD approach", "national_context": "Kenyan approach"}, [])
    assert background["international"] == "OECD approach"
    assert background["national"] == "Kenyan approach"


def test_timeline_is_ordered_by_date():
    events = [_event("Later", days_ago=10), _event("Earlier", days_ago=200)]
    background = ifw.build_background({}, events)
    dates = [item["date"] for item in background["timeline_of_major_developments"]]
    assert dates == sorted(dates)


# --- 2 Main contours -------------------------------------------------------

def test_positions_are_for_against_and_neutral():
    contours = ifw.build_main_contours([
        _stakeholder("A", "for"), _stakeholder("B", "against"), _stakeholder("C", "neutral")])
    assert set(contours["positions"].keys()) == {"for", "against", "neutral"}


def test_five_stakeholder_cap_per_segment_keeps_the_most_influential():
    """'We can also limit the number of SHs to 5 per segment'"""
    crowd = [_stakeholder(f"SH{i}", "for", "public", influence=i) for i in range(10)]
    contours = ifw.build_main_contours(crowd)

    public = contours["positions"]["for"]["segments"]["public"]
    assert len(public) == ifw.MAX_STAKEHOLDERS_PER_SEGMENT
    # The cap must keep what matters, not the first five encountered.
    assert [s["name"] for s in public] == ["SH9", "SH8", "SH7", "SH6", "SH5"]
    assert contours["positions"]["for"]["total_identified"] == 10
    assert contours["positions"]["for"]["shown"] == 5


def test_four_segments_are_the_frameworks_own():
    """public, private, civil society, development community"""
    assert set(ifw.SEGMENTS) == {"public", "private", "civil_society", "development"}


def test_stakeholder_segmentation_recognises_each_kind():
    assert ifw.segment_stakeholder("organization", "National Treasury") == "public"
    assert ifw.segment_stakeholder("company", "Acme Ltd") == "private"
    assert ifw.segment_stakeholder("organization", "Civil Society Watchdog") == "civil_society"
    assert ifw.segment_stakeholder("organization", "World Bank") == "development"


# --- 2 Stakeholder networks ------------------------------------------------

def test_champions_challengers_and_neutrals_use_framework_naming():
    networks = ifw.build_stakeholder_networks(
        [_stakeholder("A", "for"), _stakeholder("B", "against"), _stakeholder("C", "neutral")], [])
    assert networks["champions"][0]["name"] == "A"
    assert networks["challengers"][0]["name"] == "B"
    assert networks["neutral"][0]["name"] == "C"
    assert networks["champions"][0]["role"] == "champion"


def test_hover_profile_carries_the_specified_fields_within_budget():
    """'History, track record, modus operandi, position on the issue' —
    'around 500 words max per profile'"""
    long_text = " ".join(["word"] * 900)
    networks = ifw.build_stakeholder_networks(
        [_stakeholder("A", history=long_text, track_record="tr", modus_operandi="mo")], [])

    profile = networks["champions"][0]["profile"]
    for field in ("history", "track_record", "modus_operandi", "position_on_issue"):
        assert field in profile
    assert len(profile["history"].split()) <= ifw.PROFILE_WORD_LIMIT + 10


def test_network_visualisation_is_produced():
    """'Map out main stakeholders and their networks (visualisation)'"""
    networks = ifw.build_stakeholder_networks(
        [_stakeholder("A"), _stakeholder("B", "against")],
        [{"source": "A", "target": "B", "rel_type": "rival_of", "weight": 3}])

    viz = networks["visualisation"]
    assert len(viz["nodes"]) == 2 and len(viz["edges"]) == 1
    assert networks["champions"][0]["network"][0]["name"] == "B"


# --- INPUT 2 / 4 Strategic recommendations ---------------------------------

def test_missing_desired_outcome_limits_l2_rather_than_inventing_it():
    """The framework allows L2 to be left out entirely."""
    desired = ifw.build_desired_outcome(None)
    assert desired["provided"] is False

    recommendations = ifw.build_strategic_recommendations(desired, {}, [_stakeholder("A")])
    assert "requires a stated desired outcome" in recommendations["recommended_messaging"]


def test_probability_tree_is_influence_weighted_not_a_headcount():
    """One decisive actor outweighs several marginal ones."""
    stakeholders = [
        _stakeholder("Decisive", "for", influence=100),
        _stakeholder("Minor1", "against", influence=5),
        _stakeholder("Minor2", "against", influence=5),
        _stakeholder("Minor3", "against", influence=5),
    ]
    tree = ifw.build_strategic_recommendations(
        ifw.build_desired_outcome("Outcome"), {}, stakeholders)["probability_tree"]

    assert tree["baseline"]["desired_outcome_prevails"] > tree["baseline"]["opposed_outcome_prevails"]
    assert "not a forecast" in tree["caveat"]


def test_engagement_targets_map_to_the_frameworks_three_verbs():
    """engage (champions) / influence (challengers) / convince (neutral)"""
    recommendations = ifw.build_strategic_recommendations(
        ifw.build_desired_outcome("Outcome"), {},
        [_stakeholder("A", "for"), _stakeholder("B", "against"), _stakeholder("C", "neutral")])

    assert recommendations["who_to_engage_champions"][0]["name"] == "A"
    assert recommendations["who_to_influence_challengers"][0]["name"] == "B"
    assert recommendations["who_to_convince_neutral"][0]["name"] == "C"


# --- 5 Sequencing ----------------------------------------------------------

def test_coalitions_are_scheduled_before_the_dates_that_matter():
    future = datetime.utcnow() + timedelta(days=30)
    background = {"timeline_of_major_developments": [
        {"date": future.date().isoformat(), "event": "Finance Bill public participation"}]}

    sequencing = ifw.build_sequencing(background)

    assert sequencing["coalition_windows"]
    window = sequencing["coalition_windows"][0]
    assert "before the decision point" in window["rationale"]


def test_past_events_are_not_offered_as_engagement_opportunities():
    past = {"timeline_of_major_developments": [
        {"date": (datetime.utcnow() - timedelta(days=30)).date().isoformat(), "event": "Gone"}]}
    assert ifw.build_sequencing(past)["engagement_timeline"] == []


# --- 6 Data overview -------------------------------------------------------

def test_weak_spots_are_disclosed_not_hidden():
    """'To ensure that the user is aware of potential weak spots'"""
    payload = {"evidence_gate": {"examined": 50, "on_topic": 40, "ambiguous": 6},
               "verification": {"unverified": 4}}
    overview = ifw.build_data_overview(payload, [], [_event("solo", domains=1)])

    limitations = " ".join(overview["limitations"]).lower()
    assert "could not be corroborated" in limitations
    assert "single source" in limitations
    assert "no stakeholders" in limitations


def test_controls_applied_are_reported():
    overview = ifw.build_data_overview({}, [_stakeholder("A")], [])
    controls = overview["controls_applied"]
    assert controls["background_year_cutoff"] == 10
    assert controls["stakeholders_per_segment"] == 5
    assert controls["profile_word_limit"] == 500


def test_how_to_read_warns_positions_are_inferred():
    overview = ifw.build_data_overview({}, [], [])
    assert "inferred from observed coverage" in overview["how_to_read"]


# --- whole framework -------------------------------------------------------

def test_every_framework_section_is_present_and_ordered():
    result = ifw.build(
        issue="Digital Services Tax", principal="Treasury", payload={},
        stakeholders=[_stakeholder("KRA")], relationships=[], events=[_event("E")],
        desired_outcome="A softer DST regime",
    )

    assert list(result.keys()) == [
        "framework", "generated_at",
        "input_1_issue_definition",       # INPUT 1 (L1)
        "background_and_context",         # 1
        "main_contours",                  # 2
        "stakeholder_networks",           # 2
        "input_2_desired_outcome",        # INPUT 2 (L2)
        "strategic_recommendations",      # 4
        "sequencing",                     # 5
        "data_overview",                  # 6
    ]
    assert result["framework"] == "Issue Analysis & Mapping Framework V1.0"
