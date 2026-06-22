import { Injectable, OnModuleDestroy } from '@nestjs/common';
import { Pool } from 'pg';

@Injectable()
export class PgService implements OnModuleDestroy {
  readonly pool: Pool;

  constructor() {
    this.pool = new Pool({
      connectionString:
        process.env.DATABASE_URL ?? 'postgresql://postgres:postgres@postgres:5432/political_intel',
    });
  }

  query<T = any>(text: string, params: any[] = []) {
    return this.pool.query<T>(text, params);
  }

  onModuleDestroy() {
    return this.pool.end();
  }
}
