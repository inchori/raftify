use std::time::Duration;
use tokio::time::sleep;

use harness::{
    constant::{ONE_NODE_EXAMPLE, RAFT_ADDRS, THREE_NODE_EXAMPLE},
    raft_server::{run_rafts, spawn_extra_node, RAFTS},
    utils::{load_peers, wait_for_until_cluster_size_increase},
};

#[tokio::test]
pub async fn test_static_bootstrap() {
    let peers = load_peers(THREE_NODE_EXAMPLE).await.unwrap();
    let _raft_tasks = tokio::spawn(run_rafts(peers.clone()));

    sleep(Duration::from_secs(1)).await;

    let mut rafts = RAFTS.lock().unwrap();
    let raft_1 = rafts.get(&1).unwrap();

    wait_for_until_cluster_size_increase(raft_1.clone(), 3).await;

    for (_, raft) in rafts.iter_mut() {
        raft.raft_node.quit().await;
    }

    sleep(Duration::from_secs(1)).await;
}

#[tokio::test]
pub async fn test_dynamic_bootstrap() {
    let peers = load_peers(ONE_NODE_EXAMPLE).await.unwrap();
    let _raft_tasks = tokio::spawn(run_rafts(peers.clone()));
    sleep(Duration::from_secs(1)).await;

    tokio::spawn(spawn_extra_node(RAFT_ADDRS[0], "127.0.0.1:60062"))
        .await
        .unwrap()
        .unwrap();
    sleep(Duration::from_secs(1)).await;

    tokio::spawn(spawn_extra_node(RAFT_ADDRS[0], "127.0.0.1:60063"))
        .await
        .unwrap()
        .unwrap();
    sleep(Duration::from_secs(1)).await;

    let mut rafts = RAFTS.lock().unwrap();
    let raft_1 = rafts.get(&1).unwrap();

    wait_for_until_cluster_size_increase(raft_1.clone(), 3).await;

    for (_, raft) in rafts.iter_mut() {
        raft.raft_node.quit().await;
    }
}
