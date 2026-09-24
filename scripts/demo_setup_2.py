#!/usr/bin/env python3
"""demo_setup_2.py — Provisions Demo 2 fixtures and telemetry in BigQuery.

Tests all new additions from the Control Plane audit:
1. Dynamic Workload-Capped Honest Scoring (§6.2) on any table (demo_ecommerce.product_catalog).
2. Multi-tiered Ownership Attribution (§8, §9.1) via labels, top-writers, and codeowners.
3. Two-Person Approval Workflow (§8) on Class 3 structural rebuilds.
4. Streaming Buffer Cutover Handling (§9.4 S2b).
5. Storage-basis Verification Receipts (§10) tracking realized dollar savings.
6. New Rule Catalog:
   - C1-04: Time-Travel Window Tuning (demo_ecommerce.high_churn_events)
   - C1-06: Adaptive Query Optimization (project-level metadata)
   - W-01 / C1-07: Editions Billing-Mode Fit & Capacity Sizing
   - C1-08: Zombie Scheduled Query / DTS Cleanup (demo_ecommerce.zombie_export_feed)
   - C1-10: Unenforced PK/FK Constraints (demo_ecommerce.dim_customer)
   - C2-02: Search Index with Watchdog Limits (demo_ecommerce.support_tickets_archive)
   - C3-03: Partition Granularity Tuning Day -> Month (demo_ecommerce.iot_sensor_telemetry)
   - C4-04: Self-Join to Analytic Window Function PR Rewrite
   - C4-07: Exact COUNT(DISTINCT) to APPROX_COUNT_DISTINCT PR Rewrite

Usage:
    python scripts/demo_setup_2.py
"""
import os
import sys

# Auto-reexec with local virtualenv if invoked with system python
if sys.prefix == sys.base_prefix:
    _base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for _vpy in [
        os.path.join(_base_dir, ".venv", "bin", "python"),
        os.path.join(_base_dir, ".venv", "bin", "python3"),
        os.path.join(os.path.expanduser("~"), ".venv-bq", "bin", "python"),
        os.path.join(os.path.expanduser("~"), ".venv-bq", "bin", "python3"),
    ]:
        if os.path.exists(_vpy):
            os.execv(_vpy, [_vpy] + sys.argv)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from google.cloud import bigquery
from optimizer.config import cfg
from optimizer import cli

