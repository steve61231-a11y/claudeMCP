import { Controller, Get, Param } from '@nestjs/common';
import { Neo4jService } from '../common/neo4j.service';

@Controller('politicians/:id/network')
export class NetworkController {
  constructor(private readonly neo4j: Neo4jService) {}

  @Get()
  async snapshot(@Param('id') id: string) {
    const rows = await this.neo4j.run(
      `MATCH (u:User)-[r:MENTIONS]->(p:Politician {id: $id})
       RETURN u.handle AS handle, r.count AS mentions
       ORDER BY r.count DESC LIMIT 50`,
      { id },
    );
    return { politician_id: id, top_users: rows };
  }
}
