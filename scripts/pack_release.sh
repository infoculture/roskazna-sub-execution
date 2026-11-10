#!/bin/bash
# Упаковка данных для релиза на GitHub (лимит — 2 ГиБ на файл).
#
#   scripts/pack_release.sh OUT_DIR
#
# В OUT_DIR появятся:
#   raw-annual-2000-2025.tar, raw-monthly-<годы>.tar  — исходные пакеты (data/raw/zip/…)
#   export-parquet.tar                               — Parquet-выгрузка (data/export/…)
#   roskazna_kbs.duckdb.zst.part-NN                  — база DuckDB, zstd, части по 1900 МиБ
#   catalog.csv, files.csv, members.csv.gz           — реестры
#   SHA256SUMS.txt
# Пути внутри tar — относительно data/: распаковка `tar -xf … -C data` восстанавливает
# структуру каталогов. Границы групп лет подобраны так, чтобы каждый tar был < 2 ГиБ;
# при росте данных проверьте размеры (ls -la OUT_DIR).
set -euo pipefail
OUT=$(cd "${1:?usage: $0 OUT_DIR}" && pwd)
cd "$(dirname "$0")/../data"
export COPYFILE_DISABLE=1
T="tar --no-mac-metadata --no-xattrs --no-acls"
yrs() { local t=$1 a=$2 b=$3; for y in $(seq "$a" "$b"); do [ -d "raw/zip/$t/$y" ] && echo "raw/zip/$t/$y"; done; }

$T -cf "$OUT/raw-annual-2000-2025.tar" $(yrs annual 2000 2025)
for r in "2000 2013" "2014 2016" "2017 2019" "2020 2022" "2023 2024" "2025 2026"; do
  set -- $r
  $T -cf "$OUT/raw-monthly-$1-$2.tar" $(yrs monthly "$1" "$2")
done
$T -cf "$OUT/export-parquet.tar" export
gzip -9 -c catalog/members.csv > "$OUT/members.csv.gz"
cp catalog/catalog.csv catalog/files.csv "$OUT/"
zstd -q -T0 -10 --long=27 -c db/roskazna_kbs.duckdb > "$OUT/roskazna_kbs.duckdb.zst"
cd "$OUT"
split -b 1900m -d -a 2 roskazna_kbs.duckdb.zst roskazna_kbs.duckdb.zst.part-
rm roskazna_kbs.duckdb.zst
shasum -a 256 -- * | grep -v ' SHA256SUMS.txt$' > SHA256SUMS.txt
ls -la
