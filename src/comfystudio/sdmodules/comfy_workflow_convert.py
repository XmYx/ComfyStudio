#!/usr/bin/env python
"""
Convert a ComfyUI **UI-format** (litegraph) workflow graph into the **API/prompt
format** ComfyStudio renders with.

ComfyUI's own workflow browser saves graphs in UI format (a top-level
``{"nodes": [...], "links": [...]}`` structure), while ComfyStudio submits the
node-id-keyed API/prompt format. To let the studio list and render the exact
workflows the running ComfyUI has, we convert on the fly.

Only the **modern** frontend UI format is supported: the one where each node's
``inputs`` array carries a ``widget`` descriptor for its widget inputs, so the
widget names *and* their positional order are embedded in the file. That makes
the conversion exact and offline (no ``/object_info`` needed). Older exports
that store bare ``widgets_values`` with no such mapping cannot be converted
reliably offline — ``can_convert_ui_workflow`` returns False for them and the
caller keeps asking the user to re-save from a recent ComfyUI / export as API.
"""

from typing import Any, Dict


# Widget inputs that exist only in the frontend and are NOT real node inputs —
# they must never appear in the API prompt (matched by input name / type).
_SKIP_WIDGET_NAMES = {"control_after_generate", "control_before_generate"}
_SKIP_WIDGET_TYPES = {"IMAGEUPLOAD", "BUTTON"}

# litegraph node modes that mean "don't execute" (muted / bypassed).
_INACTIVE_MODES = {2, 4}

# Pure-frontend node types that never belong in an API prompt.
_FRONTEND_ONLY_TYPES = {"Note", "MarkdownNote"}


def is_ui_workflow(data: Any) -> bool:
    """True when ``data`` is a UI-format graph (has a top-level node list)."""
    return isinstance(data, dict) and isinstance(data.get("nodes"), list)


def _link_source_map(data: Dict[str, Any]) -> Dict[Any, tuple]:
    """
    Map link id -> (source_node_id, source_slot).

    Links come as either lists ``[id, from_node, from_slot, to_node, to_slot,
    type]`` or dicts ``{"id", "origin_id", "origin_slot", ...}`` depending on the
    frontend version.
    """
    result: Dict[Any, tuple] = {}
    for link in data.get("links", []) or []:
        if isinstance(link, (list, tuple)) and len(link) >= 3:
            result[link[0]] = (link[1], link[2])
        elif isinstance(link, dict) and "id" in link:
            result[link["id"]] = (link.get("origin_id"), link.get("origin_slot", 0))
    return result


def _has_widget_annotations(data: Dict[str, Any]) -> bool:
    """
    True when every node that carries widget values also names those widgets in
    its ``inputs`` array (the modern format we can convert offline).
    """
    for node in data.get("nodes", []):
        if not isinstance(node, dict):
            continue
        if node.get("type") in _FRONTEND_ONLY_TYPES:
            continue
        wv = node.get("widgets_values")
        if not wv:
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, list):
            return False
        if not any(isinstance(i, dict) and i.get("widget") for i in inputs):
            return False
    return True


def can_convert_ui_workflow(data: Any) -> bool:
    """True when ``data`` is a modern, widget-annotated UI-format graph."""
    return is_ui_workflow(data) and _has_widget_annotations(data)


def ui_workflow_to_api(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert a modern UI-format graph to API/prompt format.

    Raises ValueError if ``data`` isn't a convertible UI graph.
    """
    if not can_convert_ui_workflow(data):
        raise ValueError("Workflow is not in a convertible UI format.")

    links = _link_source_map(data)
    api: Dict[str, Any] = {}

    for node in data.get("nodes", []):
        if not isinstance(node, dict):
            continue
        node_id = node.get("id")
        class_type = node.get("type")
        if node_id is None or not class_type:
            continue
        if class_type in _FRONTEND_ONLY_TYPES:
            continue
        if node.get("mode") in _INACTIVE_MODES:
            continue

        wv = node.get("widgets_values")
        wv_is_list = isinstance(wv, list)
        widget_idx = 0
        inputs_out: Dict[str, Any] = {}

        for inp in node.get("inputs", []) or []:
            if not isinstance(inp, dict):
                continue
            name = inp.get("name")
            link_id = inp.get("link")
            is_widget = bool(inp.get("widget"))

            if link_id is not None and link_id in links:
                src_node, src_slot = links[link_id]
                if src_node is not None:
                    inputs_out[name] = [str(src_node), src_slot]
                continue

            if is_widget:
                # Widget values are positional; advance the index for EVERY
                # widget input (including skipped control/button widgets) so the
                # remaining values stay aligned.
                value = None
                if wv_is_list:
                    if widget_idx < len(wv):
                        value = wv[widget_idx]
                    widget_idx += 1
                elif isinstance(wv, dict):
                    value = wv.get(name)
                itype = str(inp.get("type", "")).upper()
                if name in _SKIP_WIDGET_NAMES or itype in _SKIP_WIDGET_TYPES:
                    continue
                if name:
                    inputs_out[name] = value
            # An unconnected, non-widget input is an optional link left empty —
            # omit it from the prompt.

        api[str(node_id)] = {"class_type": class_type, "inputs": inputs_out}

    return api
