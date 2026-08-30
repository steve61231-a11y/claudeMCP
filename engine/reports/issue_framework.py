"""Issue Analysis & Mapping Framework — V1.0.

A direct implementation of the client's framework document, preserving its
two-level structure (L1 descriptive analysis, L2 strategic analysis) and its
explicit controls. Those controls are the point of the framework, not
incidental: the author repeatedly constrains scope — a ten-year cut-off, ~500
words per section, five stakeholders per segment, profiles built "based solely
on the analysis conducted in the above elements" — because an issue map that
sprawls stops being usable and starts being a literature review.

Structure:
  INPUT 1 (L1)  Define the issue — the dependent variable everything else is
                measured against.
  1  Background and context     — outline, international approach, national
                                  approach, timeline of major developments.
  2  Main contours              — narratives and positions: for / against /
                                  neutral, capped at five stakeholders per
                                  segment (public, private, civil society,
                                  development community).
  2  Stakeholder networks       — champions, challengers, neutrals with their
                                  influence and networks, plus a concise
                                  biographical profile per stakeholder for the
                                  hover menu.
  INPUT 2 (L2)  Desired outcome — what the user wants to happen.
  4  Strategic recommendations  — baseline probability of outcomes and the
                                  variables at play, then messaging and who to
                                  engage / influence / convince.
  5  Sequencing                 — when to engage, and when to build coalitions
                                  ahead of the dates that matter.
  6  Data overview              — what the analysis rests on, its controls, and
                                  how to read it. Weak spots are disclosed.
"""

from datetime import datetime, timedelta

# Framework-stated controls. Named constants because they ARE the framework —
# changing one silently would change the deliverable.
BACKGROUND_YEAR_CUTOFF = 10          # "10 year cut off"
SECTION_WORD_LIMIT = 500             # "around 500 for section 1"
PROFILE_WORD_LIMIT = 500             # "around 500 words max per profile"
MAX_STAKEHOLDERS_PER_SEGMENT = 5     # "limit the number of SHs to 5 per segment"

# The framework's four stakeholder segments.
SEGMENTS = ("public", "private", "civil_society", "development")

# The three positions a stakeholder can hold on the issue.
POSITIONS = ("for", "against", "neutral")

# Position -> the framework's own label for that group.
POSITION_ROLE = {"for": "champion", "against": "challenger", "neutral": "neutral"}


def _truncate_words(text: str, limit: int) -> str:
    """Enforce a word budget, flagging that it was applied.

    The framework asks for word limits explicitly; silently exceeding them
    would produce exactly the sprawl it is trying to prevent.
    """
    words = (text or "").split()
    if len(words) <= limit:
        return text or ""
    return " ".join(words[:limit]) + f" […truncated to {limit} words per framework]"


def segment_stakeholder(entity_type: str | None, affiliation: str | None = None) -> str:
    """Place a stakeholder in one of the framework's four segments."""
    kind = (entity_type or "").lower()
    context = (affiliation or "").lower()

    development_markers = ("world bank", "imf", "undp", "usaid", "eu ", "donor",
                           "development", "unicef", "who", "african development")
    civil_markers = ("ngo", "civil society", "activist", "union", "association",
                     "foundation", "advocacy", "watchdog", "society")
    public_markers = ("ministry", "treasury", "authority", "parliament", "senate",
                      "government", "state", "county", "commission", "agency",
                      "judiciary", "court", "president")

    haystack = f"{kind} {context}"
    if any(marker in haystack for marker in development_markers):
        return "development"
    if any(marker in haystack for marker in civil_markers):
        return "civil_society"
    if any(marker in haystack for marker in public_markers):
        return "public"
    if kind in ("company", "business"):
        return "private"
    if kind in ("party", "politician", "person") and any(m in context for m in public_markers):
        return "public"
    return "private" if kind == "company" else "public" if kind == "politician" else "civil_society"


def build_issue_definition(issue: str, principal: str, desired_outcome: str | None = None) -> dict:
    """INPUT 1 — the dependent variable, stated before anything is measured."""
    return {
        "issue": issue,
        "principal": principal,
        "statement": f"{issue} — as it concerns {principal}.",
        "dependent_variable": issue,
        "note": (
            "Everything below is analysed against this definition; a change to it "
            "invalidates the mapping."
        ),
    }


