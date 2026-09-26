CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY,
    mode TEXT NOT NULL CHECK(mode IN ('normal', 'false_success'))
);
CREATE TABLE IF NOT EXISTS refunds (
    operation_id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL REFERENCES orders(id),
    amount_cents INTEGER NOT NULL CHECK(amount_cents = 500),
    request_sha256 TEXT NOT NULL,
    committed_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS refunds_order ON refunds(order_id);
