#!/bin/bash
# Kernel zram with each algorithm on the same 67k real application pages: ratio and throughput.
set -eu
src=/dev/vdb; bytes=$(blockdev --getsize64 $src); pages=$((bytes / 4096))
cat $src > /dev/null   # page cache warm, so reads measure compression only
for algo in lzo-rle lz4 zstd; do
  for run in 1 2 3; do
    id=$(cat /sys/class/zram-control/hot_add)
    dev=/dev/zram$id
    echo $algo > /sys/block/zram$id/comp_algorithm
    echo 1G > /sys/block/zram$id/disksize
    t0=$(date +%s.%N); dd if=$src of=$dev bs=4096 count=$pages oflag=direct status=none; t1=$(date +%s.%N)
    sync; echo 3 > /proc/sys/vm/drop_caches
    t2=$(date +%s.%N); dd if=$dev of=/dev/null bs=4096 count=$pages iflag=direct status=none; t3=$(date +%s.%N)
    read orig compr used _ < /sys/block/zram$id/mm_stat
    python3 -c "print(f'$algo run $run: ratio {int('$orig')/int('$used'):.2f} (data {int('$orig')>>20} MiB -> {int('$used')>>20} MiB) write {int('$orig')/2**20/($t1-$t0):.0f} MiB/s read {int('$orig')/2**20/($t3-$t2):.0f} MiB/s')"
    echo $id > /sys/class/zram-control/hot_remove
  done
done
