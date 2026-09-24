import argparse
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


def cleanup_demo(wipe_ops: bool = False):
    c = cfg()
    client = bigquery.Client(project=c.project_id)
    print(f"[*] Starting Demo Cleanup in project: {c.project_id}...")

    # Step 1: Drop demo workload and scratch datasets
    for ds_name in ("demo_ecommerce", "demo_scratch"):
        ds_id = f"{c.project_id}.{ds_name}"
        try:
            print(f"  - Deleting workload dataset: {ds_id} (and all tables)...")
            client.delete_dataset(ds_id, delete_contents=True, not_found_ok=True)
            print(f"  + Deleted {ds_id}")
        except Exception as e:
            print(f"  ! Note during dataset deletion: {e}")

    # Step 2: Clear demo records or wipe optimizer_ops dataset
    if wipe_ops:
        ops_ds_id = c.ops
        print(f"  - Wiping entire control plane dataset: {ops_ds_id}...")
        try:
            client.delete_dataset(ops_ds_id, delete_contents=True, not_found_ok=True)
            print(f"  + Deleted {ops_ds_id}")
        except Exception as e:
            print(f"  ! Note during ops dataset deletion: {e}")
    else:
        print(f"  - Clearing all prior telemetry and change sets from {c.ops}...")
        statements = [
            f"DELETE FROM `{c.ops}.jobs_events` WHERE project_id = '{c.project_id}'",
            f"DELETE FROM `{c.ops}.jobs_hourly_slots` WHERE project_id = '{c.project_id}'",
            f"DELETE FROM `{c.ops}.recommender_recommendations` WHERE project_id = '{c.project_id}'",
            f"DELETE FROM `{c.ops}.recommender_insights` WHERE project_id = '{c.project_id}'",
            f"DELETE FROM `{c.ops}.change_sets` WHERE target_project = '{c.project_id}'",
            f"DELETE FROM `{c.ops}.table_state_daily` WHERE project_id = '{c.project_id}'",
            f"DELETE FROM `{c.ops}.columns_daily` WHERE project_id = '{c.project_id}'",
            f"DELETE FROM `{c.ops}.table_partitions_daily` WHERE project_id = '{c.project_id}'",
            f"DELETE FROM `{c.ops}.dataset_state_daily` WHERE project_id = '{c.project_id}'",
            f"DELETE FROM `{c.ops}.object_state_raw_daily` WHERE project_id = '{c.project_id}'",
        ]
        for stmt in statements:
            try:
                client.query(stmt).result()
            except Exception as e:
                print(f"  ! Note during ops query ({stmt[:30]}...): {e}")
        print(f"  + Cleared all telemetry, columns, partitions & change sets from {c.ops}")

    print("""
================================================================================
✅ CLEANUP COMPLETE — DEMO ENVIRONMENT RESET TO CLEAN STATE!
================================================================================
You can re-run the demo anytime with:
   ./scripts/demo_setup.sh
================================================================================
""")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Wipe demo assets and reset bq-optimizer state.")
    parser.add_argument("--wipe-ops", "--all", dest="wipe_ops", action="store_true",
                        help="Completely delete the optimizer_ops dataset as well (100%% full teardown)")
    args = parser.parse_args()
    cleanup_demo(wipe_ops=args.wipe_ops)
