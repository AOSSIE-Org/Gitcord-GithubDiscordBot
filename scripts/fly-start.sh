
set -euo pipefail

.
bash /app/scripts/sync-loop.sh &


exec ghdcbot --config "${GITCORD_CONFIG:-/app/config/config.yaml}" bot
