#!/usr/bin/env bash

fail() {
    printf '%s\n' "$*" >&2
    exit 1
}

require_name() {
    [[ $1 =~ ^[A-Za-z][A-Za-z0-9_]{0,63}$ ]] || fail 'Invalid MySQL database name.'
}

mysql_args_from() {
    local credentials=$1 mode
    [[ -f $credentials && -r $credentials ]] || fail 'Supply a readable MySQL client option file.'
    credentials=$(realpath -e -- "$credentials")
    mode=$(stat -c '%a' -- "$credentials")
    (( (8#$mode & 077) == 0 )) || fail 'MySQL option file must be private (chmod 600).'
    mysql_args=("--defaults-extra-file=$credentials" --protocol=TCP --default-character-set=utf8mb4)
}

verify_snapshot() (
    cd -- "$1"
    [[ -f database.sql.gz && -f metadata.txt && -f SHA256SUMS ]] || fail 'Incomplete backup snapshot.'
    mapfile -t sums < SHA256SUMS
    [[ ${#sums[@]} -eq 2 ]] || fail 'Unexpected backup manifest.'
    [[ ${sums[0]} =~ ^[a-f0-9]{64}\ \ database\.sql\.gz$ ]] || fail 'Invalid database checksum entry.'
    [[ ${sums[1]} =~ ^[a-f0-9]{64}\ \ metadata\.txt$ ]] || fail 'Invalid metadata checksum entry.'
    sha256sum --check --strict -- SHA256SUMS
    gzip -t -- database.sql.gz
)
