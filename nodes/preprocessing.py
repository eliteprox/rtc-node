"""
Pre/Post-processing configuration nodes for Daydream StreamDiffusion pipelines.

These nodes build the `image_preprocessing`, `image_postprocessing`,
`latent_preprocessing`, and `latent_postprocessing` payload blocks described in
the Daydream Create / Update Stream APIs.
"""

import json
from typing import Any, Dict, List, Tuple

PROCESSOR_LIST_TYPE = "PROCESSOR_LIST"
PREPROCESSING_CONFIG_TYPE = "PREPROCESSING_CONFIG"


def _decode_params(params: str) -> Dict[str, Any]:
    """Parse processor params JSON into a dictionary."""
    if not params:
        return {}
    try:
        parsed = json.loads(params)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON for processor params: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("processor params must decode to a JSON object")
    return parsed


def _normalize_processor_entry(entry: Dict[str, Any], index: int) -> Dict[str, Any]:
    """Validate an individual processor entry."""
    if not isinstance(entry, dict):
        raise ValueError(
            f"processor at index {index} must be a dictionary, got {type(entry).__name__}"
        )
    processor_type = entry.get("type")
    if not isinstance(processor_type, str) or not processor_type.strip():
        raise ValueError(f"processor at index {index} is missing a valid 'type'")
    params = entry.get("params", {})
    if isinstance(params, str):
        params = _decode_params(params)
    if not isinstance(params, dict):
        raise ValueError(
            f"processor at index {index} must have params as dict/JSON string"
        )
    return {
        "type": processor_type.strip(),
        "enabled": bool(entry.get("enabled", True)),
        "params": params,
    }


class ProcessingNode:
    """
    Create a single processor definition and optionally chain multiple processors.

    The chaining pattern matches ControlNet nodes: connect the `processors` output of
    one node into the `processors` input of another to build ordered processor lists.
    """

    RETURN_TYPES = (PROCESSOR_LIST_TYPE,)
    RETURN_NAMES = ("processors",)
    FUNCTION = "create_processor_chain"
    CATEGORY = "Daydream Live/Preprocessing"

    PROCESSOR_CHOICES: Tuple[str, ...] = (
        "blur",
        "canny",
        "depth",
        "latent_feedback",
        "gaussian",
        "sobel",
        "custom",
    )

    @classmethod
    def INPUT_TYPES(cls) -> Dict[str, Any]:
        return {
            "required": {
                "processor_type": (cls.PROCESSOR_CHOICES, {
                    "default": cls.PROCESSOR_CHOICES[0],
                    "tooltip": "Processor identifier (matches Daydream 'type' field)",
                }),
            },
            "optional": {
                "processor_enabled": ("BOOLEAN", {
                    "default": True,
                    "label_on": "Enabled",
                    "label_off": "Disabled",
                }),
                "processor_params": ("STRING", {
                    "default": "{}",
                    "multiline": True,
                    "placeholder": "{\"strength\": 0.5}",
                    "tooltip": "Additional processor parameters (JSON)",
                    "label": "Params",
                }),
                "processors": (PROCESSOR_LIST_TYPE, {
                    "tooltip": "Chain additional processors (order preserved)",
                }),
            },
        }

    def create_processor_chain(
        self,
        processor_type: str,
        processor_enabled: bool = True,
        processor_params: str = "{}",
        processors: Any = None,
    ) -> Tuple[Any]:
        params = _decode_params(processor_params)
        processor_entry = {
            "type": processor_type,
            "enabled": bool(processor_enabled),
            "params": params,
        }

        if processors is None:
            return ([processor_entry],)

        if isinstance(processors, dict):
            chain = [processors, processor_entry]
        elif isinstance(processors, list):
            chain = processors + [processor_entry]
        else:
            raise ValueError(
                f"processors must be a PROCESSOR_LIST (dict or list), got {type(processors).__name__}"
            )
        return (chain,)


class ProcessingBlockNode:
    """
    Wrap a processor list into a Daydream-compatible preprocessing block.

    Use this for the `image_preprocessing`, `image_postprocessing`,
    `latent_preprocessing`, or `latent_postprocessing` inputs on PipelineConfig.
    """

    RETURN_TYPES = (PREPROCESSING_CONFIG_TYPE,)
    RETURN_NAMES = ("processing_block",)
    FUNCTION = "create_processing_block"
    CATEGORY = "Daydream Live/Preprocessing"

    @classmethod
    def INPUT_TYPES(cls) -> Dict[str, Any]:
        return {
            "required": {
                "enabled": ("BOOLEAN", {
                    "default": False,
                    "label_on": "Enabled",
                    "label_off": "Disabled",
                    "tooltip": "Toggle preprocessing block on/off",
                }),
            },
            "optional": {
                "processors": (PROCESSOR_LIST_TYPE, {
                    "tooltip": "Connect a processor chain",
                }),
                "processors_json": ("STRING", {
                    "default": "[]",
                    "multiline": True,
                    "placeholder": "[{\"type\": \"blur\", \"enabled\": true, \"params\": {}}]",
                    "tooltip": "Optional raw JSON fallback when not using processor nodes",
                    "label": "Processors JSON",
                }),
            },
        }

    def create_processing_block(
        self,
        enabled: bool,
        processors: Any = None,
        processors_json: str = "[]",
    ) -> Tuple[Dict[str, Any]]:
        processor_list: List[Dict[str, Any]]

        if processors is not None:
            if isinstance(processors, dict):
                processor_list = [processors]
            elif isinstance(processors, list):
                processor_list = processors
            else:
                raise ValueError(
                    f"processors must be a PROCESSOR_LIST (dict or list), got {type(processors).__name__}"
                )
        else:
            processors_json = processors_json.strip() or "[]"
            try:
                parsed = json.loads(processors_json)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid processors JSON: {exc}") from exc
            if parsed is None:
                parsed = []
            if isinstance(parsed, dict):
                processor_list = [parsed]
            elif isinstance(parsed, list):
                processor_list = parsed
            else:
                raise ValueError("processors_json must decode to a list or dict")

        normalized = [
            _normalize_processor_entry(entry, idx)
            for idx, entry in enumerate(processor_list)
        ]

        if enabled and not normalized:
            raise ValueError("At least one processor is required when enabled")

        return ({
            "enabled": bool(enabled),
            "processors": normalized,
        },)


NODE_CLASS_MAPPINGS = {
    "RTCStreamProcessor": ProcessingNode,
    "RTCStreamProcessingBlock": ProcessingBlockNode,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "RTCStreamProcessor": "Daydream Processor",
    "RTCStreamProcessingBlock": "Daydream Processing Block",
}


