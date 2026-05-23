ccbench_add_protocol(rc_ssn_pwal
  SOURCES   transaction.cc util.cc result.cc garbage_collection.cc
  WORKLOADS ycsb
  OPTIONS
    CCBENCH_WAL_PROTOCOL_NAME="rc_ssn_pwal"
    CCBENCH_SSN_PWAL_WAIT_MODE=0
    KEY_SORT=${CCBENCH_KEY_SORT}
    INSERT_READ_DELAY_MS=${CCBENCH_INSERT_READ_DELAY_MS}
    INSERT_BATCH_DELAY_MS=${CCBENCH_INSERT_BATCH_DELAY_MS}
)
