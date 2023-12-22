use std::{fmt, marker::PhantomData};

use crate::{AbstractLogEntry, AbstractStateMachine, Error, HeedStorage, Peers};

pub enum ResponseMessage<LogEntry: AbstractLogEntry, FSM: AbstractStateMachine<LogEntry>> {
    Server(ServerResponseMsg),
    Local(LocalResponseMsg<LogEntry, FSM>),
    _Phantom(PhantomData<LogEntry>),
}

impl<LogEntry: AbstractLogEntry, FSM: AbstractStateMachine<LogEntry>>
    From<LocalResponseMsg<LogEntry, FSM>> for ResponseMessage<LogEntry, FSM>
{
    fn from(msg: LocalResponseMsg<LogEntry, FSM>) -> Self {
        ResponseMessage::Local(msg)
    }
}

impl<LogEntry: AbstractLogEntry, FSM: AbstractStateMachine<LogEntry>> From<ServerResponseMsg>
    for ResponseMessage<LogEntry, FSM>
{
    fn from(msg: ServerResponseMsg) -> Self {
        ResponseMessage::Server(msg)
    }
}

#[derive(Debug)]
pub enum ResponseResult {
    Success,
    Error(Error),
    WrongLeader { leader_id: u64, leader_addr: String },
}

#[derive(Debug)]
pub enum ConfChangeResponseResult {
    JoinSuccess { assigned_id: u64, peers: Peers },
    RemoveSuccess,
    Error(Error),
    WrongLeader { leader_id: u64, leader_addr: String },
}

#[derive(Debug)]
pub enum ServerResponseMsg {
    MemberBootstrapReady {
        result: ResponseResult,
    },
    ClusterBootstrapReady {
        result: ResponseResult,
    },
    Propose {
        result: ResponseResult,
    },
    ConfigChange {
        result: ConfChangeResponseResult,
    },
    RequestId {
        result: ResponseResult,
        reserved_id: Option<u64>,
        leader_id: Option<u64>,
        leader_addr: Option<String>,
        peers: Option<Peers>,
    },
    ReportUnreachable {
        result: ResponseResult,
    },
    DebugNode {
        result: String,
    },
    RaftMessage {
        result: ResponseResult,
    },
}

pub enum LocalResponseMsg<LogEntry: AbstractLogEntry, FSM: AbstractStateMachine<LogEntry>> {
    IsLeader { is_leader: bool },
    GetId { id: u64 },
    GetLeaderId { leader_id: u64 },
    GetPeers { peers: Peers },
    AddPeer {},
    Store { store: FSM },
    Storage { storage: HeedStorage },
    GetClusterSize { size: usize },
    ConfigChange { result: ConfChangeResponseResult },
    Quit {},
    MakeSnapshot {},
    Propose {},
    DebugNode { result: String },
    _Phantom(PhantomData<LogEntry>),
}

impl<LogEntry: AbstractLogEntry, FSM: AbstractStateMachine<LogEntry>> fmt::Debug
    for LocalResponseMsg<LogEntry, FSM>
{
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            LocalResponseMsg::Store { store: _store } => {
                write!(f, "LocalResponseMsg::Store")
            }
            _ => {
                write!(f, "{:?}", self)
            }
        }
    }
}