def build_background(payload: dict, events: list[dict], now: datetime | None = None) -> dict:
    """1 — background and context, split international / national, with timeline.

    The ten-year cut-off is applied and disclosed, so a reader knows the horizon
    rather than assuming the record starts where our data does.
    """
    reference = now or datetime.utcnow()
    cutoff = reference - timedelta(days=365 * BACKGROUND_YEAR_CUTOFF)

    in_scope = []
    for event in events:
        occurred = event.get("occurred_at")
        if isinstance(occurred, str):
            try:
                occurred = datetime.fromisoformat(occurred[:19])
            except ValueError:
                occurred = None
        if occurred is None or occurred >= cutoff:
            in_scope.append({**event, "_when": occurred})

    timeline = sorted(
        (e for e in in_scope if e.get("_when")),
        key=lambda e: e["_when"],
    )

    return {
        "outline": _truncate_words(payload.get("issue_outline") or payload.get("summary") or "",
                                   SECTION_WORD_LIMIT),
        "international": _truncate_words(payload.get("international_context") or "", SECTION_WORD_LIMIT),
        "national": _truncate_words(payload.get("national_context") or "", SECTION_WORD_LIMIT),
        "timeline_of_major_developments": [
            {
                "date": e["_when"].date().isoformat(),
                "event": e.get("title"),
                "type": e.get("event_type"),
                "sources": e.get("independent_domains"),
            }
            for e in timeline
        ][:25],
        "controls": {
            "year_cutoff": BACKGROUND_YEAR_CUTOFF,
            "word_limit_per_section": SECTION_WORD_LIMIT,
            "events_in_scope": len(in_scope),
            "events_excluded_by_cutoff": len(events) - len(in_scope),
        },
    }


def build_main_contours(stakeholders: list[dict]) -> dict:
    """2 — narratives and positions: for / against / neutral.

    Capped at five stakeholders per segment as the framework requires. The cap
    is applied by influence, so the five kept are the five that matter.
    """
    contours: dict[str, dict] = {}
    for position in POSITIONS:
        by_segment: dict[str, list[dict]] = {segment: [] for segment in SEGMENTS}
        for stakeholder in stakeholders:
            if stakeholder.get("position") != position:
                continue
            by_segment.setdefault(stakeholder.get("segment", "public"), []).append(stakeholder)

        trimmed = {}
        for segment, group in by_segment.items():
            ranked = sorted(group, key=lambda s: s.get("influence", 0), reverse=True)
            trimmed[segment] = [
                {"name": s["name"], "influence": s.get("influence"),
                 "rationale": s.get("rationale")}
                for s in ranked[:MAX_STAKEHOLDERS_PER_SEGMENT]
            ]
        contours[position] = {
            "segments": trimmed,
            "total_identified": sum(len(v) for v in by_segment.values()),
            "shown": sum(len(v) for v in trimmed.values()),
        }

    return {
        "positions": contours,
        "controls": {"max_stakeholders_per_segment": MAX_STAKEHOLDERS_PER_SEGMENT,
                     "segments": list(SEGMENTS)},
    }


def build_stakeholder_networks(stakeholders: list[dict], relationships: list[dict]) -> dict:
    """2 — champions, challengers, neutrals: influence, networks, profiles.

    Profiles are for the hover menu the framework describes: history, track
    record, modus operandi and position, kept to the stated word budget.
    """
    grouped: dict[str, list[dict]] = {"champions": [], "challengers": [], "neutral": []}
    bucket = {"for": "champions", "against": "challengers", "neutral": "neutral"}

    for stakeholder in sorted(stakeholders, key=lambda s: s.get("influence", 0), reverse=True):
        key = bucket.get(stakeholder.get("position", "neutral"), "neutral")
        network = [
            {"name": rel["target"], "relationship": rel.get("rel_type"),
             "strength": rel.get("weight")}
            for rel in relationships
            if rel.get("source") == stakeholder["name"]
        ][:8]
        grouped[key].append(
            {
                "name": stakeholder["name"],
                "segment": stakeholder.get("segment"),
                "role": POSITION_ROLE.get(stakeholder.get("position", "neutral")),
                "influence": stakeholder.get("influence"),
                "network": network,
                # The hover profile the framework specifies.
                "profile": {
                    "history": _truncate_words(stakeholder.get("history", ""), PROFILE_WORD_LIMIT),
                    "track_record": _truncate_words(stakeholder.get("track_record", ""), PROFILE_WORD_LIMIT),
                    "modus_operandi": _truncate_words(stakeholder.get("modus_operandi", ""), PROFILE_WORD_LIMIT),
                    "position_on_issue": stakeholder.get("rationale", ""),
                    "word_limit": PROFILE_WORD_LIMIT,
                },
            }
        )

    return {
        **grouped,
        "visualisation": {
            "nodes": [
                {"id": s["name"], "group": s.get("position", "neutral"),
                 "segment": s.get("segment"), "value": s.get("influence", 1)}
                for s in stakeholders
            ],
            "edges": [
                {"from": rel.get("source"), "to": rel.get("target"),
                 "label": rel.get("rel_type"), "weight": rel.get("weight")}
                for rel in relationships
            ],
        },
        "controls": {"profile_word_limit": PROFILE_WORD_LIMIT,
                     "derived_from": "the stakeholders identified in section 2 only"},
    }


