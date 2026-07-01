import { Controller, Get, Param } from '@nestjs/common';
import { Neo4jService } from '../common/neo4j.service';

@Controller('politicians/:id/network')
export class NetworkController {
  constructor(private readonly neo4j: Neo4jService) {}

  @Get()
  async snapshot(@Param('id') id: string) {
    const [users, people, influencers] = await Promise.all([
      this.neo4j.run(
        `MATCH (u:User)-[r:MENTIONS]->(p:Politician {id: $id})
         RETURN u.handle AS handle, r.count AS mentions
         ORDER BY r.count DESC LIMIT 50`,
        { id },
      ),
      this.neo4j.run(
        `MATCH (person:Person)-[r:CO_MENTIONED]->(p:Politician {id: $id})
         OPTIONAL MATCH (person)-[:AFFILIATED_WITH]->(org:Org)
         RETURN person.name AS name, person.role AS role,
                coalesce(org.name, person.affiliation) AS affiliation,
                r.count AS co_mentions
         ORDER BY r.count DESC LIMIT 50`,
        { id },
      ),
      this.neo4j.run(
        `MATCH (i:Influencer)-[r:CREATES_CONTENT_ABOUT]->(p:Politician {id: $id})
         RETURN i.handle AS handle, i.platform AS platform,
                i.followers AS followers, r.posts AS posts
         ORDER BY i.followers DESC LIMIT 50`,
        { id },
      ),
    ]);
    return {
      politician_id: id,
      top_users: users,
      top_people: people,
      top_influencers: influencers,
    };
  }
}
