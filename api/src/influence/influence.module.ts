import { Module } from '@nestjs/common';
import { PgService } from '../common/pg.service';
import { InfluenceController } from './influence.controller';

@Module({
  controllers: [InfluenceController],
  providers: [PgService],
})
export class InfluenceModule {}
