import { WorkerEntrypoint } from "cloudflare:workers";

export default class extends WorkerEntrypoint {
  async getJson() {
    return Response.json({ ok: true });
  }
}
