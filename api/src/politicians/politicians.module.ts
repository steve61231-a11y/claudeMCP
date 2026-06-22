import { Module } from '@nestjs/common';
import { EngineClientService } from '../common/engine-client.service';
import { PoliticiansController } from './politicians.controller';

@Module({
  controllers: [PoliticiansController],
  providers: [EngineClientService],
})
export class PoliticiansModule {}
