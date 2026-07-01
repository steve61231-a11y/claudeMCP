import { Injectable, OnModuleDestroy } from '@nestjs/common';
import neo4j, { Driver } from 'neo4j-driver';

@Injectable()
export class Neo4jService implements OnModuleDestroy {
  readonly driver: Driver;

  constructor() {
    let password = process.env.NEO4J_PASSWORD;
    if (!password) {
      if ((process.env.APP_ENV ?? 'local-dev') !== 'local-dev') {
        throw new Error("NEO4J_PASSWORD must be set explicitly when APP_ENV is not 'local-dev'");
      }
      password = 'password';
    }
    this.driver = neo4j.driver(
      process.env.NEO4J_URI ?? 'bolt://neo4j:7687',
      neo4j.auth.basic(process.env.NEO4J_USER ?? 'neo4j', password),
    );
  }

  async run(query: string, params: Record<string, any> = {}) {
    const session = this.driver.session();
    try {
      const result = await session.run(query, params);
      return result.records.map((r) => r.toObject());
    } finally {
      await session.close();
    }
  }

  onModuleDestroy() {
    return this.driver.close();
  }
}
