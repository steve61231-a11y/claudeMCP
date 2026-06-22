import { Controller, Get, Param } from '@nestjs/common';
import { PgService } from '../common/pg.service';

@Controller('politicians/:id/narratives')
export class NarrativesController {
  constructor(private readonly pg: PgService) {}

  @Get()
  async list(@Param('id') id: string) {
    const result = await this.pg.query(
      `SELECT n.id, n.label, n.description, m.window_start, m.window_end, m.strength_score, m.growth_rate
       FROM narratives n
       JOIN narrative_metrics m ON m.narrative_id = n.id
       WHERE n.politician_id = $1
       ORDER BY m.strength_score DESC`,
      [id],
    );
    return result.rows;
  }
}
