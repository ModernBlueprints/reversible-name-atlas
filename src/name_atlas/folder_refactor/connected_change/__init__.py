"""Payload-free Connected Change contracts, descriptors, and matching."""

from .accepted_plan import (
    ConnectedAcceptedFileMapping,
    FolderAcceptedPlanV2,
    build_connected_accepted_plan,
    validate_connected_accepted_plan,
)
from .contracts import (
    CapsuleAppliedExecutionOrigin,
    ConnectedChangeCore,
    ConnectedChangeError,
    ConnectedChangeFile,
    ConnectedChangeLinkSlot,
    ConnectedChangeMatchMapping,
    ConnectedChangeMatchReport,
    ConnectedChangeMember,
    FolderExecutionOrigin,
    GptPlannedExecutionOrigin,
)
from .descriptors import (
    ReceiverDescriptor,
    ReceiverLinkSlot,
    build_connected_change_core,
    build_receiver_descriptors,
    create_connected_change_file,
    parse_connected_change_file,
)
from .matcher import match_connected_change

__all__ = [
    "CapsuleAppliedExecutionOrigin",
    "ConnectedAcceptedFileMapping",
    "ConnectedChangeCore",
    "ConnectedChangeError",
    "ConnectedChangeFile",
    "ConnectedChangeLinkSlot",
    "ConnectedChangeMatchMapping",
    "ConnectedChangeMatchReport",
    "ConnectedChangeMember",
    "FolderExecutionOrigin",
    "FolderAcceptedPlanV2",
    "GptPlannedExecutionOrigin",
    "ReceiverDescriptor",
    "ReceiverLinkSlot",
    "build_connected_accepted_plan",
    "build_connected_change_core",
    "build_receiver_descriptors",
    "create_connected_change_file",
    "match_connected_change",
    "parse_connected_change_file",
    "validate_connected_accepted_plan",
]
