import asyncio
import logging
import time
from asyncio import Queue
from typing import Dict, List, Optional

from rraft import (
    ConfChange,
    ConfChangeType,
    Config,
    Entry,
    Entry_Ref,
    EntryType,
    Logger_Ref,
    Message,
    Raft,
    RawNode,
    Snapshot,
    Storage,
)

from riteraft.lmdb import LMDBStorage
from riteraft.message import (
    MessageConfigChange,
    MessagePropose,
    MessageRaft,
    MessageReportUnreachable,
    MessageRequestId,
    RaftRespIdReserved,
    RaftRespJoinSuccess,
    RaftRespOk,
    RaftRespResponse,
    RaftRespWrongLeader,
)
from riteraft.message_sender import MessageSender
from riteraft.raft_client import RaftClient
from riteraft.store import AbstractStore
from riteraft.utils import AtomicInteger, decode_u64, encode_u64


def persist(raft: Raft):
    if snapshot := raft.get_raft_log().unstable_snapshot():
        snap = snapshot.clone()
        index = snap.get_metadata().get_index()
        raft.get_raft_log().stable_snap(index)
        raft.get_raft_log().get_store().wl(lambda core: core.apply_snapshot(snap))
        raft.on_persist_snap(index)
        raft.commit_apply(index)

    if unstable := raft.get_raft_log().unstable_entries():
        last_entry = unstable[-1]
        cloned_unstable = list(map(lambda x: x.clone(), unstable))

        last_idx, last_term = last_entry.get_index(), last_entry.get_term()
        raft.get_raft_log().stable_entries(last_idx, last_term)

        raft.get_raft_log().get_store().wl(lambda core: core.append(cloned_unstable))
        raft.on_persist_entries(last_idx, last_term)


