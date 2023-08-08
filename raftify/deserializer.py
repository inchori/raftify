import logging
import pickle
from typing import Optional


def pickle_deserialize(data: bytes) -> Optional[str]:
    if data == b"":
        return None

    if pickle.PROTO in data:
        return pickle.loads(data[data.index(pickle.PROTO):])

    logging.warning("Failed to deserialize data: %s", data)
    # Not pickle data
    return data


def entry_data_deserializer(data: bytes) -> Optional[str]:
    return pickle_deserialize(data)


def entry_context_deserializer(data: bytes) -> Optional[str]:
    return pickle_deserialize(data)


def snapshot_data_deserializer(data: bytes) -> Optional[str]:
    return pickle_deserialize(data)


def message_context_deserializer(data: bytes) -> Optional[str]:
    return pickle_deserialize(data)