def build_desired_outcome(desired_outcome: str | None) -> dict:
    """INPUT 2 (L2) — the user's intended outcome.

    L2 is optional in the framework ("we can also just leave the L2 analysis
    out"), so its absence is reported plainly and the strategic sections below
    say what they cannot compute without it.
    """
    if not desired_outcome:
        return {
            "provided": False,
            "outcome": None,
            "note": "No desired outcome supplied — L2 strategic analysis is limited to what L1 supports.",
        }
    return {"provided": True, "outcome": desired_outcome,
            "note": "Strategic recommendations below are oriented to this outcome."}


_POSITION_ALIASES = {
    "for": "for", "pro": "for", "support": "for", "supportive": "for",
    "supporter": "for", "champion": "for", "in favour": "for", "in favor": "for",
    "favourable": "for", "favorable": "for", "positive": "for", "ally": "for",
    "against": "against", "anti": "against", "opposed": "against",
    "opposition": "against", "opponent": "against", "critic": "against",
    "critical": "against", "hostile": "against", "negative": "against",
    "neutral": "neutral", "undecided": "neutral", "mixed": "neutral",
    "unclear": "neutral", "unknown": "neutral", "none": "neutral", "": "neutral",
}


def normalise_position(value) -> str:
    """Map a model's stance label onto for/against/neutral.

    Anything unrecognised becomes "neutral" rather than disappearing: a
    stakeholder the analyst identified belongs in the section whatever word was
    used for their stance, and neutral is the honest default when the label
    cannot be read."""
    text = str(value or "").strip().lower()
    if text in _POSITION_ALIASES:
        return _POSITION_ALIASES[text]
    for alias, canonical in _POSITION_ALIASES.items():
        if alias and alias in text:
            return canonical
    return "neutral"


def build_strategic_recommendations(desired: dict, contours: dict, stakeholders: list[dict]) -> dict:
    """4 — probability of outcomes, then messaging and engagement targets."""
    # Exact string matching on a model's label silently dropped stakeholders
    # from ALL THREE buckets: "For", "supportive", "pro" and "opposed" are the
    # same three positions written differently, and each one vanished the
    # person from the section entirely. Same failure shape as the quote matcher.
    champions = [s for s in stakeholders if normalise_position(s.get("position")) == "for"]
    challengers = [s for s in stakeholders if normalise_position(s.get("position")) == "against"]
    neutrals = [s for s in stakeholders if normalise_position(s.get("position")) == "neutral"]

    def _top(group: list[dict], limit: int = 5) -> list[dict]:
        return [
            {"name": s["name"], "segment": s.get("segment"), "influence": s.get("influence"),
             "approach": s.get("approach")}
            for s in sorted(group, key=lambda s: s.get("influence", 0), reverse=True)[:limit]
        ]

    # A simple probability tree, as the framework asks — weighted by the
    # influence behind each position rather than a headcount, since one
    # decisive actor outweighs several marginal ones.
    def _weight(group: list[dict]) -> float:
        return float(sum(s.get("influence", 0) or 0 for s in group))

    for_w, against_w, neutral_w = _weight(champions), _weight(challengers), _weight(neutrals)
    total_w = for_w + against_w + neutral_w

    tree = {
        "baseline": {
            "desired_outcome_prevails": round(100 * for_w / total_w, 1) if total_w else None,
            "opposed_outcome_prevails": round(100 * against_w / total_w, 1) if total_w else None,
            "undetermined": round(100 * neutral_w / total_w, 1) if total_w else None,
        },
        "basis": "Influence-weighted balance of identified stakeholder positions.",
        "major_variables": [
            "Movement of neutral stakeholders (largest single swing factor)",
            "Position of the most influential public-sector actors",
            "Timing relative to the formal policy calendar",
        ],
        "caveat": (
            "A baseline from observed positions, not a forecast. It shifts with any "
            "change in stakeholder alignment."
        ),
    }

    return {
        "probability_tree": tree,
        "recommended_messaging": (
            f"Position the argument so it aligns with the interests of the most influential "
            f"stakeholders relative to the stated outcome: {desired.get('outcome')}"
            if desired.get("provided")
            else "Messaging requires a stated desired outcome (INPUT 2) to be meaningful."
        ),
        "who_to_engage_champions": _top(champions),
        "who_to_influence_challengers": _top(challengers),
        "who_to_convince_neutral": _top(neutrals),
        "best_engagement_approaches": [
            "Prioritise technocrats over political appointees for substantive policy change",
            "Build the coalition among champions before approaching challengers",
            "Convert the highest-influence neutrals first — they move the baseline most",
        ],
    }


