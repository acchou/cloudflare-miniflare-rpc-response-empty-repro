export default {
  async fetch(request, env) {
    const pathname = new URL(request.url).pathname;
    if (pathname === "/direct") {
      return await env.ACCOUNT.createAuthToken();
    }
    if (pathname === "/materialized") {
      const response = await env.ACCOUNT.createAuthToken();
      return new Response(await response.arrayBuffer(), response);
    }
    return new Response("Not Found", { status: 404 });
  }
};
