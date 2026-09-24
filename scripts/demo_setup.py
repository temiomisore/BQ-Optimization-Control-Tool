#!/usr/bin/env python3
"""demo_setup.py — Provisions realistic demo datasets and telemetry in BigQuery.

Usage:
    python scripts/demo_setup.py
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

def setup_demo():
    c = cfg()
    client = bigquery.Client(project=c.project_id)
    print(f"[*] Starting Demo Setup in project: {c.project_id} (Location: {c.location})...")

    # Step 1: Initialize optimizer_ops dataset & tables
    print("\n[1/5] Initializing optimizer_ops dataset, tables, and views...")
    cli.cmd_init(c)

    # Step 2: Create demo workload dataset
    demo_ds_id = f"{c.project_id}.demo_ecommerce"
    print(f"\n[2/5] Creating workload dataset: {demo_ds_id}...")
    ds = bigquery.Dataset(demo_ds_id)
    ds.location = c.location
    ds.description = "Workload dataset for bq-optimizer customer demo"
    client.create_dataset(ds, exists_ok=True)

    # Step 3: Create demo tables
    print("\n[3/5] Creating sample workload tables in demo_ecommerce...")
    
    # 3a. demo_ecommerce.orders (Partitioned by day, UNCLUSTERED, high scan volume)
    orders_table_id = f"{demo_ds_id}.orders"
    orders_sql = f"""
    CREATE OR REPLACE TABLE `{orders_table_id}` (
        order_id STRING,
        customer_id STRING,
        order_status STRING,
        order_amount FLOAT64,
        region STRING,
        order_date DATE,
        created_at TIMESTAMP
    )
    PARTITION BY order_date
    OPTIONS (description = "E-commerce orders table (candidate for clustering on customer_id, order_status)");
    
    INSERT INTO `{orders_table_id}`
    SELECT
        GENERATE_UUID() AS order_id,
        CONCAT("CUST-", CAST(MOD(ABS(FARM_FINGERPRINT(CAST(i AS STRING))), 500) AS STRING)) AS customer_id,
        CASE MOD(i, 4)
            WHEN 0 THEN "COMPLETED"
            WHEN 1 THEN "PROCESSING"
            WHEN 2 THEN "SHIPPED"
            ELSE "CANCELLED"
        END AS order_status,
        ROUND(RAND() * 500 + 10, 2) AS order_amount,
        CASE MOD(i, 3) WHEN 0 THEN "US-EAST" WHEN 1 THEN "US-WEST" ELSE "US-CENTRAL" END AS region,
        DATE_SUB(CURRENT_DATE(), INTERVAL MOD(i, 30) DAY) AS order_date,
        TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL MOD(i, 30)*24 HOUR) AS created_at
    FROM UNNEST(GENERATE_ARRAY(1, 10000)) AS i;
    """
    client.query(orders_sql).result()
    print(f"  + Created {orders_table_id} (10,000 sample rows, partitioned by day, unclustered)")

    # 3b. demo_ecommerce.clickstream_events (Partitioned, require_partition_filter = FALSE)
    events_table_id = f"{demo_ds_id}.clickstream_events"
    events_sql = f"""
    CREATE OR REPLACE TABLE `{events_table_id}` (
        event_id STRING,
        user_id STRING,
        event_type STRING,
        page_url STRING,
        event_date DATE,
        event_timestamp TIMESTAMP
    )
    PARTITION BY event_date
    OPTIONS (
        description = "Clickstream event logs (candidate for require_partition_filter = TRUE)",
        require_partition_filter = FALSE
    );

    INSERT INTO `{events_table_id}`
    SELECT
        GENERATE_UUID() AS event_id,
        CONCAT("USER-", CAST(MOD(i, 1000) AS STRING)) AS user_id,
        CASE MOD(i, 3) WHEN 0 THEN "PAGE_VIEW" WHEN 1 THEN "ADD_TO_CART" ELSE "CHECKOUT" END AS event_type,
        CONCAT("https://store.example.com/item/", CAST(MOD(i, 200) AS STRING)) AS page_url,
        DATE_SUB(CURRENT_DATE(), INTERVAL MOD(i, 15) DAY) AS event_date,
        TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL MOD(i, 15)*24 HOUR) AS event_timestamp
    FROM UNNEST(GENERATE_ARRAY(1, 5000)) AS i;
    """
    client.query(events_sql).result()
    print(f"  + Created {events_table_id} (5,000 sample rows, partition filter not enforced)")

    # 3b2. demo_ecommerce.customer_events_clean (Partitioned, require_partition_filter = FALSE, 100% compliant queries)
    clean_events_table_id = f"{demo_ds_id}.customer_events_clean"
    clean_events_sql = f"""
    CREATE OR REPLACE TABLE `{clean_events_table_id}` (
        event_id STRING,
        user_id STRING,
        event_type STRING,
        page_url STRING,
        event_date DATE,
        event_timestamp TIMESTAMP
    )
    PARTITION BY event_date
    OPTIONS (
        description = "Clean customer events logs (candidate for clean require_partition_filter = TRUE)",
        require_partition_filter = FALSE
    );

    INSERT INTO `{clean_events_table_id}`
    SELECT
        GENERATE_UUID() AS event_id,
        CONCAT("USER-", CAST(MOD(i, 1000) AS STRING)) AS user_id,
        CASE MOD(i, 3) WHEN 0 THEN "PAGE_VIEW" WHEN 1 THEN "ADD_TO_CART" ELSE "CHECKOUT" END AS event_type,
        CONCAT("https://store.example.com/item/", CAST(MOD(i, 200) AS STRING)) AS page_url,
        DATE_SUB(CURRENT_DATE(), INTERVAL MOD(i, 15) DAY) AS event_date,
        TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL MOD(i, 15)*24 HOUR) AS event_timestamp
    FROM UNNEST(GENERATE_ARRAY(1, 5000)) AS i;
    """
    client.query(clean_events_sql).result()
    print(f"  + Created {clean_events_table_id} (5,000 sample rows, clean partition filter candidate)")

    # 3c. demo_ecommerce.audit_logs_unpartitioned (Large unpartitioned table -> C3-01 Repartitioning)
    logs_table_id = f"{demo_ds_id}.audit_logs_unpartitioned"
    logs_sql = f"""
    CREATE OR REPLACE TABLE `{logs_table_id}` (
        log_id STRING,
        actor_email STRING,
        action STRING,
        ip_address STRING,
        log_timestamp TIMESTAMP
    )
    OPTIONS (description = "Unpartitioned legacy audit logs (candidate for Class 3 Repartitioning)");

    INSERT INTO `{logs_table_id}`
    SELECT
        GENERATE_UUID() AS log_id,
        CONCAT("user", CAST(MOD(i, 50) AS STRING), "@example.com") AS actor_email,
        "ACCESS_API" AS action,
        "192.168.1.1" AS ip_address,
        TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL MOD(i, 90)*24 HOUR) AS log_timestamp
    FROM UNNEST(GENERATE_ARRAY(1, 2000)) AS i;
    """
    client.query(logs_sql).result()
    print(f"  + Created {logs_table_id} (Unpartitioned table -> C3-01)")

    # 3d. demo_ecommerce.temp_staging_inactive (Unused table -> C3-05 Safe Archiving)
    scratch_table_id = f"{demo_ds_id}.temp_staging_inactive"
    scratch_sql = f"""
    CREATE OR REPLACE TABLE `{scratch_table_id}` (
        scratch_id STRING,
        payload STRING,
        created_at TIMESTAMP
    )
    OPTIONS (description = "Abandoned scratch table (candidate for unused table cleanup and GCS export)");

    INSERT INTO `{scratch_table_id}`
    SELECT GENERATE_UUID(), "temporary data payload", CURRENT_TIMESTAMP()
    FROM UNNEST(GENERATE_ARRAY(1, 100));
    """
    client.query(scratch_sql).result()
    print(f"  + Created {scratch_table_id} (Unused table -> C3-05)")

    # 3e. demo_scratch dataset (Scratch dataset -> C1-03 Default Table Expiration)
    scratch_ds_id = f"{c.project_id}.demo_scratch"
    ds_scratch = bigquery.Dataset(scratch_ds_id)
    ds_scratch.location = "US"
    ds_scratch.description = "Temporary scratch dataset with no default table expiration"
    client.create_dataset(ds_scratch, exists_ok=True)
    
    scratch_subtable_id = f"{scratch_ds_id}.temp_etl_dump"
    client.query(f"""
    CREATE OR REPLACE TABLE `{scratch_subtable_id}` (id STRING, val STRING, dump_time TIMESTAMP);
    INSERT INTO `{scratch_subtable_id}` VALUES ('1', 'temp_val', CURRENT_TIMESTAMP());
    """).result()
    print(f"  + Created {scratch_ds_id} (Temporary dataset -> C1-03)")

    # 3f. Date-sharded tables: demo_ecommerce.ga_sessions_YYYYMMDD (30 shards -> C3-02 Consolidation)
    print("  + Creating 30 date-sharded tables (ga_sessions_YYYYMMDD -> C3-02)...")
    for day in range(1, 31):
        dt_str = f"202607{day:02d}"
        tbl_name = f"{demo_ds_id}.ga_sessions_{dt_str}"
        client.query(f"""
        CREATE OR REPLACE TABLE `{tbl_name}` (visit_id STRING, user_id STRING, hits INT64, visit_date STRING);
        INSERT INTO `{tbl_name}` VALUES (GENERATE_UUID(), 'USR-1', 15, '{dt_str}');
        """).result()
    print(f"  + Created 30 date-sharded tables: {demo_ds_id}.ga_sessions_20260701..30 -> C3-02")

    # Step 4: Populate simulated historical telemetry in optimizer_ops
    # Clear prior demo telemetry from ALL ops tables
    client.query(f"""
    DELETE FROM `{c.ops}.jobs_events` WHERE project_id = '{c.project_id}';
    DELETE FROM `{c.ops}.jobs_hourly_slots` WHERE project_id = '{c.project_id}';
    DELETE FROM `{c.ops}.recommender_recommendations` WHERE project_id = '{c.project_id}';
    DELETE FROM `{c.ops}.recommender_insights` WHERE project_id = '{c.project_id}';
    DELETE FROM `{c.ops}.change_sets` WHERE target_project = '{c.project_id}';
    DELETE FROM `{c.ops}.table_state_daily` WHERE project_id = '{c.project_id}';
    DELETE FROM `{c.ops}.columns_daily` WHERE project_id = '{c.project_id}';
    DELETE FROM `{c.ops}.table_partitions_daily` WHERE project_id = '{c.project_id}';
    DELETE FROM `{c.ops}.dataset_state_daily` WHERE project_id = '{c.project_id}';
    DELETE FROM `{c.ops}.object_state_raw_daily` WHERE project_id = '{c.project_id}';
    """).result()

    # Seed table_state_daily and dataset_state_daily for C1-05 (Storage billing gap), C1-03 (Scratch expiry), and C3-02 (Date shards)
    state_seed_sql = f"""
    INSERT INTO `{c.ops}.dataset_state_daily` (
        snapshot_date, region, project_id, dataset_id, location, created, last_modified, storage_billing_model, run_id
    ) VALUES 
        (CURRENT_DATE(), 'us', '{c.project_id}', 'demo_ecommerce', 'US', CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP(), 'LOGICAL', 'demo-init'),
        (CURRENT_DATE(), 'us', '{c.project_id}', 'demo_scratch', 'US', CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP(), 'LOGICAL', 'demo-init');

    INSERT INTO `{c.ops}.table_state_daily` (
        snapshot_date, region, project_id, dataset_id, table_id, table_type, table_created,
        total_rows, total_partitions, total_logical_bytes, active_logical_bytes, long_term_logical_bytes,
        total_physical_bytes, active_physical_bytes, long_term_physical_bytes, time_travel_physical_bytes, fail_safe_physical_bytes,
        storage_last_modified, run_id
    )
    -- High-compression dataset table state for C1-05
    SELECT
        CURRENT_DATE(), 'us', '{c.project_id}', 'demo_ecommerce', 'orders', 'BASE TABLE', TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 60 DAY),
        1000000, 30, CAST(20 * POW(1024, 4) AS INT64), CAST(15 * POW(1024, 4) AS INT64), CAST(5 * POW(1024, 4) AS INT64),
        CAST(2.5 * POW(1024, 4) AS INT64), CAST(2 * POW(1024, 4) AS INT64), CAST(0.5 * POW(1024, 4) AS INT64), CAST(200 * POW(1024, 3) AS INT64), CAST(100 * POW(1024, 3) AS INT64),
        CURRENT_TIMESTAMP(), 'demo-init'
    UNION ALL
    -- Clickstream events table state for C1-02 (Blocked guardrail candidate)
    SELECT
        CURRENT_DATE(), 'us', '{c.project_id}', 'demo_ecommerce', 'clickstream_events', 'BASE TABLE', TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 60 DAY),
        500000, 30, CAST(5 * POW(1024, 4) AS INT64), CAST(4 * POW(1024, 4) AS INT64), CAST(1 * POW(1024, 4) AS INT64),
        CAST(1 * POW(1024, 4) AS INT64), CAST(800 * POW(1024, 3) AS INT64), CAST(200 * POW(1024, 3) AS INT64), CAST(50 * POW(1024, 3) AS INT64), CAST(20 * POW(1024, 3) AS INT64),
        CURRENT_TIMESTAMP(), 'demo-init'
    UNION ALL
    -- Customer events clean table state for C1-02 (Clean apply candidate)
    SELECT
        CURRENT_DATE(), 'us', '{c.project_id}', 'demo_ecommerce', 'customer_events_clean', 'BASE TABLE', TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 60 DAY),
        500000, 30, CAST(5 * POW(1024, 4) AS INT64), CAST(4 * POW(1024, 4) AS INT64), CAST(1 * POW(1024, 4) AS INT64),
        CAST(1 * POW(1024, 4) AS INT64), CAST(800 * POW(1024, 3) AS INT64), CAST(200 * POW(1024, 3) AS INT64), CAST(50 * POW(1024, 3) AS INT64), CAST(20 * POW(1024, 3) AS INT64),
        CURRENT_TIMESTAMP(), 'demo-init'
    UNION ALL
    -- Unpartitioned legacy audit logs for C3-01
    SELECT
        CURRENT_DATE(), 'us', '{c.project_id}', 'demo_ecommerce', 'audit_logs_unpartitioned', 'BASE TABLE', TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 90 DAY),
        4000000, 0, CAST(400 * POW(1024, 3) AS INT64), CAST(400 * POW(1024, 3) AS INT64), 0,
        CAST(50 * POW(1024, 3) AS INT64), CAST(50 * POW(1024, 3) AS INT64), 0, CAST(5 * POW(1024, 3) AS INT64), CAST(2 * POW(1024, 3) AS INT64),
        CURRENT_TIMESTAMP(), 'demo-init'
    UNION ALL
    -- Inactive temp staging table for C3-05
    SELECT
        CURRENT_DATE(), 'us', '{c.project_id}', 'demo_ecommerce', 'temp_staging_inactive', 'BASE TABLE', TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 120 DAY),
        100000, 0, CAST(500 * POW(1024, 3) AS INT64), CAST(500 * POW(1024, 3) AS INT64), 0,
        CAST(60 * POW(1024, 3) AS INT64), CAST(60 * POW(1024, 3) AS INT64), 0, 0, 0,
        CURRENT_TIMESTAMP(), 'demo-init'
    UNION ALL
    -- Scratch dataset unexpired table for C1-03
    SELECT
        CURRENT_DATE(), 'us', '{c.project_id}', 'demo_scratch', 'temp_etl_dump', 'BASE TABLE', TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 45 DAY),
        200000, 0, CAST(2 * POW(1024, 4) AS INT64), CAST(2 * POW(1024, 4) AS INT64), 0,
        CAST(300 * POW(1024, 3) AS INT64), CAST(300 * POW(1024, 3) AS INT64), 0, 0, 0,
        CURRENT_TIMESTAMP(), 'demo-init'
    UNION ALL
    -- 30 Date-sharded table state entries for C3-02
    SELECT
        CURRENT_DATE(), 'us', '{c.project_id}', 'demo_ecommerce', CONCAT('ga_sessions_202607', LPAD(CAST(i AS STRING), 2, '0')), 'BASE TABLE',
        TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY),
        5000, 1, CAST(5 * POW(1024, 3) AS INT64), CAST(5 * POW(1024, 3) AS INT64), 0,
        CAST(1 * POW(1024, 3) AS INT64), CAST(1 * POW(1024, 3) AS INT64), 0, 0, 0,
        CURRENT_TIMESTAMP(), 'demo-init'
    FROM UNNEST(GENERATE_ARRAY(1, 30)) AS i;

    INSERT INTO `{c.ops}.columns_daily` (
        snapshot_date, region, project_id, dataset_id, table_id, column_name, data_type, is_partitioning_column, clustering_ordinal_position, run_id
    ) VALUES
        (CURRENT_DATE(), 'us', '{c.project_id}', 'demo_ecommerce', 'orders', 'order_id', 'STRING', 'NO', NULL, 'demo-init'),
        (CURRENT_DATE(), 'us', '{c.project_id}', 'demo_ecommerce', 'orders', 'customer_id', 'STRING', 'NO', NULL, 'demo-init'),
        (CURRENT_DATE(), 'us', '{c.project_id}', 'demo_ecommerce', 'orders', 'order_status', 'STRING', 'NO', NULL, 'demo-init'),
        (CURRENT_DATE(), 'us', '{c.project_id}', 'demo_ecommerce', 'orders', 'order_amount', 'FLOAT64', 'NO', NULL, 'demo-init'),
        (CURRENT_DATE(), 'us', '{c.project_id}', 'demo_ecommerce', 'orders', 'order_date', 'DATE', 'YES', NULL, 'demo-init'),
        (CURRENT_DATE(), 'us', '{c.project_id}', 'demo_ecommerce', 'orders', 'created_at', 'TIMESTAMP', 'NO', NULL, 'demo-init'),
        (CURRENT_DATE(), 'us', '{c.project_id}', 'demo_ecommerce', 'clickstream_events', 'event_id', 'STRING', 'NO', NULL, 'demo-init'),
        (CURRENT_DATE(), 'us', '{c.project_id}', 'demo_ecommerce', 'clickstream_events', 'user_id', 'STRING', 'NO', NULL, 'demo-init'),
        (CURRENT_DATE(), 'us', '{c.project_id}', 'demo_ecommerce', 'clickstream_events', 'event_date', 'DATE', 'YES', NULL, 'demo-init'),
        (CURRENT_DATE(), 'us', '{c.project_id}', 'demo_ecommerce', 'customer_events_clean', 'event_id', 'STRING', 'NO', NULL, 'demo-init'),
        (CURRENT_DATE(), 'us', '{c.project_id}', 'demo_ecommerce', 'customer_events_clean', 'user_id', 'STRING', 'NO', NULL, 'demo-init'),
        (CURRENT_DATE(), 'us', '{c.project_id}', 'demo_ecommerce', 'customer_events_clean', 'event_type', 'STRING', 'NO', NULL, 'demo-init'),
        (CURRENT_DATE(), 'us', '{c.project_id}', 'demo_ecommerce', 'customer_events_clean', 'page_url', 'STRING', 'NO', NULL, 'demo-init'),
        (CURRENT_DATE(), 'us', '{c.project_id}', 'demo_ecommerce', 'customer_events_clean', 'event_date', 'DATE', 'YES', NULL, 'demo-init'),
        (CURRENT_DATE(), 'us', '{c.project_id}', 'demo_ecommerce', 'customer_events_clean', 'event_timestamp', 'TIMESTAMP', 'NO', NULL, 'demo-init'),
        (CURRENT_DATE(), 'us', '{c.project_id}', 'demo_ecommerce', 'audit_logs_unpartitioned', 'log_id', 'STRING', 'NO', NULL, 'demo-init'),
        (CURRENT_DATE(), 'us', '{c.project_id}', 'demo_ecommerce', 'audit_logs_unpartitioned', 'actor_email', 'STRING', 'NO', NULL, 'demo-init'),
        (CURRENT_DATE(), 'us', '{c.project_id}', 'demo_ecommerce', 'audit_logs_unpartitioned', 'ip_address', 'STRING', 'NO', NULL, 'demo-init'),
        (CURRENT_DATE(), 'us', '{c.project_id}', 'demo_ecommerce', 'audit_logs_unpartitioned', 'action', 'STRING', 'NO', NULL, 'demo-init'),
        (CURRENT_DATE(), 'us', '{c.project_id}', 'demo_ecommerce', 'audit_logs_unpartitioned', 'log_timestamp', 'TIMESTAMP', 'NO', NULL, 'demo-init'),
        (CURRENT_DATE(), 'us', '{c.project_id}', 'demo_ecommerce', 'temp_staging_inactive', 'batch_id', 'STRING', 'NO', NULL, 'demo-init'),
        (CURRENT_DATE(), 'us', '{c.project_id}', 'demo_ecommerce', 'temp_staging_inactive', 'raw_payload', 'STRING', 'NO', NULL, 'demo-init'),
        (CURRENT_DATE(), 'us', '{c.project_id}', 'demo_ecommerce', 'temp_staging_inactive', 'ingest_time', 'TIMESTAMP', 'NO', NULL, 'demo-init');
    """
    client.query(state_seed_sql).result()
    print("  + Seeded state telemetry for C1-05 (Billing Model), C1-03 (Scratch), C3-02 (Date Shards), and columns")

    # Seed jobs_events covering C1-01, C1-02, C3-01, C4-01, C4-03, C4-05, C4-06, C4-08, C4-09
    jobs_seed_sql = f"""
    INSERT INTO `{c.ops}.jobs_events` (
        region, project_id, job_id, user_email, job_type, statement_type, priority, state,
        cache_hit, creation_time, start_time, end_time, duration_ms,
        total_bytes_processed, total_bytes_billed, total_slot_ms,
        referenced_tables, destination_table, query_hash, query_preview, collected_at
    )
    -- 1. Queries on demo_ecommerce.orders (Scanning without clustering -> $1,500/mo spend baseline for C1-01)
    SELECT
        'us' AS region,
        '{c.project_id}' AS project_id,
        CONCAT('demo-job-orders-', CAST(i AS STRING)) AS job_id,
        'analytics-dashboard@company.internal' AS user_email,
        'QUERY' AS job_type,
        'SELECT' AS statement_type,
        'INTERACTIVE' AS priority,
        'DONE' AS state,
        FALSE AS cache_hit,
        TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL MOD(i, 80)*24 HOUR) AS creation_time,
        TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL MOD(i, 80)*24 HOUR) AS start_time,
        TIMESTAMP_ADD(TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL MOD(i, 80)*24 HOUR), INTERVAL 5 SECOND) AS end_time,
        5000 AS duration_ms,
        CAST(3 * POW(1024, 4) AS INT64) AS total_bytes_processed,
        CAST(3 * POW(1024, 4) AS INT64) AS total_bytes_billed,
        120000 AS total_slot_ms,
        [STRUCT('{c.project_id}' AS project_id, 'demo_ecommerce' AS dataset_id, 'orders' AS table_id)] AS referenced_tables,
        STRUCT('{c.project_id}' AS project_id, 'demo_ecommerce' AS dataset_id, 'anon_dest' AS table_id) AS destination_table,
        'hash_orders_dashboard_query' AS query_hash,
        'SELECT customer_id, SUM(order_amount) FROM demo_ecommerce.orders WHERE customer_id = @cid GROUP BY 1' AS query_preview,
        CURRENT_TIMESTAMP() AS collected_at
    FROM UNNEST(GENERATE_ARRAY(1, 240)) AS i

    UNION ALL

    -- 2. Queries on demo_ecommerce.clickstream_events (100% of queries use partition filters -> C1-02 candidate)
    SELECT
        'us' AS region,
        '{c.project_id}' AS project_id,
        CONCAT('demo-job-events-', CAST(i AS STRING)) AS job_id,
        'etl-runner@company.internal' AS user_email,
        'QUERY' AS job_type,
        'SELECT' AS statement_type,
        'INTERACTIVE' AS priority,
        'DONE' AS state,
        FALSE AS cache_hit,
        TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL MOD(i, 15)*24 HOUR) AS creation_time,
        TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL MOD(i, 15)*24 HOUR) AS start_time,
        TIMESTAMP_ADD(TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL MOD(i, 15)*24 HOUR), INTERVAL 3 SECOND) AS end_time,
        3000 AS duration_ms,
        CAST(80 * POW(1024, 3) AS INT64) AS total_bytes_processed,
        CAST(80 * POW(1024, 3) AS INT64) AS total_bytes_billed,
        45000 AS total_slot_ms,
        [STRUCT('{c.project_id}' AS project_id, 'demo_ecommerce' AS dataset_id, 'clickstream_events' AS table_id)] AS referenced_tables,
        STRUCT('{c.project_id}' AS project_id, 'demo_ecommerce' AS dataset_id, 'anon_dest' AS table_id) AS destination_table,
        'hash_events_etl_query' AS query_hash,
        'SELECT event_type, COUNT(*) FROM demo_ecommerce.clickstream_events WHERE event_date = CURRENT_DATE() GROUP BY 1' AS query_preview,
        CURRENT_TIMESTAMP() AS collected_at
    FROM UNNEST(GENERATE_ARRAY(1, 80)) AS i

    UNION ALL

    -- 2b. Queries on demo_ecommerce.customer_events_clean (100% of queries use partition filters -> C1-02 clean candidate)
    SELECT
        'us' AS region,
        '{c.project_id}' AS project_id,
        CONCAT('demo-job-clean-events-', CAST(i AS STRING)) AS job_id,
        'clean-etl-runner@company.internal' AS user_email,
        'QUERY' AS job_type,
        'SELECT' AS statement_type,
        'INTERACTIVE' AS priority,
        'DONE' AS state,
        FALSE AS cache_hit,
        TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL MOD(i, 15)*24 HOUR) AS creation_time,
        TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL MOD(i, 15)*24 HOUR) AS start_time,
        TIMESTAMP_ADD(TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL MOD(i, 15)*24 HOUR), INTERVAL 3 SECOND) AS end_time,
        3000 AS duration_ms,
        CAST(50 * POW(1024, 3) AS INT64) AS total_bytes_processed,
        CAST(50 * POW(1024, 3) AS INT64) AS total_bytes_billed,
        30000 AS total_slot_ms,
        [STRUCT('{c.project_id}' AS project_id, 'demo_ecommerce' AS dataset_id, 'customer_events_clean' AS table_id)] AS referenced_tables,
        STRUCT('{c.project_id}' AS project_id, 'demo_ecommerce' AS dataset_id, 'anon_dest' AS table_id) AS destination_table,
        'hash_clean_events_query' AS query_hash,
        'SELECT event_type, COUNT(*) FROM demo_ecommerce.customer_events_clean WHERE event_date = CURRENT_DATE() GROUP BY 1' AS query_preview,
        CURRENT_TIMESTAMP() AS collected_at
    FROM UNNEST(GENERATE_ARRAY(1, 80)) AS i

    UNION ALL

    -- 3. Queries on audit_logs_unpartitioned (Full table scans with SELECT * -> C3-01 and C4-01 candidate)
    SELECT
        'us' AS region,
        '{c.project_id}' AS project_id,
        CONCAT('demo-job-logs-', CAST(i AS STRING)) AS job_id,
        'sec-auditor@company.internal' AS user_email,
        'QUERY' AS job_type,
        'SELECT' AS statement_type,
        'INTERACTIVE' AS priority,
        'DONE' AS state,
        FALSE AS cache_hit,
        TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL MOD(i, 60)*24 HOUR) AS creation_time,
        TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL MOD(i, 60)*24 HOUR) AS start_time,
        TIMESTAMP_ADD(TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL MOD(i, 60)*24 HOUR), INTERVAL 8 SECOND) AS end_time,
        8000 AS duration_ms,
        CAST(400 * POW(1024, 3) AS INT64) AS total_bytes_processed,
        CAST(400 * POW(1024, 3) AS INT64) AS total_bytes_billed,
        250000 AS total_slot_ms,
        [STRUCT('{c.project_id}' AS project_id, 'demo_ecommerce' AS dataset_id, 'audit_logs_unpartitioned' AS table_id)] AS referenced_tables,
        STRUCT('{c.project_id}' AS project_id, 'demo_ecommerce' AS dataset_id, 'anon_dest' AS table_id) AS destination_table,
        'hash_logs_audit_query' AS query_hash,
        'SELECT * FROM demo_ecommerce.audit_logs_unpartitioned WHERE log_timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 1 DAY)' AS query_preview,
        CURRENT_TIMESTAMP() AS collected_at
    FROM UNNEST(GENERATE_ARRAY(1, 90)) AS i

    UNION ALL

    -- 4. Query with function-wrapped date in WHERE (-> C4-03 Anti-pattern candidate)
    SELECT
        'us' AS region,
        '{c.project_id}' AS project_id,
        CONCAT('demo-job-date-wrap-', CAST(i AS STRING)) AS job_id,
        'reporting@company.internal' AS user_email,
        'QUERY' AS job_type,
        'SELECT' AS statement_type,
        'INTERACTIVE' AS priority,
        'DONE' AS state,
        FALSE AS cache_hit,
        TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL MOD(i, 20)*24 HOUR) AS creation_time,
        TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL MOD(i, 20)*24 HOUR) AS start_time,
        TIMESTAMP_ADD(TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL MOD(i, 20)*24 HOUR), INTERVAL 4 SECOND) AS end_time,
        4000 AS duration_ms,
        CAST(300 * POW(1024, 3) AS INT64) AS total_bytes_processed,
        CAST(300 * POW(1024, 3) AS INT64) AS total_bytes_billed,
        180000 AS total_slot_ms,
        [STRUCT('{c.project_id}' AS project_id, 'demo_ecommerce' AS dataset_id, 'orders' AS table_id)] AS referenced_tables,
        STRUCT('{c.project_id}' AS project_id, 'demo_ecommerce' AS dataset_id, 'anon_dest' AS table_id) AS destination_table,
        'hash_date_wrap_query' AS query_hash,
        'SELECT order_id, order_amount FROM demo_ecommerce.orders WHERE DATE(created_at) = CURRENT_DATE()' AS query_preview,
        CURRENT_TIMESTAMP() AS collected_at
    FROM UNNEST(GENERATE_ARRAY(1, 30)) AS i

    UNION ALL

    -- 5. Query with cartesian CROSS JOIN (-> C4-05 Anti-pattern candidate)
    SELECT
        'us' AS region,
        '{c.project_id}' AS project_id,
        CONCAT('demo-job-cross-join-', CAST(i AS STRING)) AS job_id,
        'data-science@company.internal' AS user_email,
        'QUERY' AS job_type,
        'SELECT' AS statement_type,
        'INTERACTIVE' AS priority,
        'DONE' AS state,
        FALSE AS cache_hit,
        TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL MOD(i, 10)*24 HOUR) AS creation_time,
        TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL MOD(i, 10)*24 HOUR) AS start_time,
        TIMESTAMP_ADD(TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL MOD(i, 10)*24 HOUR), INTERVAL 7 SECOND) AS end_time,
        7000 AS duration_ms,
        CAST(500 * POW(1024, 3) AS INT64) AS total_bytes_processed,
        CAST(500 * POW(1024, 3) AS INT64) AS total_bytes_billed,
        300000 AS total_slot_ms,
        [STRUCT('{c.project_id}' AS project_id, 'demo_ecommerce' AS dataset_id, 'orders' AS table_id),
         STRUCT('{c.project_id}' AS project_id, 'demo_ecommerce' AS dataset_id, 'clickstream_events' AS table_id)] AS referenced_tables,
        STRUCT('{c.project_id}' AS project_id, 'demo_ecommerce' AS dataset_id, 'anon_dest' AS table_id) AS destination_table,
        'hash_cross_join_query' AS query_hash,
        'SELECT o.order_id, e.event_id FROM demo_ecommerce.orders o CROSS JOIN demo_ecommerce.clickstream_events e' AS query_preview,
        CURRENT_TIMESTAMP() AS collected_at
    FROM UNNEST(GENERATE_ARRAY(1, 20)) AS i

    UNION ALL

    -- 6. Query with NOT IN subquery (-> C4-06 Anti-pattern candidate)
    SELECT
        'us' AS region,
        '{c.project_id}' AS project_id,
        CONCAT('demo-job-notin-', CAST(i AS STRING)) AS job_id,
        'etl-analyst@company.internal' AS user_email,
        'QUERY' AS job_type,
        'SELECT' AS statement_type,
        'INTERACTIVE' AS priority,
        'DONE' AS state,
        FALSE AS cache_hit,
        TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL MOD(i, 15)*24 HOUR) AS creation_time,
        TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL MOD(i, 15)*24 HOUR) AS start_time,
        TIMESTAMP_ADD(TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL MOD(i, 15)*24 HOUR), INTERVAL 6 SECOND) AS end_time,
        6000 AS duration_ms,
        CAST(200 * POW(1024, 3) AS INT64) AS total_bytes_processed,
        CAST(200 * POW(1024, 3) AS INT64) AS total_bytes_billed,
        150000 AS total_slot_ms,
        [STRUCT('{c.project_id}' AS project_id, 'demo_ecommerce' AS dataset_id, 'orders' AS table_id)] AS referenced_tables,
        STRUCT('{c.project_id}' AS project_id, 'demo_ecommerce' AS dataset_id, 'anon_dest' AS table_id) AS destination_table,
        'hash_notin_query' AS query_hash,
        'SELECT order_id FROM demo_ecommerce.orders WHERE customer_id NOT IN (SELECT user_id FROM demo_ecommerce.clickstream_events)' AS query_preview,
        CURRENT_TIMESTAMP() AS collected_at
    FROM UNNEST(GENERATE_ARRAY(1, 25)) AS i

    UNION ALL

    -- 7. Query with non-deterministic cache busters (-> C4-08 Anti-pattern candidate)
    SELECT
        'us' AS region,
        '{c.project_id}' AS project_id,
        CONCAT('demo-job-cachebust-', CAST(i AS STRING)) AS job_id,
        'looker-sa@company.internal' AS user_email,
        'QUERY' AS job_type,
        'SELECT' AS statement_type,
        'INTERACTIVE' AS priority,
        'DONE' AS state,
        FALSE AS cache_hit,
        TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL MOD(i, 10)*24 HOUR) AS creation_time,
        TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL MOD(i, 10)*24 HOUR) AS start_time,
        TIMESTAMP_ADD(TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL MOD(i, 10)*24 HOUR), INTERVAL 3 SECOND) AS end_time,
        3000 AS duration_ms,
        CAST(150 * POW(1024, 3) AS INT64) AS total_bytes_processed,
        CAST(150 * POW(1024, 3) AS INT64) AS total_bytes_billed,
        90000 AS total_slot_ms,
        [STRUCT('{c.project_id}' AS project_id, 'demo_ecommerce' AS dataset_id, 'orders' AS table_id)] AS referenced_tables,
        STRUCT('{c.project_id}' AS project_id, 'demo_ecommerce' AS dataset_id, 'anon_dest' AS table_id) AS destination_table,
        'hash_cachebust_query' AS query_hash,
        'SELECT order_id, order_amount, CURRENT_TIMESTAMP() AS pulled_at FROM demo_ecommerce.orders WHERE order_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)' AS query_preview,
        CURRENT_TIMESTAMP() AS collected_at
    FROM UNNEST(GENERATE_ARRAY(1, 35)) AS i

    UNION ALL

    -- 8. Query with ORDER BY without LIMIT in subquery (-> C4-09 Anti-pattern candidate)
    SELECT
        'us' AS region,
        '{c.project_id}' AS project_id,
        CONCAT('demo-job-orderby-', CAST(i AS STRING)) AS job_id,
        'analyst@company.internal' AS user_email,
        'QUERY' AS job_type,
        'SELECT' AS statement_type,
        'INTERACTIVE' AS priority,
        'DONE' AS state,
        FALSE AS cache_hit,
        TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL MOD(i, 20)*24 HOUR) AS creation_time,
        TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL MOD(i, 20)*24 HOUR) AS start_time,
        TIMESTAMP_ADD(TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL MOD(i, 20)*24 HOUR), INTERVAL 5 SECOND) AS end_time,
        5000 AS duration_ms,
        CAST(180 * POW(1024, 3) AS INT64) AS total_bytes_processed,
        CAST(180 * POW(1024, 3) AS INT64) AS total_bytes_billed,
        110000 AS total_slot_ms,
        [STRUCT('{c.project_id}' AS project_id, 'demo_ecommerce' AS dataset_id, 'orders' AS table_id)] AS referenced_tables,
        STRUCT('{c.project_id}' AS project_id, 'demo_ecommerce' AS dataset_id, 'anon_dest' AS table_id) AS destination_table,
        'hash_orderby_query' AS query_hash,
        'WITH sorted_orders AS (SELECT * FROM demo_ecommerce.orders ORDER BY order_amount DESC) SELECT customer_id, AVG(order_amount) FROM sorted_orders GROUP BY 1' AS query_preview,
        CURRENT_TIMESTAMP() AS collected_at
    FROM UNNEST(GENERATE_ARRAY(1, 30)) AS i;
    """
    client.query(jobs_seed_sql).result()
    print("  + Seeded historical job events across all workload patterns (C4-01 through C4-09)")

    # Seed active recommender recommendations
    rec_seed_sql = f"""
    INSERT INTO `{c.ops}.recommender_recommendations` (
        recommender, recommendation_id, project_id, region, state, subtype, description,
        last_updated_time, target_resources_json, additional_details_json, raw_json,
        first_seen_at, last_seen_at, run_id
    )
    VALUES (
        'google.bigquery.table.PartitionClusterRecommender',
        'rec-demo-orders-cluster-001',
        '{c.project_id}',
        'us',
        'ACTIVE',
        'CLUSTER_TABLE',
        'Clustering demo_ecommerce.orders by customer_id, order_status will reduce scanned bytes by up to 68%.',
        CURRENT_TIMESTAMP(),
        '["//bigquery.googleapis.com/projects/{c.project_id}/datasets/demo_ecommerce/tables/orders"]',
        '\\{{"clustering_columns": ["customer_id", "order_status"], "est_gb_saved_monthly": 1638400\\}}',
        '\\{{\\}}',
        CURRENT_TIMESTAMP(),
        CURRENT_TIMESTAMP(),
        'demo-run-001'
    );
    """.replace('\\{', '{').replace('\\}', '}')
    client.query(rec_seed_sql).result()
    print("  + Seeded Active Assist PartitionClusterRecommender recommendation with $10,000 gross claim")

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

    # Step 5: Run Rules Engine to compile fresh change set cards
    print("\n[5/5] Running Rules Engine to evaluate telemetry and compile change sets...")
    cli.cmd_rules(c)

    print(f"""
================================================================================
DEMO ENVIRONMENT IS FULLY PROVISIONED!
================================================================================
Project:        {c.project_id}
Demo Dataset:   demo_ecommerce
Ops Dataset:    {c.ops}

RECOMMENDED DEMO STEPS:
1. Start the Review UI:
   python3 review_app/main.py

2. Open in your browser:
   http://localhost:8080

3. Review the pending change sets (Classes 1, 3, and 4):
   - C1-01: Clustering orders table by (customer_id, order_status)
   - C1-02: Require Partition Filter on customer_events_clean (clean apply) & clickstream_events (guardrail blocked)
   - C1-03: Staging dataset default expiration
   - C1-05: Storage billing model flip to physical
   - C3-01: Repartitioning audit_logs_unpartitioned
   - C3-02: Consolidating 30 date-sharded ga_sessions tables
   - C3-05: Archiving inactive temp_staging table to GCS
   - C4-01..C4-09: SQL anti-pattern recommendations

4. Approve a change set in the UI, then run execution:
   python3 -m optimizer.cli execute

5. Check receipts:
   python3 -m optimizer.cli verify

6. To reset for another demo:
   python3 scripts/demo_cleanup.py
================================================================================
""")

if __name__ == "__main__":
    setup_demo()