class RaftNode:
    def __init__(
        self,
        raw_node: RawNode,
        # the peer client could be optional, because an id can be reserved and later populated
        peers: Dict[int, Optional[RaftClient]],
        chan: Queue,
        store: AbstractStore,
        storage: Storage,
        should_quit: bool,
        seq: AtomicInteger,
        last_snap_time: float,
    ):
        self.raw_node = raw_node
        self.peers = peers
        self.chan = chan
        self.store = store
        self.storage = storage
        self.should_quit = should_quit
        self.seq = seq
        self.last_snap_time = last_snap_time

    @staticmethod
    def new_leader(chan: Queue, store: AbstractStore, logger: Logger_Ref) -> "RaftNode":
        config = Config.default()
        config.set_id(1)
        config.set_election_tick(10)
        # Heartbeat tick is for how long the leader needs to send
        # a heartbeat to keep alive.
        config.set_heartbeat_tick(3)
        config.validate()

        snapshot = Snapshot.default()
        # Because we don't use the same configuration to initialize every node, so we use
        # a non-zero index to force new followers catch up logs by snapshot first, which will
        # bring all nodes to the same initial state.
        snapshot.get_metadata().set_index(1)
        snapshot.get_metadata().set_term(1)
        snapshot.get_metadata().get_conf_state().set_voters([1])

        lmdb = LMDBStorage.create(".", 1)
        lmdb.apply_snapshot(snapshot)

        storage = Storage(lmdb)
        # storage.wl(lambda core: core.apply_snapshot(snapshot))
        raw_node = RawNode(config, storage, logger)

        peers = {}
        seq = AtomicInteger(0)
        last_snap_time = time.time()

        raw_node.get_raft().become_candidate()
        raw_node.get_raft().become_leader()

        persist(raw_node.get_raft())

        return RaftNode(
            raw_node,
            peers,
            chan,
            store,
            storage,
            False,
            seq,
            last_snap_time,
        )

    @staticmethod
    def new_follower(
        chan: Queue,
        id: int,
        store: AbstractStore,
        logger: Logger_Ref,
    ) -> "RaftNode":
        config = Config.default()
        config.set_id(id)
        config.set_election_tick(10)
        # Heartbeat tick is for how long the leader needs to send
        # a heartbeat to keep alive.
        config.set_heartbeat_tick(3)
        config.validate()

        storage = Storage(LMDBStorage.create(".", id))
        raw_node = RawNode(config, storage, logger)
        persist(raw_node.get_raft())

        peers = {}
        seq = AtomicInteger(0)
        last_snap_time = time.time()

        return RaftNode(
            raw_node,
            peers,
            chan,
            store,
            storage,
            False,
            seq,
            last_snap_time,
        )

    def id(self) -> int:
        return self.raw_node.get_raft().get_id()

    def leader(self) -> int:
        return self.raw_node.get_raft().get_leader_id()

    def is_leader(self) -> bool:
        return self.id() == self.leader()

    def peer_addrs(self) -> Dict[int, str]:
        return {k: str(v.addr) for k, v in self.peers.items()}

    def reserve_next_peer_id(self) -> int:
        """
        Reserve a slot to insert node on next node addition commit
        """
        next_id = max(self.peers.keys()) if any(self.peers) else 1
        # if assigned id is ourself, return next one
        next_id = max(next_id + 1, self.id())
        self.peers[next_id] = None

        logging.info(f"Reserved peer id {next_id}")
        return next_id

    def send_messages(self, msgs: List[Message]):
        for msg in msgs:
            logging.debug(
                f"light ready message from {msg.get_from()} to {msg.get_to()}"
            )

            if client := self.peers.get(msg.get_to()):
                asyncio.create_task(
                    MessageSender(
                        client_id=msg.get_to(),
                        client=client,
                        chan=self.chan,
                        message=msg,
                        timeout=0.1,
                        max_retries=5,
                    ).send()
                )

    async def send_wrong_leader(self, channel: Queue) -> None:
        leader_id = self.leader()
        # leader can't be an empty node
        leader_addr = str(self.peers[leader_id].addr)
        raft_response = RaftRespWrongLeader(
            leader_id,
            leader_addr,
        )
        # TODO handle error here
        await channel.put(raft_response)

    async def handle_committed_entries(
        self, committed_entries: List[Entry], client_senders: Dict[int, Queue]
    ) -> None:
        # Mostly, you need to save the last apply index to resume applying
        # after restart. Here we just ignore this because we use a Memory storage.

        # _last_apply_index = 0

        for entry in committed_entries:
            # Empty entry, when the peer becomes Leader it will send an empty entry.
            if not entry.get_data():
                continue

            if entry.get_entry_type() == EntryType.EntryNormal:
                await self.handle_normal(entry, client_senders)

            elif entry.get_entry_type() == EntryType.EntryConfChange:
                await self.handle_config_change(entry, client_senders)

            elif entry.get_entry_type() == EntryType.EntryConfChangeV2:
                raise NotImplementedError

    async def handle_normal(self, entry: Entry_Ref, senders: Dict[int, Queue]) -> None:
        seq = decode_u64(entry.get_context())
        data = await self.store.apply(entry.get_data())

        if sender := senders.pop(seq, None):
            await sender.put(RaftRespResponse(data))

        if time.time() > self.last_snap_time + 15:
            logging.info("Creating snapshot...")
            self.last_snap_time = time.time()
            last_applied = self.raw_node.get_raft().get_raft_log().get_applied()
            snapshot = await self.store.snapshot()
            self.storage.wl(lambda core: core.compact(last_applied))
            self.storage.wl(lambda core: core.create_snapshot(snapshot))

    async def handle_config_change(
        self, entry: Entry_Ref, senders: Dict[int, Queue]
    ) -> None:
        seq = decode_u64(entry.get_context())
        change = ConfChange.decode(entry.get_data())
        id = change.get_node_id()

        change_type = change.get_change_type()

        if change_type == ConfChangeType.AddNode:
            addr = decode_u64(change.get_context())
            logging.info(f"Adding {addr} ({id}) to peers")
            self.peers[id] = RaftClient(addr)
        elif change_type == ConfChangeType.RemoveNode:
            if change.get_node_id() == self.id():
                self.should_quit = True
                logging.warning("Quitting the cluster")
            else:
                self.peers.pop(change.get_node_id())
        else:
            raise NotImplementedError

        if cs := self.raw_node.apply_conf_change(change):
            last_applied = self.raw_node.get_raft().get_raft_log().get_applied()
            snapshot = await self.store.snapshot()

            self.storage.wl(lambda core: core.set_conf_state(cs))
            self.storage.wl(lambda core: core.compact(last_applied))
            self.storage.wl(lambda core: core.create_snapshot(snapshot))

        if sender := senders.pop(seq, None):
            if change_type == ConfChangeType.AddNode:
                response = RaftRespJoinSuccess(
                    assigned_id=id, peer_addrs=self.peer_addrs()
                )
            elif change_type == ConfChangeType.RemoveNode:
                response = RaftRespOk()
            else:
                raise NotImplementedError

            try:
                await sender.put(response)
            except Exception:
                logging.error("Error sending response")

    async def run(self) -> None:
        heartbeat = 0.1

        # A map to contain sender to client responses
        client_senders: Dict[int, Queue] = {}

        while True:
            if self.should_quit:
                logging.warning("Quitting raft")
                return

            message = None

            try:
                message = await asyncio.wait_for(self.chan.get(), heartbeat)
            except asyncio.TimeoutError:
                pass
            except asyncio.CancelledError:
                logging.warning("Cancelled error occurred!")
                raise
            except Exception:
                raise

            if isinstance(message, MessageConfigChange):
                # whenever a change id is 0, it's a message to self.
                if message.change.get_node_id() == 0:
                    message.change.set_node_id(self.id())

                if not self.is_leader():
                    # wrong leader send client cluster data
                    # TODO: retry strategy in case of failure
                    await self.send_wrong_leader(channel=message.chan)
                else:
                    # leader assign new id to peer
                    logging.debug(
                        f"Received request from: {message.change.get_node_id()}"
                    )
                    self.seq.increase()
                    client_senders[self.seq.value] = message.chan
                    context = encode_u64(self.seq.value)
                    self.raw_node.propose_conf_change(context, message.change)

            elif isinstance(message, MessagePropose):
                if not self.is_leader():
                    await self.send_wrong_leader(message.chan)
                else:
                    self.seq.increase()
                    client_senders[self.seq.value] = message.chan
                    context = encode_u64(self.seq.value)
                    self.raw_node.propose(context, message.proposal)

            elif isinstance(message, MessageRequestId):
                if not self.is_leader():
                    # TODO: retry strategy in case of failure
                    logging.info("Requested Id, but not leader")
                    await self.send_wrong_leader(message.chan)
                else:
                    await message.chan.put(
                        RaftRespIdReserved(self.reserve_next_peer_id())
                    )

            elif isinstance(message, MessageRaft):
                logging.debug(
                    f"Raft message: to={self.id()} from={message.msg.get_from()}"
                )
                self.raw_node.step(message.msg)

            elif isinstance(message, MessageReportUnreachable):
                self.raw_node.report_unreachable(message.node_id)

            self.raw_node.tick()
            await self.on_ready(client_senders)

    async def on_ready(self, client_senders: Dict[int, Queue]) -> None:
        if not self.raw_node.has_ready():
            return

        ready = self.raw_node.ready()

        # Send out the messages.
        self.send_messages(ready.messages())

        snapshot_default = Snapshot.default()
        if ready.snapshot() != snapshot_default.make_ref():
            snapshot = ready.snapshot()
            await self.store.restore(snapshot.get_data())
            self.storage.wl(lambda core: core.apply_snapshot(snapshot))

        await self.handle_committed_entries(ready.committed_entries(), client_senders)

        self.storage.wl(lambda core: core.append(ready.entries()))

        if hs := ready.hs():
            # Raft HardState changed, and we need to persist it.
            self.storage.wl(lambda core: core.set_hard_state(hs))

        if any(ready.persisted_messages()):
            # Send out the persisted messages come from the node.
            self.send_messages(ready.persisted_messages())

        light_rd = self.raw_node.advance(ready.make_ref())

        if commit := light_rd.commit_index():
            self.storage.wl(lambda core: core.set_hard_state_comit(commit))

        # Send out the messages.
        self.send_messages(light_rd.messages())

        # Apply all committed entries.
        await self.handle_committed_entries(
            light_rd.committed_entries(), client_senders
        )
        self.raw_node.advance_apply()
