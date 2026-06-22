import { Module } from '@nestjs/common';
import { EngineClientService } from '../common/engine-client.service';
import { RunsController } from './runs.controller';

@Module({
  controllers: [RunsController],
  providers: [EngineClientService],
})
export class RunsModule {}
