import { WorkerEntrypoint } from "cloudflare:workers";

export default class AccountWorker extends WorkerEntrypoint {
  async fetch() {
    return new Response("Not Found", { status: 404 });
  }

  async createAuthToken() {
    return Response.json({ ok: true });
  }
}
