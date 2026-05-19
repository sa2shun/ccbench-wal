ccbench_add_protocol(si_wal
  SOURCES   transaction.cc util.cc result.cc garbage_collection.cc
  WORKLOADS ycsb tpcc
  OPTIONS
    CCBENCH_WAL_SHARED=1
    CCBENCH_WAL_PROTOCOL_NAME="si_wal"
    KEY_SORT=${CCBENCH_KEY_SORT}
    INSERT_READ_DELAY_MS=${CCBENCH_INSERT_READ_DELAY_MS}
    INSERT_BATCH_DELAY_MS=${CCBENCH_INSERT_BATCH_DELAY_MS}
)
