import { Module } from '@nestjs/common';
import { PgService } from '../common/pg.service';
import { ReportsController } from './reports.controller';

@Module({
  controllers: [ReportsController],
  providers: [PgService],
})
export class ReportsModule {}
