#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
source "$(dirname -- "${BASH_SOURCE[0]}")/mysql-common.sh"

if [[ $# -lt 3 || $# -gt 4 ]]; then
    printf 'Usage: %s MYSQL_OPTION_FILE DATABASE LOCAL_DIRECTORY [OFFSITE_DIRECTORY]\n' "$0" >&2
    exit 2
fi

database=$2
require_name "$database"
mysql_args_from "$1"
local_dir=$(realpath -e -- "$3")
[[ -d $local_dir && -w $local_dir ]] || fail 'Local backup directory must exist and be writable.'
offsite_dir=''
if [[ $# -eq 4 ]]; then
    offsite_dir=$(realpath -e -- "$4")
    [[ -d $offsite_dir && -w $offsite_dir && $offsite_dir != "$local_dir" ]] ||
        fail 'Offsite destination must be a separate existing writable directory.'
fi

mysql_query=(mysql "${mysql_args[@]}" --batch --skip-column-names)
server_info=$("${mysql_query[@]}" -e     'SELECT VERSION(), @@GLOBAL.log_bin, @@GLOBAL.innodb_flush_log_at_trx_commit, @@GLOBAL.sync_binlog, @@GLOBAL.server_uuid')
IFS=$'\t' read -r server_version log_bin flush_at_commit sync_binlog server_uuid <<< "$server_info"
[[ $log_bin == 0 || $log_bin == 1 ]] || fail 'Could not determine MySQL binary logging state.'
non_innodb=$("${mysql_query[@]}" --database="$database" -e     "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=DATABASE() AND table_type='BASE TABLE' AND engine <> 'InnoDB'")
[[ $non_innodb == 0 ]] || fail 'A non-InnoDB table prevents a consistent transactional snapshot.'

stamp=$(date -u '+%Y%m%dT%H%M%SZ')
pending=$(mktemp -d -- "$local_dir/.i2v-backup-$stamp.XXXXXXXX")
copy_pending=''
cleanup() {
    if [[ -n $pending ]]; then
        rm -f -- "$pending/database.sql.gz" "$pending/metadata.txt" "$pending/SHA256SUMS"
        rmdir -- "$pending"
    fi
    if [[ -n $copy_pending ]]; then
        rm -f -- "$copy_pending/database.sql.gz" "$copy_pending/metadata.txt" "$copy_pending/SHA256SUMS"
        rmdir -- "$copy_pending"
    fi
}
trap cleanup EXIT
name="i2v-mysql-$database-$stamp-${pending##*.}"

dump_options=(--single-transaction --quick --skip-lock-tables --skip-add-drop-table
              --set-gtid-purged=OFF --no-tablespaces --routines --events --skip-add-locks)
if [[ $log_bin == 1 ]]; then
    dump_options+=(--source-data=2)
fi
mysqldump "${mysql_args[@]}" "${dump_options[@]}" -- "$database" | gzip -6 > "$pending/database.sql.gz"
printf 'schema=i2v-mysql-backup-v1\ndatabase=%s\nstarted_utc=%s\nserver_version=%s\nlog_bin=%s\ninnodb_flush_log_at_trx_commit=%s\nsync_binlog=%s\nserver_uuid=%s\n'     "$database" "$stamp" "$server_version" "$log_bin" "$flush_at_commit" "$sync_binlog" "$server_uuid" > "$pending/metadata.txt"
(cd -- "$pending" && sha256sum database.sql.gz metadata.txt > SHA256SUMS)
verify_snapshot "$pending"
mv -T -- "$pending" "$local_dir/$name"
pending=''
printf 'Local snapshot: %s\n' "$local_dir/$name"

if [[ -n $offsite_dir ]]; then
    copy_pending=$(mktemp -d -- "$offsite_dir/.i2v-backup-$stamp.XXXXXXXX")
    cp -- "$local_dir/$name/"{database.sql.gz,metadata.txt,SHA256SUMS} "$copy_pending/"
    verify_snapshot "$copy_pending"
    mv -T -- "$copy_pending" "$offsite_dir/$name"
    copy_pending=''
    printf 'Offsite snapshot: %s\n' "$offsite_dir/$name"
else
    printf 'No offsite copy was requested.\n' >&2
fi
