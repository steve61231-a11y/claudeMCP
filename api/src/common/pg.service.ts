import { Injectable, OnModuleDestroy } from '@nestjs/common';
import { Pool } from 'pg';

@Injectable()
export class PgService implements OnModuleDestroy {
  readonly pool: Pool;

  constructor() {
    let connectionString = process.env.DATABASE_URL;
    if (!connectionString) {
      if ((process.env.APP_ENV ?? 'local-dev') !== 'local-dev') {
        throw new Error("DATABASE_URL must be set explicitly when APP_ENV is not 'local-dev'");
      }
      connectionString = 'postgresql://postgres:postgres@postgres:5432/political_intel';
    }
    this.pool = new Pool({ connectionString });
  }

  query<T = any>(text: string, params: any[] = []) {
    return this.pool.query<T>(text, params);
  }

  onModuleDestroy() {
    return this.pool.end();
  }
}
