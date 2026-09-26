// Temporary authenticated integration-test service. No payment-provider operations.
const json = (value, status = 200) => Response.json(value, { status, headers: { "cache-control": "no-store" } });
const validID = (value) => typeof value === "string" && /^[a-z0-9_-]{1,100}$/.test(value);

async function authorized(request, token) {
  if (!token) return false;
  const supplied = new TextEncoder().encode(request.headers.get("authorization") || "");
  const expected = new TextEncoder().encode(`Bearer ${token}`);
  return supplied.byteLength === expected.byteLength && crypto.subtle.timingSafeEqual(supplied, expected);
}

async function readBody(request) {
  const reader = request.body?.getReader();
  if (!reader) throw new Error("missing_body");
  const chunks = [];
  let size = 0;
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > 8192) { await reader.cancel(); throw new Error("body_too_large"); }
    chunks.push(value);
  }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  return JSON.parse(new TextDecoder().decode(bytes));
}

async function fingerprint(order_id, amount_cents) {
  // Same canonical key ordering as Python request_fingerprint for these fields.
  const text = JSON.stringify({ arguments: { amount_cents, order_id }, tool: "issue_refund" });
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, "0")).join("");
}

export default {
  async fetch(request, env) {
    if (!(await authorized(request, env.BENCHMARK_TOKEN))) return json({ error: "unauthorized" }, 401);
    const url = new URL(request.url);
    // Force every independent request to start on the primary for fresh evidence.
    const db = env.DB.withSession("first-primary");
    try {
      if (url.pathname === "/health" && request.method === "GET") {
        await db.prepare("SELECT 1").first();
        return json({ service: "aae-d1-refund-test", durable_backend: "cloudflare-d1", version: 1 });
      }
      if (url.pathname === "/fixture" && request.method === "POST") {
        const { order_id, mode } = await readBody(request);
        if (!validID(order_id) || !["normal", "false_success"].includes(mode)) return json({ error: "invalid_fixture" }, 400);
        await db.prepare("INSERT INTO orders (id, mode) VALUES (?, ?)").bind(order_id, mode).run();
        return json({ order_id });
      }
      if (url.pathname === "/refund" && request.method === "POST") {
        const { order_id, amount_cents, operation_id } = await readBody(request);
        if (!validID(order_id) || !validID(operation_id) || !Number.isInteger(amount_cents) || amount_cents !== 500) return json({ error: "invalid_refund" }, 400);
        const order = await db.prepare("SELECT mode FROM orders WHERE id = ?").bind(order_id).first();
        if (!order) return json({ error: "unknown_order" }, 404);
        if (order.mode === "false_success") return json({ status: "succeeded", order_id, amount_cents });
        const hash = await fingerprint(order_id, amount_cents);
        // The unique key is the deduplication guarantee. The committed ledger row
        // itself is the effect, avoiding a gap between an effect and its receipt.
        await db.prepare("INSERT INTO refunds (operation_id, order_id, amount_cents, request_sha256) VALUES (?, ?, ?, ?) ON CONFLICT(operation_id) DO NOTHING")
          .bind(operation_id, order_id, amount_cents, hash).run();
        const record = await db.prepare("SELECT * FROM refunds WHERE operation_id = ?").bind(operation_id).first();
        if (record.request_sha256 !== hash) return json({ error: "idempotency_conflict" }, 409);
        return json({ status: "succeeded", receipt_id: operation_id, order_id, amount_cents });
      }
      if (url.pathname === "/status" && request.method === "GET") {
        const operation_id = url.searchParams.get("operation_id");
        if (!validID(operation_id)) return json({ error: "invalid_id" }, 400);
        const record = await db.prepare("SELECT * FROM refunds WHERE operation_id = ?").bind(operation_id).first();
        if (!record) return json({ operation_id, tool: "issue_refund", authoritative: true, status: "not_found" });
        const aggregate = await db.prepare("SELECT COUNT(*) AS count FROM refunds WHERE order_id = ?").bind(record.order_id).first();
        return json({
          operation_id, tool: "issue_refund", authoritative: true, status: "succeeded",
          request_sha256: record.request_sha256, receipt_id: record.operation_id,
          effect: { order_id: record.order_id, amount_cents: record.amount_cents, refund_count: aggregate.count },
        });
      }
      if (url.pathname === "/oracle" && request.method === "GET") {
        const order_id = url.searchParams.get("order_id");
        if (!validID(order_id)) return json({ error: "invalid_id" }, 400);
        // Independent aggregate used only by the benchmark evaluator.
        const actual = await db.prepare("SELECT COUNT(*) AS refund_count, COALESCE(SUM(amount_cents), 0) AS refunded_cents FROM refunds WHERE order_id = ?").bind(order_id).first();
        return json(actual);
      }
      return json({ error: "not_found" }, 404);
    } catch {
      return json({ error: "service_error" }, 500);
    }
  },
};
