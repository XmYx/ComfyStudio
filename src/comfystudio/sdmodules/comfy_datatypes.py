#!/usr/bin/env python
"""
ComfyUI-style datatypes for the shot workflow graph.

Port types follow ComfyUI conventions (MODEL/CLIP/VAE/CONDITIONING/LATENT/
IMAGE/MASK/... plus the widget primitives INT/FLOAT/BOOLEAN/STRING), with two
ComfyStudio additions: VIDEO (a rendered video file) and "*" (unknown/any,
litegraph's wildcard). API-format workflow JSON never declares output types,
so they are inferred offline: from the name of the input a link feeds
(a slot wired into "samples" is LATENT, "model" is MODEL, ...), else from the
producing node's class name, else "*".
"""

# Link colours as used by the ComfyUI frontend (litegraph link_type_colors).
COMFY_TYPE_COLORS = {
    "MODEL": "#B39DDB",
    "CLIP": "#FFD500",
    "CLIP_VISION": "#A8DADC",
    "CLIP_VISION_OUTPUT": "#AD7452",
    "CONDITIONING": "#FFA931",
    "CONTROL_NET": "#6EE7B7",
    "IMAGE": "#64B5F6",
    "LATENT": "#FF9CF9",
    "MASK": "#81C784",
    "VAE": "#FF6E6E",
    "STYLE_MODEL": "#C2FFAE",
    "NOISE": "#B0B0B0",
    "GUIDER": "#66FFFF",
    "SAMPLER": "#ECB4B4",
    "SIGMAS": "#CDFFCD",
    # ComfyStudio-specific rendered-media types.
    "VIDEO": "#4DD0E1",
    "AUDIO": "#F48FB1",
    # Widget primitives are grey in ComfyUI.
    "INT": "#A9A9A9",
    "FLOAT": "#A9A9A9",
    "BOOLEAN": "#A9A9A9",
    "STRING": "#A9A9A9",
}
DEFAULT_TYPE_COLOR = "#9A9A9A"  # unknown / "*"

WILDCARD = "*"

# The rendered-media types a workflow's Result port can carry. These are the
# choices offered when a user manually overrides a workflow's output type
# (auto-detection is the empty string "").
RESULT_MEDIA_TYPES = ("IMAGE", "VIDEO", "AUDIO", "STRING")

# (stored value, human label) pairs for the per-workflow output-type selector.
# "" means "Auto" — detect from the actual rendered file.
RESULT_TYPE_CHOICES = (
    ("", "Auto"),
    ("IMAGE", "Image"),
    ("VIDEO", "Video"),
    ("AUDIO", "Audio"),
    ("STRING", "Text"),
)

# File extensions -> rendered-media type, for auto-detecting what a render
# actually produced.
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tiff", ".tif"}
_VIDEO_EXTS = {".mp4", ".webm", ".mov", ".avi", ".mkv", ".m4v", ".gifv"}
_AUDIO_EXTS = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac", ".opus"}
# Animated GIFs are emitted by video nodes (VHS etc.); treat as VIDEO when the
# producing node says so, but a bare .gif file defaults to IMAGE below.


def detect_media_type(path, default="IMAGE") -> str:
    """Best-effort rendered-media type from a file path's extension."""
    if not path:
        return default
    ext = ("." + path.rsplit(".", 1)[-1].lower()) if "." in path else ""
    if ext in _VIDEO_EXTS:
        return "VIDEO"
    if ext in _AUDIO_EXTS:
        return "AUDIO"
    if ext in _IMAGE_EXTS:
        return "IMAGE"
    return default

# Param widget type -> canonical port type.
_PARAM_TYPE_MAP = {
    "int": "INT",
    "float": "FLOAT",
    "bool": "BOOLEAN",
    "string": "STRING",
}

# Consumer input name -> the type ComfyUI conventionally wires into it.
_INPUT_NAME_TYPES = {
    "model": "MODEL",
    "clip": "CLIP",
    "vae": "VAE",
    "conditioning": "CONDITIONING",
    "positive": "CONDITIONING",
    "negative": "CONDITIONING",
    "samples": "LATENT",
    "latent": "LATENT",
    "latent_image": "LATENT",
    "image": "IMAGE",
    "images": "IMAGE",
    "pixels": "IMAGE",
    "mask": "MASK",
    "control_net": "CONTROL_NET",
    "clip_vision": "CLIP_VISION",
    "guider": "GUIDER",
    "sampler": "SAMPLER",
    "sigmas": "SIGMAS",
    "noise": "NOISE",
}


def color_for_type(ptype) -> str:
    return COMFY_TYPE_COLORS.get((ptype or WILDCARD).upper(), DEFAULT_TYPE_COLOR)


def param_port_type(param) -> str:
    """Canonical port type of a workflow parameter (widget) port."""
    return _PARAM_TYPE_MAP.get(str(param.get("type", "string")).lower(), "STRING")


def result_port_type(workflow) -> str:
    """
    Type of a workflow's implicit Result port. Priority:
      1. an explicit manual override (workflow.outputType),
      2. the type auto-detected from the last render (workflow.lastOutputType),
      3. the legacy isVideo hint (before the first run).
    """
    override = (getattr(workflow, "outputType", "") or "").upper()
    if override:
        return override
    detected = (getattr(workflow, "lastOutputType", "") or "").upper()
    if detected:
        return detected
    if getattr(workflow, "lastOutput", ""):
        return "VIDEO" if getattr(workflow, "lastOutputIsVideo", False) else "IMAGE"
    return "VIDEO" if getattr(workflow, "isVideo", False) else "IMAGE"


def infer_node_output_type(workflow_json, node_id, slot) -> str:
    """Best-effort type of a node's output slot in an API-format workflow."""
    node_id = str(node_id)
    # 1) The name of an input this slot feeds is the strongest signal.
    for node_data in workflow_json.values():
        if not isinstance(node_data, dict):
            continue
        inputs = node_data.get("inputs", {})
        if not isinstance(inputs, dict):
            continue
        for input_name, value in inputs.items():
            if (isinstance(value, (list, tuple)) and len(value) == 2
                    and str(value[0]) == node_id and value[1] == slot):
                inferred = _INPUT_NAME_TYPES.get(str(input_name).lower())
                if inferred:
                    return inferred
    # 2) Unreferenced slots (terminal output nodes): guess from the class name.
    node_data = workflow_json.get(node_id, {})
    class_type = str(node_data.get("class_type", "")).lower() if isinstance(node_data, dict) else ""
    if any(k in class_type for k in ("video", "vhs", "gif")):
        return "VIDEO"
    if "image" in class_type:
        return "IMAGE"
    return WILDCARD


def types_compatible(src_type, dst_type) -> bool:
    """
    Connection rule: equal types match, "*" matches anything, STRING inputs
    additionally accept rendered files (IMAGE/VIDEO/AUDIO — chaining passes file
    paths as strings), and FLOAT inputs accept INT.
    """
    s = (src_type or WILDCARD).upper()
    d = (dst_type or WILDCARD).upper()
    if s == WILDCARD or d == WILDCARD:
        return True
    if s == d:
        return True
    if d == "STRING" and s in ("IMAGE", "VIDEO", "AUDIO"):
        return True
    if d == "FLOAT" and s == "INT":
        return True
    return False
