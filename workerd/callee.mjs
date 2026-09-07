import { WorkerEntrypoint } from "cloudflare:workers";

export default class extends WorkerEntrypoint {
  // Deployed Workers require an event handler even when used only through RPC.
  fetch() {
    return new Response("Not Found", { status: 404 });
  }

  async getJson() {
    return Response.json({ ok: true });
  }
}
