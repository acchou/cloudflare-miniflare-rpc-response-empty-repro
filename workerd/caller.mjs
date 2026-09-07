export default {
  async fetch(request, env) {
    const pathname = new URL(request.url).pathname;
    let response = pathname === "/no-rpc"
      ? Response.json({ ok: true })
      : await env.CALLEE.getJson();

    if (pathname !== "/no-await") {
      await new Promise(resolve => setTimeout(resolve, 0));
    }

    if (pathname !== "/no-gzip") {
      response = new Response(response.body, response);
      response.headers.set("Content-Encoding", "gzip");
    }

    return response;
  }
};
