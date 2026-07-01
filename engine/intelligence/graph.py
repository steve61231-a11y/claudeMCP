from engine.db.neo4j_client import get_driver

UPSERT_USER = "MERGE (u:User {handle: $handle}) RETURN u"
UPSERT_POLITICIAN = "MERGE (p:Politician {id: $id}) SET p.name = $name RETURN p"
UPSERT_MENTIONS_EDGE = """
MATCH (u:User {handle: $handle})
MATCH (p:Politician {id: $politician_id})
MERGE (u)-[r:MENTIONS]->(p)
ON CREATE SET r.count = 1
ON MATCH SET r.count = r.count + 1
"""
UPSERT_AMPLIFIES_EDGE = """
MATCH (u:User {handle: $handle})
MATCH (p:Politician {id: $politician_id})
MERGE (u)-[r:AMPLIFIES {platform: $platform}]->(p)
ON CREATE SET r.shares = $shares
ON MATCH SET r.shares = r.shares + $shares
"""


def upsert_mentions(politician_id: str, politician_name: str, mentions: list[dict]) -> None:
    """Incrementally upserts nodes/edges for this run's mentions (not a full rebuild)."""
    driver = get_driver()
    with driver.session() as session:
        session.run(UPSERT_POLITICIAN, id=politician_id, name=politician_name)
        for mention in mentions:
            session.run(UPSERT_USER, handle=mention["author_handle"])
            session.run(UPSERT_MENTIONS_EDGE, handle=mention["author_handle"], politician_id=politician_id)
            shares = mention["engagement"].get("shares", 0)
            if shares > 0:
                session.run(
                    UPSERT_AMPLIFIES_EDGE,
                    handle=mention["author_handle"],
                    politician_id=politician_id,
                    platform=mention["platform"],
                    shares=shares,
                )


UPSERT_PERSON = """
MERGE (person:Person {canonical_key: $canonical_key})
SET person.name = $name,
    person.role = coalesce($role, person.role),
    person.affiliation = coalesce($affiliation, person.affiliation)
"""
UPSERT_CO_MENTIONED = """
MATCH (person:Person {canonical_key: $canonical_key})
MATCH (p:Politician {id: $politician_id})
MERGE (person)-[r:CO_MENTIONED]->(p)
ON CREATE SET r.count = $count
ON MATCH SET r.count = r.count + $count
"""
UPSERT_AFFILIATED = """
MATCH (person:Person {canonical_key: $canonical_key})
MERGE (org:Org {name: $org})
MERGE (person)-[:AFFILIATED_WITH]->(org)
"""
UPSERT_INFLUENCER = """
MERGE (i:Influencer {platform: $platform, handle: $handle})
SET i.followers = $followers
WITH i
MATCH (p:Politician {id: $politician_id})
MERGE (i)-[r:CREATES_CONTENT_ABOUT]->(p)
ON CREATE SET r.posts = $posts
ON MATCH SET r.posts = r.posts + $posts
"""


def upsert_people(politician_id: str, people: list[dict]) -> None:
    """Person↔politician co-mention edges plus person→org affiliations.

    `people` items: canonical_key, name, role, affiliation, count. All MERGE —
    idempotent across runs; role/affiliation only fill gaps, never overwrite
    with nulls."""
    if not people:
        return
    driver = get_driver()
    with driver.session() as session:
        for person in people:
            session.run(
                UPSERT_PERSON,
                canonical_key=person["canonical_key"],
                name=person["name"],
                role=person.get("role"),
                affiliation=person.get("affiliation"),
            )
            session.run(
                UPSERT_CO_MENTIONED,
                canonical_key=person["canonical_key"],
                politician_id=politician_id,
                count=person.get("count", 1),
            )
            if person.get("affiliation"):
                session.run(
                    UPSERT_AFFILIATED, canonical_key=person["canonical_key"], org=person["affiliation"]
                )


def upsert_influencers(politician_id: str, influencers: list[dict]) -> None:
    """Creators (≥ follower threshold) making content about the politician.
    `influencers` items: platform, handle, followers, posts."""
    if not influencers:
        return
    driver = get_driver()
    with driver.session() as session:
        for inf in influencers:
            session.run(
                UPSERT_INFLUENCER,
                politician_id=politician_id,
                platform=inf["platform"],
                handle=inf["handle"],
                followers=inf.get("followers", 0),
                posts=inf.get("posts", 1),
            )


def get_network_snapshot(politician_id: str, limit: int = 50) -> dict:
    """People-first network view: who specifically surrounds the politician
    (named people with roles/affiliations, influencers by reach), plus the
    legacy top-authors list."""
    driver = get_driver()
    users_query = """
    MATCH (u:User)-[r:MENTIONS]->(p:Politician {id: $politician_id})
    RETURN u.handle AS handle, r.count AS mentions
    ORDER BY r.count DESC
    LIMIT $limit
    """
    people_query = """
    MATCH (person:Person)-[r:CO_MENTIONED]->(p:Politician {id: $politician_id})
    RETURN person.name AS name, person.role AS role, person.affiliation AS affiliation,
           r.count AS co_mentions
    ORDER BY r.count DESC
    LIMIT $limit
    """
    influencers_query = """
    MATCH (i:Influencer)-[r:CREATES_CONTENT_ABOUT]->(p:Politician {id: $politician_id})
    RETURN i.handle AS handle, i.platform AS platform, i.followers AS followers, r.posts AS posts
    ORDER BY i.followers DESC
    LIMIT $limit
    """
    with driver.session() as session:
        users = [
            {"handle": r["handle"], "mentions": r["mentions"]}
            for r in session.run(users_query, politician_id=politician_id, limit=limit)
        ]
        people = [
            {
                "name": r["name"],
                "role": r["role"],
                "affiliation": r["affiliation"],
                "co_mentions": r["co_mentions"],
            }
            for r in session.run(people_query, politician_id=politician_id, limit=limit)
        ]
        influencers = [
            {
                "handle": r["handle"],
                "platform": r["platform"],
                "followers": r["followers"],
                "posts": r["posts"],
            }
            for r in session.run(influencers_query, politician_id=politician_id, limit=limit)
        ]
    return {
        "politician_id": politician_id,
        "top_users": users,
        "top_people": people,
        "top_influencers": influencers,
    }
