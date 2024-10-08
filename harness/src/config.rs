use raftify::{Config, RaftConfig};

use crate::utils::{ensure_directory_exist, get_storage_path};

pub fn build_config(node_id: u64) -> Config {
    let raft_config = RaftConfig {
        id: node_id,
        election_tick: 10,
        heartbeat_tick: 3,
        omit_heartbeat_log: true,
        ..Default::default()
    };

    let storage_path = get_storage_path("./logs", node_id);
    ensure_directory_exist(&storage_path).expect("Failed to create storage directory");

    Config {
        log_dir: storage_path.clone(),
        save_compacted_logs: true,
        compacted_log_dir: storage_path,
        compacted_log_size_threshold: 1024 * 1024 * 1024,
        raft_config,
        ..Default::default()
    }
}
