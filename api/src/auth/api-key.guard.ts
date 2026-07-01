import {
  CanActivate,
  ExecutionContext,
  Injectable,
  ServiceUnavailableException,
  UnauthorizedException,
} from '@nestjs/common';
import { timingSafeEqual } from 'crypto';

/**
 * Global API-key guard for the gateway. Fails closed: without GATEWAY_API_KEY
 * the gateway only serves requests in explicit local-dev, never silently
 * unauthenticated in any other environment.
 */
@Injectable()
export class ApiKeyGuard implements CanActivate {
  canActivate(context: ExecutionContext): boolean {
    const expected = process.env.GATEWAY_API_KEY;
    if (!expected) {
      if ((process.env.APP_ENV ?? 'local-dev') === 'local-dev') {
        return true;
      }
      throw new ServiceUnavailableException('gateway auth is not configured');
    }

    const request = context.switchToHttp().getRequest();
    const provided = request.headers['x-api-key'];
    if (typeof provided !== 'string' || !safeEqual(provided, expected)) {
      throw new UnauthorizedException('invalid or missing API key');
    }
    return true;
  }
}

function safeEqual(a: string, b: string): boolean {
  const bufA = Buffer.from(a);
  const bufB = Buffer.from(b);
  return bufA.length === bufB.length && timingSafeEqual(bufA, bufB);
}