def setup_demo_2():
    c = cfg()
    client = bigquery.Client(project=c.project_id)
    print(f"[*] Starting Demo 2 Setup in project: {c.project_id} (Location: {c.location})...\n")

    # 1. Initialize optimizer_ops dataset & tables
    print("[1/5] Initializing optimizer_ops dataset, tables, and views...")
    cli.cmd_init(c)

    # 2. Create demo workload dataset
    demo_ds_id = f"{c.project_id}.demo_ecommerce"
    print(f"\n[2/5] Ensuring workload dataset exists: {demo_ds_id}...")
    ds = bigquery.Dataset(demo_ds_id)
    ds.location = c.location
    ds.description = "Workload dataset for bq-optimizer Demo 2 additions"
    client.create_dataset(ds, exists_ok=True)

    # 3. Create demo tables
    print("\n[3/5] Creating sample workload tables in demo_ecommerce for Demo 2 additions...")

    # 3a. demo_ecommerce.high_churn_events (C1-04 Time Travel Window + Label Ownership)
    hce_table_id = f"{demo_ds_id}.high_churn_events"
    client.query(f"""
    CREATE OR REPLACE TABLE `{hce_table_id}` (
        event_id STRING,
        event_type STRING,
        payload STRING,
        event_time TIMESTAMP
    )
    PARTITION BY DATE(event_time)
    OPTIONS (
        description = "High-churn CDC ingestion table accumulating huge physical time-travel storage",
        labels = [("owner", "finops-core-team"), ("env", "production")]
    );

    INSERT INTO `{hce_table_id}`
    SELECT
        GENERATE_UUID(),
        CASE MOD(i, 3) WHEN 0 THEN 'UPDATE' WHEN 1 THEN 'UPSERT' ELSE 'DELETE' END,
        REPEAT('telemetry-payload-data-', 5),
        TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL MOD(i, 20)*24 HOUR)
    FROM UNNEST(GENERATE_ARRAY(1, 2000)) AS i;
    """).result()
    print(f"  + Created {hce_table_id} (C1-04 Time Travel + Owner Label)")

    # 3b. demo_ecommerce.zombie_export_feed (C1-08 Zombie ETL write with 0 readers)
    zombie_table_id = f"{demo_ds_id}.zombie_export_feed"
    client.query(f"""
    CREATE OR REPLACE TABLE `{zombie_table_id}` (
        export_id STRING,
        partner_code STRING,
        export_payload STRING,
        exported_at TIMESTAMP
    )
    PARTITION BY DATE(exported_at)
    OPTIONS (description = "Nightly batch export table populated by scheduled ETL with 0 downstream readers");

    INSERT INTO `{zombie_table_id}`
    SELECT
        GENERATE_UUID(),
        CONCAT("PARTNER-", CAST(MOD(i, 10) AS STRING)),
        "batch-exported-record",
        TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL MOD(i, 25)*24 HOUR)
    FROM UNNEST(GENERATE_ARRAY(1, 1500)) AS i;
    """).result()
    print(f"  + Created {zombie_table_id} (C1-08 Zombie Scheduled Query)")

    # 3c. demo_ecommerce.dim_customer (C1-10 Unenforced PK/FK Join Elimination)
    dim_cust_id = f"{demo_ds_id}.dim_customer"
    client.query(f"""
    CREATE OR REPLACE TABLE `{dim_cust_id}` (
        id STRING,
        customer_name STRING,
        tier STRING,
        country STRING,
        created_at TIMESTAMP
    )
    OPTIONS (description = "Customer dimension table heavily joined across analytics without PK constraints");

    INSERT INTO `{dim_cust_id}`
    SELECT
        CONCAT("CUST-", CAST(i AS STRING)),
        CONCAT("Customer ", CAST(i AS STRING)),
        CASE MOD(i, 3) WHEN 0 THEN "ENTERPRISE" WHEN 1 THEN "PRO" ELSE "BASIC" END,
        "US",
        CURRENT_TIMESTAMP()
    FROM UNNEST(GENERATE_ARRAY(1, 1000)) AS i;
    """).result()
    print(f"  + Created {dim_cust_id} (C1-10 PK/FK Constraints)")

    # 3d. demo_ecommerce.support_tickets_archive (C2-02 Search Indexes)
    tickets_table_id = f"{demo_ds_id}.support_tickets_archive"
    client.query(f"""
    CREATE OR REPLACE TABLE `{tickets_table_id}` (
        ticket_id STRING,
        customer_id STRING,
        subject STRING,
        body STRING,
        status STRING,
        created_at TIMESTAMP
    )
    OPTIONS (description = "Archive of customer support tickets queried by needle-in-haystack lookups");

    INSERT INTO `{tickets_table_id}`
    SELECT
        CONCAT("TICK-", CAST(i AS STRING)),
        CONCAT("CUST-", CAST(MOD(i, 500) AS STRING)),
        CONCAT("Issue subject ", CAST(i AS STRING)),
        CONCAT("Customer reported critical timeout exception on gateway node ", CAST(MOD(i, 20) AS STRING)),
        "RESOLVED",
        TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL MOD(i, 60)*24 HOUR)
    FROM UNNEST(GENERATE_ARRAY(1, 3000)) AS i;
    """).result()
    print(f"  + Created {tickets_table_id} (C2-02 Search Indexes)")

    # 3e. demo_ecommerce.iot_sensor_telemetry (C3-03 Partition Granularity Tuning Day -> Month + 2-Person Approval)
    iot_table_id = f"{demo_ds_id}.iot_sensor_telemetry"
    client.query(f"""
    CREATE OR REPLACE TABLE `{iot_table_id}` (
        device_id STRING,
        temperature FLOAT64,
        pressure FLOAT64,
        reading_date DATE
    )
    PARTITION BY reading_date
    OPTIONS (description = "IoT sensor telemetry with thousands of tiny daily partitions (<10 MB each)");

    INSERT INTO `{iot_table_id}`
    SELECT
        CONCAT("DEV-", CAST(MOD(i, 100) AS STRING)),
        ROUND(RAND() * 40 + 10, 2),
        ROUND(RAND() * 100 + 900, 2),
        DATE_SUB(CURRENT_DATE(), INTERVAL MOD(i, 10) DAY)
    FROM UNNEST(GENERATE_ARRAY(1, 4000)) AS i;
    """).result()
    print(f"  + Created {iot_table_id} (C3-03 Partition Granularity + 2-Person Approval)")

    # 3f. demo_ecommerce.streaming_events_active (Streaming Buffer Cutover Target)
    streaming_table_id = f"{demo_ds_id}.streaming_events_active"
    client.query(f"""
    CREATE OR REPLACE TABLE `{streaming_table_id}` (
        stream_id STRING,
        user_id STRING,
        event_payload STRING,
        event_time TIMESTAMP
    )
    PARTITION BY DATE(event_time)
    OPTIONS (description = "High-throughput streaming ingestion table with active streaming buffer");

    INSERT INTO `{streaming_table_id}`
    SELECT
        GENERATE_UUID(),
        CONCAT("USER-", CAST(MOD(i, 500) AS STRING)),
        "live-stream-data",
        CURRENT_TIMESTAMP()
    FROM UNNEST(GENERATE_ARRAY(1, 1000)) AS i;
    """).result()
    print(f"  + Created {streaming_table_id} (Streaming Buffer Cutover Target)")

    # 3g. demo_ecommerce.product_catalog (Dynamic Workload Capping: $8k claim vs $800 spend)
    prod_table_id = f"{demo_ds_id}.product_catalog"
    client.query(f"""
    CREATE OR REPLACE TABLE `{prod_table_id}` (
        product_id STRING,
        title STRING,
        category STRING,
        price FLOAT64,
        in_stock BOOL,
        updated_at TIMESTAMP
    )
    OPTIONS (description = "Catalog table for testing dynamic summation capping on non-orders tables");

    INSERT INTO `{prod_table_id}`
    SELECT
        CONCAT("PROD-", CAST(i AS STRING)),
        CONCAT("Product Item ", CAST(i AS STRING)),
        CASE MOD(i, 5) WHEN 0 THEN "ELECTRONICS" WHEN 1 THEN "APPAREL" WHEN 2 THEN "HOME" WHEN 3 THEN "BOOKS" ELSE "BEAUTY" END,
        ROUND(RAND() * 150 + 5, 2),
        TRUE,
        CURRENT_TIMESTAMP()
    FROM UNNEST(GENERATE_ARRAY(1, 2000)) AS i;
    """).result()
    print(f"  + Created {prod_table_id} (Dynamic Honest Scoring Target)")

    # 4. Clear and populate simulated telemetry in optimizer_ops
    print("\n[4/5] Populating simulated telemetry in optimizer_ops...")

    client.query(f"""
    DELETE FROM `{c.ops}.jobs_events` WHERE project_id = '{c.project_id}';
    DELETE FROM `{c.ops}.jobs_hourly_slots` WHERE project_id = '{c.project_id}';
    DELETE FROM `{c.ops}.recommender_recommendations` WHERE project_id = '{c.project_id}';
    DELETE FROM `{c.ops}.change_sets` WHERE target_project = '{c.project_id}';
    DELETE FROM `{c.ops}.table_state_daily` WHERE project_id = '{c.project_id}';
    DELETE FROM `{c.ops}.table_partitions_daily` WHERE project_id = '{c.project_id}';
    DELETE FROM `{c.ops}.columns_daily` WHERE project_id = '{c.project_id}';
    """).result()

    # 4a. Seed table_state_daily
    client.query(f"""
    INSERT INTO `{c.ops}.table_state_daily` (
        snapshot_date, region, project_id, dataset_id, table_id, table_type, table_created,
        total_rows, total_partitions, total_logical_bytes, active_logical_bytes, long_term_logical_bytes,
        total_physical_bytes, active_physical_bytes, long_term_physical_bytes, time_travel_physical_bytes, fail_safe_physical_bytes,
        storage_last_modified, run_id
    ) VALUES
        -- High-churn table with 18 GiB physical time travel (C1-04)
        (CURRENT_DATE(), 'us', '{c.project_id}', 'demo_ecommerce', 'high_churn_events', 'BASE TABLE', TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 60 DAY),
         5000000, 20, CAST(8 * POW(1024, 3) AS INT64), CAST(8 * POW(1024, 3) AS INT64), 0,
         CAST(25 * POW(1024, 3) AS INT64), CAST(7 * POW(1024, 3) AS INT64), 0, CAST(18 * POW(1024, 3) AS INT64), CAST(2 * POW(1024, 3) AS INT64),
         CURRENT_TIMESTAMP(), 'demo-2-init'),

        -- Dim customer table for C1-10 PK/FK
        (CURRENT_DATE(), 'us', '{c.project_id}', 'demo_ecommerce', 'dim_customer', 'BASE TABLE', TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 90 DAY),
         100000, 0, CAST(200 * POW(1024, 2) AS INT64), CAST(200 * POW(1024, 2) AS INT64), 0,
         CAST(50 * POW(1024, 2) AS INT64), CAST(50 * POW(1024, 2) AS INT64), 0, 0, 0,
         CURRENT_TIMESTAMP(), 'demo-2-init'),

        -- Support tickets archive with 12 GiB logical storage (C2-02 Search Index)
        (CURRENT_DATE(), 'us', '{c.project_id}', 'demo_ecommerce', 'support_tickets_archive', 'BASE TABLE', TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 120 DAY),
         8000000, 0, CAST(12 * POW(1024, 3) AS INT64), CAST(12 * POW(1024, 3) AS INT64), 0,
         CAST(3 * POW(1024, 3) AS INT64), CAST(3 * POW(1024, 3) AS INT64), 0, 0, 0,
         CURRENT_TIMESTAMP(), 'demo-2-init'),

        -- Product catalog for dynamic summation capping ($800/mo spend baseline)
        (CURRENT_DATE(), 'us', '{c.project_id}', 'demo_ecommerce', 'product_catalog', 'BASE TABLE', TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 45 DAY),
         500000, 0, CAST(2 * POW(1024, 3) AS INT64), CAST(2 * POW(1024, 3) AS INT64), 0,
         CAST(500 * POW(1024, 2) AS INT64), CAST(500 * POW(1024, 2) AS INT64), 0, 0, 0,
         CURRENT_TIMESTAMP(), 'demo-2-init');
    """).result()

    # 4b. Seed table_partitions_daily for C3-03 (1,200 micro-partitions with avg 8 MB size)
    client.query(f"""
    INSERT INTO `{c.ops}.table_partitions_daily` (
        snapshot_date, region, project_id, dataset_id, table_id, partition_id, total_rows, total_logical_bytes, run_id
    )
    SELECT
        CURRENT_DATE(), 'us', '{c.project_id}', 'demo_ecommerce', 'iot_sensor_telemetry',
        FORMAT_DATE('%Y%m%d', DATE_SUB(CURRENT_DATE(), INTERVAL i DAY)),
        1000,
        CAST(8 * 1024 * 1024 AS INT64),
        'demo-2-init'
    FROM UNNEST(GENERATE_ARRAY(1, 1200)) AS i;
    """).result()

    # 4c. Seed Active Assist recommendation for product_catalog ($8,000 claim vs $800 spend)
    rec2_seed_sql = f"""
    INSERT INTO `{c.ops}.recommender_recommendations` (
        recommender, recommendation_id, project_id, region, state, subtype, description,
        last_updated_time, target_resources_json, additional_details_json, raw_json,
        first_seen_at, last_seen_at, run_id
    ) VALUES (
        'google.bigquery.table.PartitionClusterRecommender',
        'rec-prod-catalog-overshoot',
        '{c.project_id}',
        'us',
        'ACTIVE',
        'CLUSTER_TABLE',
        'Native recommender promising $8,000/mo without spend audit for product_catalog',
        CURRENT_TIMESTAMP(),
        '["//bigquery.googleapis.com/projects/{c.project_id}/datasets/demo_ecommerce/tables/product_catalog"]',
        '\\{{"clustering_columns": ["category", "price"], "est_gb_saved_monthly": 1310720\\}}',
        '\\{{\\}}',
        CURRENT_TIMESTAMP(),
        CURRENT_TIMESTAMP(),
        'demo-2-init'
    );
    """.replace('\\{', '{').replace('\\}', '}')
    client.query(rec2_seed_sql).result()

    # 4d. Seed jobs_events
    client.query(f"""
    INSERT INTO `{c.ops}.jobs_events` (
        region, project_id, job_id, user_email, job_type, statement_type, priority, state,
        cache_hit, creation_time, start_time, end_time, duration_ms,
        total_bytes_processed, total_bytes_billed, total_slot_ms,
        referenced_tables, destination_table, query_hash, query_preview, collected_at
    )
    -- 1. Product catalog queries ($800/mo spend baseline for dynamic summation cap)
    SELECT
        'us', '{c.project_id}', CONCAT('job-prod-cat-', CAST(i AS STRING)),
        'catalog-service@company.internal', 'QUERY', 'SELECT', 'INTERACTIVE', 'DONE', FALSE,
        TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL MOD(i, 30)*24 HOUR),
        TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL MOD(i, 30)*24 HOUR),
        TIMESTAMP_ADD(TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL MOD(i, 30)*24 HOUR), INTERVAL 3 SECOND),
        3000,
        CAST(1 * POW(1024, 4) AS INT64),
        CAST(1 * POW(1024, 4) AS INT64),
        60000,
        [STRUCT('{c.project_id}' AS project_id, 'demo_ecommerce' AS dataset_id, 'product_catalog' AS table_id)] AS referenced_tables,
        STRUCT('{c.project_id}' AS project_id, 'demo_ecommerce' AS dataset_id, 'anon_dest' AS table_id) AS destination_table,
        'hash_prod_catalog_query',
        'SELECT * FROM demo_ecommerce.product_catalog WHERE category = "ELECTRONICS"',
        CURRENT_TIMESTAMP()
    FROM UNNEST(GENERATE_ARRAY(1, 128)) AS i

    UNION ALL

    -- 2. C1-08 Zombie ETL writes: 15 write jobs to zombie_export_feed, 0 reader queries
    SELECT
        'us', '{c.project_id}', CONCAT('job-zombie-write-', CAST(i AS STRING)),
        'etl-pipeline-runner@company.iam.gserviceaccount.com', 'QUERY', 'INSERT', 'BATCH', 'DONE', FALSE,
        TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL i * 40 HOUR),
        TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL i * 40 HOUR),
        TIMESTAMP_ADD(TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL i * 40 HOUR), INTERVAL 10 SECOND),
        10000,
        CAST(800 * POW(1024, 3) AS INT64),
        CAST(800 * POW(1024, 3) AS INT64),
        120000,
        [STRUCT('{c.project_id}' AS project_id, 'demo_ecommerce' AS dataset_id, 'source_staging' AS table_id)] AS referenced_tables,
        STRUCT('{c.project_id}' AS project_id, 'demo_ecommerce' AS dataset_id, 'zombie_export_feed' AS table_id) AS destination_table,
        'hash_zombie_nightly_etl',
        'INSERT INTO demo_ecommerce.zombie_export_feed SELECT * FROM source_staging',
        CURRENT_TIMESTAMP()
    FROM UNNEST(GENERATE_ARRAY(1, 15)) AS i

    UNION ALL

    -- 3. C1-10 Join queries referencing dim_customer
    SELECT
        'us', '{c.project_id}', CONCAT('job-dim-cust-join-', CAST(i AS STRING)),
        'sales-analytics@company.com', 'QUERY', 'SELECT', 'INTERACTIVE', 'DONE', FALSE,
        TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL MOD(i, 28)*24 HOUR),
        TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL MOD(i, 28)*24 HOUR),
        TIMESTAMP_ADD(TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL MOD(i, 28)*24 HOUR), INTERVAL 4 SECOND),
        4000,
        CAST(500 * POW(1024, 3) AS INT64),
        CAST(500 * POW(1024, 3) AS INT64),
        400000,
        [STRUCT('{c.project_id}' AS project_id, 'demo_ecommerce' AS dataset_id, 'dim_customer' AS table_id)] AS referenced_tables,
        STRUCT('{c.project_id}' AS project_id, 'demo_ecommerce' AS dataset_id, 'anon_dest' AS table_id) AS destination_table,
        'hash_dim_cust_join',
        'SELECT o.order_id, c.customer_name FROM demo_ecommerce.orders o JOIN demo_ecommerce.dim_customer c ON o.customer_id = c.id',
        CURRENT_TIMESTAMP()
    FROM UNNEST(GENERATE_ARRAY(1, 30)) AS i

    UNION ALL

    -- 4. C2-02 Needle-in-haystack point lookups on support_tickets_archive
    SELECT
        'us', '{c.project_id}', CONCAT('job-ticket-lookup-', CAST(i AS STRING)),
        'support-agent@company.com', 'QUERY', 'SELECT', 'INTERACTIVE', 'DONE', FALSE,
        TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL MOD(i, 25)*24 HOUR),
        TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL MOD(i, 25)*24 HOUR),
        TIMESTAMP_ADD(TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL MOD(i, 25)*24 HOUR), INTERVAL 6 SECOND),
        6000,
        CAST(12 * POW(1024, 3) AS INT64),
        CAST(12 * POW(1024, 3) AS INT64),
        150000,
        [STRUCT('{c.project_id}' AS project_id, 'demo_ecommerce' AS dataset_id, 'support_tickets_archive' AS table_id)] AS referenced_tables,
        STRUCT('{c.project_id}' AS project_id, 'demo_ecommerce' AS dataset_id, 'anon_dest' AS table_id) AS destination_table,
        'hash_ticket_lookup',
        'SELECT ticket_id, subject FROM demo_ecommerce.support_tickets_archive WHERE ticket_id = "TICK-1042"',
        CURRENT_TIMESTAMP()
    FROM UNNEST(GENERATE_ARRAY(1, 20)) AS i

    UNION ALL

    -- 5. C1-06 Adaptive Optimization & W-01 Editions Sizing (120 TiB scanned, 15 complex queries > 6M slot ms)
    SELECT
        'us', '{c.project_id}', CONCAT('job-heavy-complex-', CAST(i AS STRING)),
        'data-science@company.com', 'QUERY', 'SELECT', 'BATCH', 'DONE', FALSE,
        TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL MOD(i, 28)*24 HOUR),
        TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL MOD(i, 28)*24 HOUR),
        TIMESTAMP_ADD(TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL MOD(i, 28)*24 HOUR), INTERVAL 30 SECOND),
        30000,
        CAST(8 * POW(1024, 4) AS INT64),
        CAST(8 * POW(1024, 4) AS INT64),
        7000000,
        [STRUCT('{c.project_id}' AS project_id, 'demo_ecommerce' AS dataset_id, 'high_churn_events' AS table_id)] AS referenced_tables,
        STRUCT('{c.project_id}' AS project_id, 'demo_ecommerce' AS dataset_id, 'anon_dest' AS table_id) AS destination_table,
        'hash_heavy_ml_features',
        'SELECT event_type, COUNT(*) FROM demo_ecommerce.high_churn_events GROUP BY 1',
        CURRENT_TIMESTAMP()
    FROM UNNEST(GENERATE_ARRAY(1, 15)) AS i

    UNION ALL

    -- 6. C4-04 Self-Join Anti-Pattern
    SELECT
        'us', '{c.project_id}', CONCAT('job-c4-self-join-', CAST(i AS STRING)),
        'analyst@company.com', 'QUERY', 'SELECT', 'INTERACTIVE', 'DONE', FALSE,
        TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL MOD(i, 20)*24 HOUR),
        TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL MOD(i, 20)*24 HOUR),
        TIMESTAMP_ADD(TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL MOD(i, 20)*24 HOUR), INTERVAL 5 SECOND),
        5000,
        CAST(2 * POW(1024, 3) AS INT64),
        CAST(2 * POW(1024, 3) AS INT64),
        80000,
        [STRUCT('{c.project_id}' AS project_id, 'demo_ecommerce' AS dataset_id, 'high_churn_events' AS table_id)] AS referenced_tables,
        STRUCT('{c.project_id}' AS project_id, 'demo_ecommerce' AS dataset_id, 'anon_dest' AS table_id) AS destination_table,
        'hash_c4_self_join',
        'SELECT a.event_id, b.event_id FROM demo_ecommerce.high_churn_events a JOIN demo_ecommerce.high_churn_events b ON a.event_type = b.event_type AND a.event_time < b.event_time',
        CURRENT_TIMESTAMP()
    FROM UNNEST(GENERATE_ARRAY(1, 20)) AS i

    UNION ALL

    -- 7. C4-07 Exact COUNT(DISTINCT) Anti-Pattern
    SELECT
        'us', '{c.project_id}', CONCAT('job-c4-count-distinct-', CAST(i AS STRING)),
        'growth@company.com', 'QUERY', 'SELECT', 'INTERACTIVE', 'DONE', FALSE,
        TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL MOD(i, 20)*24 HOUR),
        TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL MOD(i, 20)*24 HOUR),
        TIMESTAMP_ADD(TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL MOD(i, 20)*24 HOUR), INTERVAL 5 SECOND),
        5000,
        CAST(3 * POW(1024, 3) AS INT64),
        CAST(3 * POW(1024, 3) AS INT64),
        90000,
        [STRUCT('{c.project_id}' AS project_id, 'demo_ecommerce' AS dataset_id, 'high_churn_events' AS table_id)] AS referenced_tables,
        STRUCT('{c.project_id}' AS project_id, 'demo_ecommerce' AS dataset_id, 'anon_dest' AS table_id) AS destination_table,
        'hash_c4_count_distinct',
        'SELECT event_type, COUNT(DISTINCT event_id) AS unique_events FROM demo_ecommerce.high_churn_events GROUP BY 1',
        CURRENT_TIMESTAMP()
    FROM UNNEST(GENERATE_ARRAY(1, 25)) AS i;
    """).result()

    print("  + Seeded state telemetry and query logs for all new additions.")

    # Seed jobs_hourly_slots (hourly slot concurrency & Edition sizing baseline for 1B)
    slots_seed_sql = f"""
    INSERT INTO `{c.ops}.jobs_hourly_slots` (
        region, hour_ts, project_id, reservation_id, total_slot_ms, jobs, collected_at, run_id
    )
    SELECT
        'us' AS region,
        TIMESTAMP_SUB(TIMESTAMP_TRUNC(CURRENT_TIMESTAMP(), HOUR), INTERVAL i HOUR) AS hour_ts,
        '{c.project_id}' AS project_id,
        '(on-demand)' AS reservation_id,
        CAST(
          (
            265.4 +
            CASE
              WHEN EXTRACT(HOUR FROM TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL i HOUR)) BETWEEN 13 AND 21
                THEN 180.0 + MOD(ABS(FARM_FINGERPRINT(CONCAT('peak-', CAST(i AS STRING)))), 1850) / 10.0
              ELSE MOD(ABS(FARM_FINGERPRINT(CONCAT('offpeak-', CAST(i AS STRING)))), 950) / 10.0
            END
          ) * 3600 * 1000 AS INT64
        ) AS total_slot_ms,
        CAST(
          42 +
          CASE
            WHEN EXTRACT(HOUR FROM TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL i HOUR)) BETWEEN 13 AND 21
              THEN 35 + MOD(ABS(FARM_FINGERPRINT(CONCAT('q-peak-', CAST(i AS STRING)))), 45)
            ELSE MOD(ABS(FARM_FINGERPRINT(CONCAT('q-off-', CAST(i AS STRING)))), 25)
          END AS INT64
        ) AS jobs,
        CURRENT_TIMESTAMP() AS collected_at,
        'demo-seed' AS run_id
    FROM UNNEST(GENERATE_ARRAY(0, 168)) AS i;
    """
    client.query(slots_seed_sql).result()
    print("  + Seeded hourly slot concurrency curve into jobs_hourly_slots (feeds Notebook Cell 1B)")

    # 5. Run rules engine to compile change sets
    print("\n[5/5] Running Rules Engine to evaluate telemetry and compile change sets...")
    cli.cmd_rules(c)

    print("\n" + "="*80)
    print("✅ DEMO 2 SETUP COMPLETE!")
    print("="*80)
    print("All new additions are provisioned and ready for interactive customer presentation:")
    print("  1. Dynamic Honest Scoring: demo_ecommerce.product_catalog ($8k claim capped to $544/mo)")
    print("  2. Ownership Attribution: Label-based (finops-core-team) & Top-Writer (etl-runner)")
    print("  3. Two-Person Approvals: demo_ecommerce.iot_sensor_telemetry (C3-03)")
    print("  4. Storage Receipts: demo_ecommerce.high_churn_events (C1-04)")
    print("  5. Rules Catalog: C1-04, C1-06, W-01, C1-08, C1-10, C2-02, C3-03, C4-04, C4-07")
    print("="*80 + "\n")

if __name__ == "__main__":
    setup_demo_2()
