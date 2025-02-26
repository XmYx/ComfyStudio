#!/usr/bin/env python

from dataclasses import dataclass, field
from typing import List, Dict, Any
import cv2

@dataclass
class WorkflowAssignment:
    path: str
    enabled: bool = True
    parameters: Dict[str, Any] = field(default_factory=dict)
    isVideo: bool = False
    lastSignature: str = field(default_factory=str)
    versions: List[Dict[str, Any]] = field(default_factory=list)  # New field for version snapshots
    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "enabled": self.enabled,
            "parameters": self.parameters,
            "isVideo": self.isVideo,
            "lastSignature": self.lastSignature,
            "versions": self.versions  # Include versions when serializing
        }
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'WorkflowAssignment':
        return cls(
            path=data.get('path', ''),
            enabled=data.get('enabled', True),
            parameters=data.get('parameters', {}),
            isVideo=data.get('isVideo', False),
            lastSignature=data.get('lastSignature', ""),
            versions=data.get('versions', [])  # Load versions if present
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
    def duration(self) -> float:
        """
        Computes the shot's duration (in seconds) based on its video file and fps.
        The computed duration is then scaled by (outPoint - inPoint) and cached
        for later use.
        """
        if self._cached_duration is None:
            if self.videoPath:
                cap = cv2.VideoCapture(self.videoPath)
                if cap.isOpened():
                    frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
                    video_fps = cap.get(cv2.CAP_PROP_FPS)
                    # Use the shot's fps if set (and > 0); otherwise, fall back to the video's fps.
                    used_fps = self.fps if self.fps > 0 else video_fps
                    if used_fps > 0:
                        self._cached_duration = frame_count / used_fps
                    else:
                        self._cached_duration = float(self.default_duration)
                else:
                    self._cached_duration = float(self.default_duration)
                cap.release()
            else:
                self._cached_duration = float(self.default_duration)
        # Return the trimmed duration based on the in/out fraction.
        return self._cached_duration * (self.outPoint - self.inPoint)

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
            "duration": self.duration  # computed duration
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Shot':
        workflows_data = data.get('workflows', [])
        workflows = [WorkflowAssignment.from_dict(wf) for wf in workflows_data]
        return cls(
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
            default_duration=data.get('duration', 5),
            inPoint=data.get('inPoint', 0.0),
            outPoint=data.get('outPoint', 1.0),
            linkedAudio=data.get('linkedAudio', True),
            thumbnail_path=data.get('thumbnail_path', ""),
            fps=data.get('fps', 24.0)
        )

    def get(self, var, default=""):
        try:
            return self.to_dict().get(var, default)
        except Exception:
            return default
