# Pure-workerd reproducer: an RPC Response loses its body when the caller awaits
# before returning it and workerd compresses the response during egress.
using Workerd = import "/workerd/workerd.capnp";

const config :Workerd.Config = (
  services = [
    (name = "caller", worker = .caller),
    (name = "callee", worker = .callee),
  ],
  sockets = [
    (name = "http", address = "127.0.0.1:8080", http = (), service = "caller"),
  ],
);

const caller :Workerd.Worker = (
  modules = [
    (name = "caller.mjs", esModule = embed "caller.mjs"),
  ],
  compatibilityDate = "2026-04-09",
  bindings = [
    (name = "CALLEE", service = "callee"),
  ],
);

const callee :Workerd.Worker = (
  modules = [
    (name = "callee.mjs", esModule = embed "callee.mjs"),
  ],
  compatibilityDate = "2026-04-09",
);
