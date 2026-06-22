import { Injectable } from '@nestjs/common';
import axios, { AxiosInstance } from 'axios';

@Injectable()
export class EngineClientService {
  private readonly client: AxiosInstance;

  constructor() {
    this.client = axios.create({
      baseURL: process.env.ENGINE_URL ?? 'http://engine:8000',
      headers: process.env.INTERNAL_API_TOKEN
        ? { 'X-Internal-Token': process.env.INTERNAL_API_TOKEN }
        : {},
    });
  }

  createPolitician(payload: { name: string; aliases?: string[]; keywords?: string[] }) {
    return this.client.post('/politicians', payload).then((r) => r.data);
  }

  getPolitician(id: string) {
    return this.client.get(`/politicians/${id}`).then((r) => r.data);
  }

  triggerRun(politicianId: string, payload: { period: string; window_start: string; window_end: string }) {
    return this.client.post(`/politicians/${politicianId}/runs`, payload).then((r) => r.data);
  }

  getReport(reportId: string) {
    return this.client.get(`/reports/${reportId}`).then((r) => r.data);
  }
}
