import { Module } from '@nestjs/common';
import { ConfigModule } from '@nestjs/config';
import { APP_GUARD } from '@nestjs/core';
import { ApiKeyGuard } from './auth/api-key.guard';
import { InfluenceModule } from './influence/influence.module';
import { NarrativesModule } from './narratives/narratives.module';
import { NetworkModule } from './network/network.module';
import { PoliticiansModule } from './politicians/politicians.module';
import { ReportsModule } from './reports/reports.module';
import { RunsModule } from './runs/runs.module';

@Module({
  imports: [
    ConfigModule.forRoot({ isGlobal: true }),
    PoliticiansModule,
    RunsModule,
    ReportsModule,
    NarrativesModule,
    InfluenceModule,
    NetworkModule,
  ],
  providers: [{ provide: APP_GUARD, useClass: ApiKeyGuard }],
})
export class AppModule {}
