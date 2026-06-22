import { Module } from '@nestjs/common';
import { Neo4jService } from '../common/neo4j.service';
import { NetworkController } from './network.controller';

@Module({
  controllers: [NetworkController],
  providers: [Neo4jService],
})
export class NetworkModule {}
