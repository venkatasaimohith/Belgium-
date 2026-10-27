const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function loadApi(http) {
  const BELGIUM = {};
  const context = vm.createContext({
    window: { BELGIUM },
    BELGIUM,
    localStorage: { getItem: () => "[]" },
  });
  vm.runInContext(fs.readFileSync(
    path.join(__dirname, "..", "assets", "api", "belgium-api.js"), "utf8"
  ), context);
  context.BELGIUM.http = http;
  return context.BELGIUM.belgiumApi;
}

for (const size of [0, 50, 1001]) {
  test(`loads all ${size} records for client-side pagination and search`, async () => {
    const records = Array.from({ length: size }, (_, i) => ({ id: i + 1 }));
    const calls = [];
    const api = loadApi(async (url) => {
      calls.push(url);
      const params = new URL(url, "http://localhost").searchParams;
      const page = Number(params.get("page"));
      const limit = Number(params.get("limit"));
      assert.equal(limit, 500);
      return {
        count: size,
        records: records.slice((page - 1) * limit, page * limit),
        total_pages: Math.max(1, Math.ceil(size / limit)),
      };
    });
    const result = await api.getJobRecords("real-job");
    assert.equal(result.count, size);
    assert.equal(result.records.length, size);
    assert.deepEqual(Array.from(result.records, (row) => row.id), records.map((row) => row.id));
    assert.equal(calls.length, Math.max(1, Math.ceil(size / 500)));
  });
}

test("does not return partial records when a later page fails", async () => {
  const failure = new Error("Records unavailable");
  let calls = 0;
  const api = loadApi(async () => {
    if (++calls === 2) throw failure;
    return { records: [{ id: 1 }], total_pages: 2 };
  });
  await assert.rejects(api.getJobRecords("real-job"), failure);
});

test("preserves the backend error instead of reporting a fake missing project", async () => {
  const failure = new Error("Backend unavailable");
  const api = loadApi(async () => { throw failure; });
  await assert.rejects(api.getJob("real-job"), failure);
});
