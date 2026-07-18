#!/usr/bin/env python

import uuid

from dataclasses import dataclass, field
from typing import List, Dict, Any
import cv2

# Reserved port id for a workflow node's implicit rendered-output port in the
# shot workflow graph. Never stored in a param dict.
RESULT_PORT = "__result__"

# Port-id prefix for an exposed ComfyUI node output ("nodeout:<node_id>:<slot>").
# Exposure choices live in workflow.parameters["exposedNodeOutputs"]
# ([{"port", "label"}]) so they serialize with the assignment.
NODE_OUTPUT_PREFIX = "nodeout:"


def workflow_param_port_id(param: Dict[str, Any]) -> str:
    """
    Stable port identity for a workflow parameter: name + owning node ids.
    Matches the (name, nodeIDs) identity used by syncWorkflowFromFile, so port
    ids survive workflow file resyncs.
    """
    return f"{param.get('name', '')}@{','.join(str(n) for n in param.get('nodeIDs', []))}"


def node_output_port_id(node_id, slot=0) -> str:
    """Port id for a ComfyUI node's output slot."""
    return f"{NODE_OUTPUT_PREFIX}{node_id}:{slot}"


def node_output_port_node_id(port) -> 'str | None':
    """The ComfyUI node id of a node-output port id, or None for other ports."""
    if isinstance(port, str) and port.startswith(NODE_OUTPUT_PREFIX):
        return port[len(NODE_OUTPUT_PREFIX):].rsplit(":", 1)[0]
    return None


def default_shot_graph() -> Dict[str, Any]:
    return {"connections": [], "positions": {}}


def ensure_unique_workflow_uids(shot: 'Shot') -> bool:
    """
    Repairs missing/duplicated workflow uids WITHIN one shot (deepcopied
    assignments share uids). Cross-shot duplicates are fine — connections are
    shot-internal — so uids must never be used as global keys.
    """
    seen = set()
    changed = False
    for wf in shot.workflows:
        if not wf.uid or wf.uid in seen:
            wf.uid = uuid.uuid4().hex
            changed = True
        seen.add(wf.uid)
    return changed


@dataclass
class WorkflowAssignment:
    path: str
    enabled: bool = True
    parameters: Dict[str, Any] = field(default_factory=dict)
    isVideo: bool = False
    lastSignature: str = field(default_factory=str)
    versions: List[Dict[str, Any]] = field(default_factory=list)  # New field for version snapshots
    # Index of the version currently selected in the UI dropdown (0 == placeholder / latest params).
    selected_version_index: int = 0
    # Stable identity within a shot (graph connections reference it).
    uid: str = ""
    # Most recent rendered output of this workflow (the graph "Result" port).
    lastOutput: str = ""
    lastOutputIsVideo: bool = False
    # Most recent rendered file per ComfyUI output node ({node_id: path}) —
    # feeds exposed node-output ports ("nodeout:<node_id>:<slot>").
    lastNodeOutputs: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        if not self.uid:
            self.uid = uuid.uuid4().hex

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "enabled": self.enabled,
            "parameters": self.parameters,
            "isVideo": self.isVideo,
            "lastSignature": self.lastSignature,
            "versions": self.versions,  # Include versions when serializing
            "selected_version_index": self.selected_version_index,
            "uid": self.uid,
            "lastOutput": self.lastOutput,
            "lastOutputIsVideo": self.lastOutputIsVideo,
            "lastNodeOutputs": self.lastNodeOutputs
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'WorkflowAssignment':
        return cls(
            path=data.get('path', ''),
            enabled=data.get('enabled', True),
            parameters=data.get('parameters', {}),
            isVideo=data.get('isVideo', False),
            lastSignature=data.get('lastSignature', ""),
            versions=data.get('versions', []),  # Load versions if present
            selected_version_index=data.get('selected_version_index', 0),
            uid=data.get('uid', ""),  # empty -> __post_init__ generates one
            lastOutput=data.get('lastOutput', ""),
            lastOutputIsVideo=data.get('lastOutputIsVideo', False),
            lastNodeOutputs=data.get('lastNodeOutputs', {}) or {}
        )

    def get(self, name, default=None):
        if hasattr(self, name):
            return getattr(self, name)
        else:
            return default

