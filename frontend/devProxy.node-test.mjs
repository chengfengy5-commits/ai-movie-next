import assert from "node:assert/strict";
import test from "node:test";
import { createApiProxyOptions, resolveApiProxyTarget } from "./devProxy.mjs";

test("leaves Vite proxying disabled when the shell variable is unset", () => {
  assert.equal(resolveApiProxyTarget(undefined), undefined);
  assert.equal(createApiProxyOptions(undefined), undefined);
});

test("accepts only the three explicit loopback host forms", () => {
  const cases = [
    ["http://localhost:8000", "http://localhost:8000"],
    ["https://127.0.0.1:8443/", "https://127.0.0.1:8443"],
    ["http://[::1]:9000/", "http://[::1]:9000"],
  ];

  for (const [configured, expected] of cases) {
    assert.equal(resolveApiProxyTarget(configured), expected);
    assert.deepEqual(createApiProxyOptions(configured), {
      target: expected,
      changeOrigin: true,
    });
  }
});

test("normalizes default ports and rejects non-origin or non-loopback targets", () => {
  assert.equal(resolveApiProxyTarget("https://LOCALHOST:443/"), "https://localhost");

  const invalidTargets = [
    "",
    "https://api.example.test:8443",
    "http://127.0.0.1.evil.test:8000",
    "http://localhost.:8000",
    "http://2130706433:8000",
    "http://0177.0.0.1:8000",
    "http://user:pass@127.0.0.1:8000",
    "http://127.0.0.1:8000/api",
    "http://127.0.0.1:8000/api/..",
    "http://127.0.0.1:8000?mode=proxy",
    "http://127.0.0.1:8000#fragment",
    "http://127.0.0.1:8000 ",
    "http://127.0.0.1:8000\n",
    "http://127.0.0.1:8000\\api",
    "ftp://localhost:21",
  ];

  for (const target of invalidTargets) {
    assert.throws(() => resolveApiProxyTarget(target), TypeError, target);
    assert.throws(() => createApiProxyOptions(target), TypeError, target);
  }
});

