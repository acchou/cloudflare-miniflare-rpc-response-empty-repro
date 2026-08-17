const routes = [
  ["affected: await + gzip", "/"],
  ["control: no await", "/no-await"],
  ["control: no gzip", "/no-gzip"]
];

let observedFailure = false;
for (const [label, pathname] of routes) {
  const response = await fetch(new URL(pathname, "http://127.0.0.1:8080"));
  const body = await response.text();
  console.log(
    `${label}: status=${response.status} encoding=${response.headers.get("content-encoding") ?? "identity"} body=${JSON.stringify(body)}`
  );
  if (pathname === "/" && response.status === 200 && body === "") {
    observedFailure = true;
  }
}

if (!observedFailure) {
  console.log("The affected route retained its body; this workerd build is not affected.");
}
