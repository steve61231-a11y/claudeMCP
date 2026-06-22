import { Body, Controller, Param, Post } from '@nestjs/common';
import { EngineClientService } from '../common/engine-client.service';

@Controller('politicians/:id/runs')
export class RunsController {
  constructor(private readonly engine: EngineClientService) {}

  @Post()
  trigger(
    @Param('id') id: string,
    @Body() body: { period: string; window_start: string; window_end: string },
  ) {
    return this.engine.triggerRun(id, body);
  }
}
