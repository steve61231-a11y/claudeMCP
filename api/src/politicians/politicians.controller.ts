import { Body, Controller, Get, Param, Post } from '@nestjs/common';
import { EngineClientService } from '../common/engine-client.service';

@Controller('politicians')
export class PoliticiansController {
  constructor(private readonly engine: EngineClientService) {}

  @Post()
  create(@Body() body: { name: string; aliases?: string[]; keywords?: string[] }) {
    return this.engine.createPolitician(body);
  }

  @Get(':id')
  get(@Param('id') id: string) {
    return this.engine.getPolitician(id);
  }
}