@dataclass
class Shot:
    name: str = "Unnamed Shot"
    videoPath: str = ""
    videoVersions: List[str] = field(default_factory=list)
    currentVideoVersion: int = -1
    stillPath: str = ""
    imageVersions: List[str] = field(default_factory=list)
    currentImageVersion: int = -1
    lastStillSignature: str = ""
    lastVideoSignature: str = ""
    workflows: List[WorkflowAssignment] = field(default_factory=list)
    params: List[Dict[str, Any]] = field(default_factory=list)
    # Shot workflow graph: {"connections": [{"from": {"wf": uid, "port": id},
    # "to": {"wf": uid, "port": id}}], "positions": {uid: [x, y]}}.
    # Port ids come from workflow_param_port_id(); RESULT_PORT is the implicit
    # rendered-output port of each workflow node.
    graph: Dict[str, Any] = field(default_factory=default_shot_graph)
    # Remove the raw "duration" field from the initializer.
    # Instead, we use a default fallback duration (in seconds) if video cannot be read.
    default_duration: int = 5
    inPoint: float = 0.0  # fraction (0.0-1.0) for trimmed start
    outPoint: float = 1.0  # fraction (0.0-1.0) for trimmed end
    linkedAudio: bool = True  # whether the clip has a linked audio clip
    thumbnail_path: str = ""
    fps: float = 24.0  # frames per second; can be set externally
    # Private field to cache the computed (raw) duration (in seconds) of the video.
    _cached_duration: float = field(init=False, default=None)

    def __post_init__(self):
        # Initialize the cached duration as None.
        self._cached_duration = None

    def __setattr__(self, name, value):
        # Invalidate the cached duration if videoPath, inPoint, or outPoint change.
        if name in {"videoPath", "inPoint", "outPoint"}:
            object.__setattr__(self, "_cached_duration", None)
        object.__setattr__(self, name, value)

    @property
    def raw_duration(self) -> float:
        """
        The full (untrimmed) duration of the source video in seconds.
        Falls back to default_duration when the file can't be read.
        """
        if self._cached_duration is None:
            duration = float(self.default_duration)
            if self.videoPath:
                cap = None
                try:
                    cap = cv2.VideoCapture(self.videoPath)
                    if cap.isOpened():
                        frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
                        video_fps = cap.get(cv2.CAP_PROP_FPS)
                        # Use the shot's fps if set (and > 0); otherwise, fall back to the video's fps.
                        used_fps = self.fps if self.fps > 0 else video_fps
                        if used_fps > 0 and frame_count > 0:
                            duration = frame_count / used_fps
                except Exception:
                    pass
                finally:
                    if cap is not None:
                        cap.release()
            self._cached_duration = duration
        return self._cached_duration

    @property
    def duration(self) -> float:
        """
        The shot's effective duration (in seconds): the raw source duration
        scaled by the (inPoint, outPoint) trim fractions.
        """
        return self.raw_duration * (self.outPoint - self.inPoint)

    @duration.setter
    def duration(self, value: float):
        """
        Allows manual override of the cached duration.
        """
        self._cached_duration = value

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "videoPath": self.videoPath,
            "videoVersions": self.videoVersions,
            "currentVideoVersion": self.currentVideoVersion,
            "stillPath": self.stillPath,
            "imageVersions": self.imageVersions,
            "currentImageVersion": self.currentImageVersion,
            "lastStillSignature": self.lastStillSignature,
            "lastVideoSignature": self.lastVideoSignature,
            "workflows": [workflow.to_dict() for workflow in self.workflows],
            "params": self.params,
            "graph": self.graph,
            "duration": self.raw_duration,  # untrimmed source duration (fallback when file is unreadable)
            "inPoint": self.inPoint,
            "outPoint": self.outPoint,
            "linkedAudio": self.linkedAudio,
            "thumbnail_path": self.thumbnail_path,
            "fps": self.fps
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Shot':
        workflows_data = data.get('workflows', [])
        workflows = [WorkflowAssignment.from_dict(wf) for wf in workflows_data]
        graph = data.get('graph') or default_shot_graph()
        if not isinstance(graph, dict):
            graph = default_shot_graph()
        graph.setdefault("connections", [])
        graph.setdefault("positions", {})
        shot = cls(
            name=data.get('name', "Unnamed Shot"),
            videoPath=data.get('videoPath', ""),
            videoVersions=data.get('videoVersions', []),
            currentVideoVersion=data.get('currentVideoVersion', -1),
            stillPath=data.get('stillPath', ""),
            imageVersions=data.get('imageVersions', []),
            currentImageVersion=data.get('currentImageVersion', -1),
            lastStillSignature=data.get('lastStillSignature', ""),
            lastVideoSignature=data.get('lastVideoSignature', ""),
            workflows=workflows,
            params=data.get('params', []),
            graph=graph,
            default_duration=data.get('duration', 5),
            inPoint=data.get('inPoint', 0.0),
            outPoint=data.get('outPoint', 1.0),
            linkedAudio=data.get('linkedAudio', True),
            thumbnail_path=data.get('thumbnail_path', ""),
            fps=data.get('fps', 24.0)
        )
        ensure_unique_workflow_uids(shot)
        return shot

    def get(self, var, default=""):
        try:
            return self.to_dict().get(var, default)
        except Exception:
            return default
