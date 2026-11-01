#!/bin/sh
# Auto-invoked by Google Cloud Shell (`gcr.io/ds-artifacts-cloudshell/deploystack_custom_image`)
# immediately after cloning the repository — before `dsexec` makes any GCP API calls, avoiding
# Cloud Shell's shared `project_number:618104708054` `serviceusage.Services.List` 429 quota limit.
./scripts/install.sh --prompt-project --yes
_bqopt_rc=$?
stty sane 2>/dev/null || true
exit "$_bqopt_rc"
