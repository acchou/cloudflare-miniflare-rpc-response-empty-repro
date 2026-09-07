export default {
  async fetch(request, env) {
    const pathname = new URL(request.url).pathname;
    let response = pathname === "/no-rpc"
      ? Response.json({ ok: true })
      : await env.CALLEE.getJson();

    if (pathname === "/microtask") {
      await Promise.resolve();
    } else if (pathname !== "/no-await") {
      await new Promise(resolve => setTimeout(resolve, 0));
    }

    if (pathname !== "/no-gzip") {
      response = new Response(response.body, response);
    }

    if (pathname !== "/no-gzip" && pathname !== "/wrapped-no-gzip") {
      response.headers.set("Content-Encoding", "gzip");
    }

    return response;
  }
};
