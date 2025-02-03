#!/usr/bin/env python

from dataclasses import dataclass, field
from typing import List, Dict, Any


@dataclass
class WorkflowAssignment:
    path: str
    enabled: bool = True
    parameters: Dict[str, Any] = field(default_factory=dict)
    isVideo: bool = False
    lastSignature: str = field(default_factory=str)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "enabled": self.enabled,
            "parameters": self.parameters,
            "isVideo": self.isVideo,
            "lastSignature": self.lastSignature
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'WorkflowAssignment':
        return cls(
            path=data.get('path', ''),
            enabled=data.get('enabled', True),
            parameters=data.get('parameters', {}),
            isVideo=data.get('isVideo', False),
            lastSignature=data.get('lastSignature', "")
        )


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
    duration: int = 5
    inPoint: float = 0.0  # fraction (0.0-1.0) for trimmed start
    outPoint: float = 1.0  # fraction (0.0-1.0) for trimmed end
    linkedAudio: bool = True  # whether the clip has a linked audio clip
    thumbnail_path: str = ""

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
            "duration": self.duration
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
            duration=data.get('duration', 5)
        )

    def get(self, var, default=""):
        try:
            return self.to_dict().get(var)
        except:
            return default