def build_sequencing(background: dict, now: datetime | None = None) -> dict:
    """5 — engagement timeline, including when to build coalitions.

    The framework's emphasis: coalition-building happens BEFORE the dates that
    matter, not during them.
    """
    reference = now or datetime.utcnow()
    upcoming = [
        item for item in background.get("timeline_of_major_developments", [])
        if item.get("date") and item["date"] >= reference.date().isoformat()
    ]
    return {
        "engagement_timeline": upcoming[:15],
        "coalition_windows": [
            {
                "before": item["date"],
                "action": f"Build alignment ahead of: {item.get('event')}",
                "rationale": "Coalitions must be in place before the decision point, not after.",
            }
            for item in upcoming[:5]
        ],
        "note": (
            "Derived from dated developments in the record; policy calendars supplied by "
            "the client should be merged in."
        ),
    }


def build_data_overview(payload: dict, stakeholders: list[dict], events: list[dict]) -> dict:
    """6 — what the analysis rests on, its controls, and how to read it.

    The framework's stated purpose is to make the user aware of weak spots, so
    limitations are listed as prominently as strengths.
    """
    # The gate result lives under `acquisition`, where _acquire_and_store puts
    # it. Reading the top level meant "relevance gate: not run" was printed on
    # every issue map ever produced, including the ones where it did run.
    acquisition = payload.get("acquisition") or {}
    gate = payload.get("evidence_gate") or acquisition.get("evidence_gate") or {}
    relevance_filter = acquisition.get("relevance_filter") or {}
    verification = payload.get("verification") or {}
    credibility = payload.get("source_credibility") or {}

    limitations = []
    if verification.get("unverified"):
        limitations.append(
            f"{verification['unverified']} claim(s) could not be corroborated from the stored evidence."
        )
    single_source = [e for e in events if (e.get("independent_domains") or 0) <= 1]
    if single_source:
        limitations.append(f"{len(single_source)} event(s) rest on a single source.")
    if gate.get("ambiguous"):
        limitations.append(
            f"{gate['ambiguous']} document(s) could not be confidently attributed to the subject."
        )
    if not stakeholders:
        limitations.append("No stakeholders were identified — the mapping below is not yet supported.")

    return {
        "data_used": {
            "documents_examined": gate.get("examined"),
            "documents_on_topic": gate.get("on_topic"),
            "events_resolved": len(events),
            "stakeholders_identified": len(stakeholders),
            "sources_scored": credibility.get("scored"),
        },
        "controls_applied": {
            "background_year_cutoff": BACKGROUND_YEAR_CUTOFF,
            "section_word_limit": SECTION_WORD_LIMIT,
            "stakeholders_per_segment": MAX_STAKEHOLDERS_PER_SEGMENT,
            "profile_word_limit": PROFILE_WORD_LIMIT,
            "relevance_gate": "applied" if gate else "not run",
            "relevance_filter": (
                f"{relevance_filter['kept']} of {relevance_filter['examined']} documents "
                f"mentioned both terms"
                + (" and this market" if relevance_filter.get("market_anchored") else "")
                if relevance_filter else "not run"),
            "claim_verification": "applied" if verification else "not run",
        },
        "limitations": limitations or ["No material limitations identified in this run."],
        "how_to_read": (
            "Positions are inferred from observed coverage, not declared by the stakeholders "
            "themselves. Probabilities are an influence-weighted baseline of those positions, "
            "not a forecast. Treat single-source events as unconfirmed until corroborated."
        ),
    }


def build(issue: str, principal: str, payload: dict, stakeholders: list[dict],
          relationships: list[dict], events: list[dict],
          desired_outcome: str | None = None, now: datetime | None = None) -> dict:
    """Assemble the full framework in its documented order."""
    definition = build_issue_definition(issue, principal, desired_outcome)
    background = build_background(payload, events, now=now)
    contours = build_main_contours(stakeholders)
    networks = build_stakeholder_networks(stakeholders, relationships)
    desired = build_desired_outcome(desired_outcome)

    return {
        "framework": "Issue Analysis & Mapping Framework V1.0",
        "generated_at": (now or datetime.utcnow()).isoformat(),
        "input_1_issue_definition": definition,
        "background_and_context": background,
        "main_contours": contours,
        "stakeholder_networks": networks,
        "input_2_desired_outcome": desired,
        "strategic_recommendations": build_strategic_recommendations(desired, contours, stakeholders),
        "sequencing": build_sequencing(background, now=now),
        "data_overview": build_data_overview(payload, stakeholders, events),
    }
