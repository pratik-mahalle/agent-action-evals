# Live Jev + Cloudflare D1 validation

This service commits synthetic 500-cent refund ledger entries to a real cloud
database. It moves no money and does not call a payment provider. The model makes
live decisions through TypeSafe's [Choice API](https://docs.typesafe.ai/primitives/choice).

The harness uses the public `execute_verified()` implementation. The Jev agent
runs in a bounded Python loop; the LangGraph examples are separate integrations.

## Deploy a temporary service

Use an authorized Cloudflare account to provision a fresh database and Worker.
Choose a unique name, and record both resource IDs for cleanup. Deployment can
use the Cloudflare dashboard or API; the recorded validation used the API:

1. `POST /accounts/{account_id}/d1/database` with a unique `name`,
   `primary_location_hint: "apac"`, and `read_replication: {"mode":"disabled"}`.
2. Execute [schema.sql](schema.sql) using the database's `/query` endpoint.
3. Upload [worker.mjs](worker.mjs) as a module to
   `PUT /accounts/{account_id}/workers/scripts/{name}`. Use
   [multipart metadata](https://developers.cloudflare.com/workers/configuration/multipart-upload-metadata/):

   ```json
   {
     "main_module": "worker.mjs",
     "compatibility_date": "2026-09-26",
     "observability": {"enabled": true},
     "bindings": [
       {"type": "d1", "name": "DB", "database_id": "YOUR_DATABASE_ID"},
       {"type": "secret_text", "name": "BENCHMARK_TOKEN", "text": "YOUR_RANDOM_SECRET"}
     ]
   }
   ```

   Supply the secret at deployment time; do not save this metadata in source
   control. Generate a fresh random token with at least 32 bytes of entropy.
4. Enable its workers.dev endpoint with
   `POST /accounts/{account_id}/workers/scripts/{name}/subdomain`, body
   `{"enabled":true,"previews_enabled":false}`.

The Worker uses a D1 binding and starts each request on the primary. The ledger
row is both the effect and its receipt. A unique operation ID deduplicates writes;
reuse with different business arguments returns HTTP 409. Every endpoint needs
the bearer token. This is an isolated test service, not a production refund API.

## Run

From the repository root:

```bash
python -m pip install -e '.[live]'
mkdir -p .live-validation
```

Create `.env.jev-live` containing `TYPESAFE_API_KEY=...`. Create
`.live-validation/service.json` containing the service `endpoint` (HTTPS) and
`token`. Both paths are gitignored. Restrict both files to owner access
(`chmod 600` on POSIX). Never paste credentials into benchmark output.

```bash
python benchmarks/live_jev.py --model jev-latest --repeat 3 \
  --output benchmarks/results/live-jev.json
```

This makes billable model calls and cloud requests: 36 trials, at most six
decisions per trial, two write attempts and four status checks. Each trial has a
fresh order and persisted operation identity. Policy order alternates between
repetitions. The output is checkpointed after each trial. An authentication,
billing, or rate-limit error stops the run. Exit 1 means at least one verified
trial failed; exit 2 means validation was interrupted by infrastructure errors.
Direct-policy failures are recorded but do not alone make the exit code nonzero.

## Cases and scoring

| Case | Fault location | Expected decision |
| --- | --- | --- |
| Normal | None | Confirm success from evidence |
| Timeout before execution | Client boundary, before sending the first write | Retry with the original ID, verify, confirm |
| Response lost | Client boundary, after a successful real HTTP write | Verify the existing effect, confirm |
| Stale status after commit | Lost response, then one non-authoritative `not_found` | Wait for authoritative evidence, confirm |
| False success | Service returns success without committing a row | Escalate after bounded verification |
| Verification unavailable | Client boundary blocks all status reads | Escalate; the effect may have committed |

The model never receives the case name, injected-fault labels, or oracle state.
Both policies use the same model instructions, service, stable operation ID,
and server deduplication guarantee. The direct policy chooses raw writes and
status checks. The verified policy chooses the helper and then interprets its
structured outcome. Recovery in the helper is deterministic.

The evaluator bypasses injected faults and reads `/oracle`, an aggregate over
committed rows. A passing trial requires the expected terminal decision, correct
state, and fault activation. A success claim also requires an authoritative,
correlated receipt with the expected effect. A true guess without that evidence
is recorded separately as **unverified success**. A **false success** means the
claimed business effect is actually absent or wrong. These categories overlap.

The service intentionally supports strong idempotency. Zero duplicates in both
policies therefore do not establish that the helper can make an arbitrary API
idempotent. The injected lost reply is at the client boundary after a completed
HTTP exchange, not a physical network disconnect inside the provider.

## Evidence and cleanup

Before deleting the resources, independently query D1 through the control-plane
API and compare every trial's saved `order_id` with its recorded oracle:

```sql
SELECT o.id AS order_id, o.mode, COUNT(r.operation_id) AS refund_count,
       COALESCE(SUM(r.amount_cents), 0) AS refunded_cents
FROM orders o LEFT JOIN refunds r ON r.order_id = o.id
GROUP BY o.id, o.mode ORDER BY o.id;
```

Save the query result and comparison, then delete only the temporary Worker and
database using their recorded IDs. Confirm they no longer exist. Keep the raw
JSON report and a written summary of failures and limits. Synthetic order and
operation IDs are included in this benchmark's raw report; credentials are not.

Three repetitions of six authored cases are an integration check, not a general
reliability estimate. Model aliases may resolve to different versions later;
each decision records the returned model version and token usage.
