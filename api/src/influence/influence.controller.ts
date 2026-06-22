import { Controller, Get, Param } from '@nestjs/common';
import { PgService } from '../common/pg.service';

@Controller('politicians/:id/influence')
export class InfluenceController {
  constructor(private readonly pg: PgService) {}

  @Get()
  async latest(@Param('id') id: string) {
    const result = await this.pg.query(
      `SELECT payload->'influence_summary' AS influence_summary
       FROM intelligence_reports WHERE politician_id = $1
       ORDER BY generated_at DESC LIMIT 1`,
      [id],
    );
    return result.rows[0]?.influence_summary ?? [];
  }
}
