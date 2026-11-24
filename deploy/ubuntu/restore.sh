#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
source "$(dirname -- "${BASH_SOURCE[0]}")/mysql-common.sh"

if [[ $# -ne 4 || $4 != --create-new ]]; then
    printf 'Usage: %s MYSQL_OPTION_FILE SNAPSHOT_DIRECTORY NEW_DATABASE --create-new\n' "$0" >&2
    exit 2
fi

snapshot=$(realpath -e -- "$2")
target=$3
require_name "$target"
mysql_args_from "$1"
verify_snapshot "$snapshot"
grep -qx 'schema=i2v-mysql-backup-v1' "$snapshot/metadata.txt" || fail 'Unsupported backup format.'
source_name=$(sed -n 's/^database=//p' "$snapshot/metadata.txt")
require_name "$source_name"
[[ ${target,,} != "${source_name,,}" ]] || fail 'Restore target must differ from source database.'

mysql_query=(mysql "${mysql_args[@]}" --batch --skip-column-names)
printf 'Restoring into a new database, %s.\n' "$target" >&2
# CREATE DATABASE without IF NOT EXISTS is the atomic guard against overwriting a database.
"${mysql_query[@]}" -e "CREATE DATABASE \`$target\` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
gzip -dc -- "$snapshot/database.sql.gz" | mysql "${mysql_args[@]}" --binary-mode --database="$target"
"${mysql_query[@]}" --database="$target" -e     'SELECT "candidates", COUNT(*) FROM candidates UNION ALL SELECT "works", COUNT(*) FROM works UNION ALL SELECT "quotas", COUNT(*) FROM quotas UNION ALL SELECT "audit_logs", COUNT(*) FROM audit_logs'
printf 'Restore completed in %s. Review the row counts before switching traffic.\n' "$target"
