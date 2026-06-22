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


def get_network_snapshot(politician_id: str, limit: int = 50) -> dict:
    driver = get_driver()
    query = """
    MATCH (u:User)-[r:MENTIONS]->(p:Politician {id: $politician_id})
    RETURN u.handle AS handle, r.count AS mentions
    ORDER BY r.count DESC
    LIMIT $limit
    """
    with driver.session() as session:
        records = session.run(query, politician_id=politician_id, limit=limit)
        nodes = [{"handle": r["handle"], "mentions": r["mentions"]} for r in records]
    return {"politician_id": politician_id, "top_users": nodes}
