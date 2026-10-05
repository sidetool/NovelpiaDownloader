#!/bin/sh
# Close the hidden form through its normal event, preserving original config saving.
set -eu
curl -fsS --max-time 3 -X POST http://127.0.0.1:8090/internal/close >/dev/null
