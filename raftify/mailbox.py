import asyncio
import pickle
from asyncio import Queue
from typing import Optional

from rraft import ConfChange, ConfChangeType

from .error import UnknownError
from .pb_adapter import ConfChangeV2Adapter
from .protos import eraftpb_pb2, raft_service_pb2
from .raft_node import RaftNode
from .request_message import ConfigChangeReqMessage, ProposeReqMessage
from .response_message import (
    ClusterBootstrapReadyRespMessage,
    RaftRespMessage,
    ResponseMessage,
    WrongLeaderRespMessage,
)
from .utils import SocketAddr


class Mailbox:
    """
    A mailbox to send messages to a running raft node.
    """

    def __init__(self, raft_node: RaftNode):
        self.raft_node = raft_node
        self.message_queue = raft_node.message_queue
        self.logger = raft_node.logger
        self.raftify_config = raft_node.raftify_cfg

    async def __handle_response(
        self,
        response: ResponseMessage,
        *,
        reroute_msg_type: Optional[raft_service_pb2.RerouteMsgType] = None,
        proposed_data: Optional[bytes] = None,
        conf_change: Optional[eraftpb_pb2.ConfChangeV2] = None,
    ) -> Optional[bytes]:
        if isinstance(response, ClusterBootstrapReadyRespMessage):
            return None
        if isinstance(response, RaftRespMessage):
            return response.data
        elif isinstance(response, WrongLeaderRespMessage):
            assert reroute_msg_type is not None

            leader_id = self.raft_node.get_leader_id()
            leader = self.raft_node.peers[leader_id]

            assert leader and leader.client is not None

            resp_from_leader = await leader.client.reroute_message(
                reroute_msg_type=reroute_msg_type,
                conf_change=conf_change,
                msg_bytes=proposed_data,
                timeout=self.raftify_config.message_timeout,
            )

            if isinstance(resp_from_leader, raft_service_pb2.SendMessageResponse):
                return resp_from_leader.data
            else:
                # TODO: handle this case. The leader might change in the meanwhile.
                assert False

        raise UnknownError(f"Unknown response type: {response}")

    async def send(self, message: bytes) -> bytes:
        """
        Send a proposal message to commit to the node.
        """

        receiver: Queue = Queue()
        # TODO: make timeout duration a variable
        await self.message_queue.put(ProposeReqMessage(message, receiver))

        try:
            resp = await self.__handle_response(
                await asyncio.wait_for(receiver.get(), 2),
                reroute_msg_type=raft_service_pb2.Propose,
                proposed_data=message,
            )
            assert resp is not None
            return resp
        except Exception as e:
            self.logger.error("Error occurred while sending message through mailbox", e)
            raise

    async def leave(self, node_id: int, addr: SocketAddr) -> None:
        conf_change = ConfChange.default()
        conf_change.set_node_id(node_id)
        conf_change.set_context(pickle.dumps([addr]))
        conf_change.set_change_type(ConfChangeType.RemoveNode)
        conf_change_v2 = conf_change.as_v2()

        receiver: Queue = Queue()
        pb_conf_change_v2 = ConfChangeV2Adapter.to_pb(conf_change_v2)

        await self.message_queue.put(
            ConfigChangeReqMessage(pb_conf_change_v2, receiver)
        )

        res = await receiver.get()

        await self.__handle_response(
            res,
            reroute_msg_type=raft_service_pb2.ConfChange,
            conf_change=pb_conf_change_v2,
        )
