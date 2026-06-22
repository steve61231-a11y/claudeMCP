import { Module } from '@nestjs/common';
import { PgService } from '../common/pg.service';
import { NarrativesController } from './narratives.controller';

@Module({
  controllers: [NarrativesController],
  providers: [PgService],
})
export class NarrativesModule {}
