ccbench_add_protocol(si_pwal
  SOURCES   transaction.cc util.cc result.cc garbage_collection.cc
  WORKLOADS ycsb tpcc
  OPTIONS
    CCBENCH_WAL_PWAL=1
    CCBENCH_WAL_PROTOCOL_NAME="si_pwal"
    KEY_SORT=${CCBENCH_KEY_SORT}
    INSERT_READ_DELAY_MS=${CCBENCH_INSERT_READ_DELAY_MS}
    INSERT_BATCH_DELAY_MS=${CCBENCH_INSERT_BATCH_DELAY_MS}
)
