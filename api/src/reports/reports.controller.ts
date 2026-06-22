import { Controller, Get, Param, Query } from '@nestjs/common';
import { PgService } from '../common/pg.service';

@Controller('politicians/:id/reports')
export class ReportsController {
  constructor(private readonly pg: PgService) {}

  @Get()
  async list(@Param('id') id: string, @Query('period') period?: string) {
    const conditions = ['politician_id = $1'];
    const params: any[] = [id];
    if (period) {
      conditions.push('period = $2');
      params.push(period);
    }
    const result = await this.pg.query(
      `SELECT id, period, window_start, window_end, payload, generated_at
       FROM intelligence_reports WHERE ${conditions.join(' AND ')}
       ORDER BY generated_at DESC`,
      params,
    );
    return result.rows;
  }
}
