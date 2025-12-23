"""
ControlNet configuration nodes for the Daydream StreamDiffusion integration.

These nodes produce validated payload fragments that attach to
`PipelineConfigNode` and guarantee compatibility with Daydream's
supported model configurations.

Separate node types are provided for each model family:
- ControlNetSD21Node: For SD-Turbo (SD2.1-based) models
- ControlNetSDXLNode: For SDXL-Turbo models

Each uses a distinct link type (CONTROLNET_CONFIG_SD21, CONTROLNET_CONFIG_SDXL)
to prevent cross-model chaining at the visual level.
"""

import json
from typing import Any, Dict, List, Tuple, Union

from .pipeline_config import CONTROLNET_REGISTRY


# ---------------------------------------------------------------------------
# Build model-family-specific registries
# ---------------------------------------------------------------------------

def _filter_controlnets_by_model(target_model: str) -> Dict[str, Dict[str, Any]]:
    """Filter CONTROLNET_REGISTRY to only include controlnets supporting target_model."""
    return {
        model_id: definition
        for model_id, definition in CONTROLNET_REGISTRY.items()
        if target_model in definition.get("pipelines", {}).get("streamdiffusion", ())
    }


# SD2.1 / SD-Turbo ControlNets
SD21_CONTROLNETS = _filter_controlnets_by_model("stabilityai/sd-turbo")
SD21_MODEL_CHOICES = tuple(SD21_CONTROLNETS.keys())
SD21_PREPROCESSORS = tuple(
    sorted({
        preprocessor
        for definition in SD21_CONTROLNETS.values()
        for preprocessor in definition["preprocessors"]
    })
)
SD21_DEFAULT_MODEL = SD21_MODEL_CHOICES[0] if SD21_MODEL_CHOICES else ""
SD21_DEFAULT_PREPROCESSOR = (
    SD21_CONTROLNETS[SD21_DEFAULT_MODEL]["default_preprocessor"]
    if SD21_DEFAULT_MODEL else ""
)

# SDXL / SDXL-Turbo ControlNets
SDXL_CONTROLNETS = _filter_controlnets_by_model("stabilityai/sdxl-turbo")
SDXL_MODEL_CHOICES = tuple(SDXL_CONTROLNETS.keys())
SDXL_PREPROCESSORS = tuple(
    sorted({
        preprocessor
        for definition in SDXL_CONTROLNETS.values()
        for preprocessor in definition["preprocessors"]
    })
)
SDXL_DEFAULT_MODEL = SDXL_MODEL_CHOICES[0] if SDXL_MODEL_CHOICES else ""
SDXL_DEFAULT_PREPROCESSOR = (
    SDXL_CONTROLNETS[SDXL_DEFAULT_MODEL]["default_preprocessor"]
    if SDXL_DEFAULT_MODEL else ""
)


def _build_preprocessor_defaults() -> Dict[str, Dict[str, Any]]:
    """Build a mapping of preprocessor -> default values from the registry."""
    defaults: Dict[str, Dict[str, Any]] = {}
    for definition in CONTROLNET_REGISTRY.values():
        for preprocessor, values in definition.get("preprocessor_defaults", {}).items():
            defaults[preprocessor] = values
    return defaults


PREPROCESSOR_DEFAULTS = _build_preprocessor_defaults()


def _get_default_scale(preprocessor: str) -> float:
    """Get the default conditioning scale for a preprocessor."""
    return float(
        PREPROCESSOR_DEFAULTS.get(preprocessor, {}).get("conditioning_scale", 0.5)
    )


def _build_scale_hints(preprocessors: tuple) -> str:
    """Build tooltip hints for preprocessor default scales."""
    hints = []
    for preprocessor in preprocessors:
        scale = PREPROCESSOR_DEFAULTS.get(preprocessor, {}).get("conditioning_scale")
        if scale is not None:
            hints.append(f"{preprocessor}={scale}")
    return ", ".join(hints)


# ---------------------------------------------------------------------------
# Link Type Constants
# ---------------------------------------------------------------------------

# Single link type for all ControlNet nodes
# Model compatibility is validated at runtime in PipelineConfig
CONTROLNET_CONFIG = "CONTROLNET_CONFIG"


# ---------------------------------------------------------------------------
# Base ControlNet Node Class
# ---------------------------------------------------------------------------

class ControlNetNodeBase:
    """
    Base class for ControlNet configuration nodes.
    
    Subclasses must define:
    - MODEL_CHOICES: Tuple of valid model_id options
    - PREPROCESSOR_CHOICES: Tuple of valid preprocessor options
    - DEFAULT_MODEL: Default model_id
    - DEFAULT_PREPROCESSOR: Default preprocessor
    - LINK_TYPE: The link type for input/output (prevents cross-model chaining)
    - CATEGORY: Node category for the UI
    """
    
    MODEL_CHOICES: Tuple[str, ...] = ()
    PREPROCESSOR_CHOICES: Tuple[str, ...] = ()
    DEFAULT_MODEL: str = ""
    DEFAULT_PREPROCESSOR: str = ""
    LINK_TYPE: str = CONTROLNET_CONFIG
    
    RETURN_TYPES = (CONTROLNET_CONFIG,)
    RETURN_NAMES = ("controlnets",)
    FUNCTION = "create_controlnet"
    CATEGORY = "Daydream Live/ControlNet"

    @classmethod
    def INPUT_TYPES(cls) -> Dict[str, Any]:
        default_scale = _get_default_scale(cls.DEFAULT_PREPROCESSOR)
        scale_hints = _build_scale_hints(cls.PREPROCESSOR_CHOICES)
        
        return {
            "required": {
                "model_id": (cls.MODEL_CHOICES, {
                    "default": cls.DEFAULT_MODEL,
                    "tooltip": "Select a Daydream-supported ControlNet model",
                }),
                "preprocessor": (cls.PREPROCESSOR_CHOICES, {
                    "default": cls.DEFAULT_PREPROCESSOR,
                    "tooltip": (
                        f"Preprocessor for this ControlNet. Recommended scales: {scale_hints}"
                        if scale_hints else "Preprocessor for this ControlNet"
                    ),
                }),
                "conditioning_scale": ("FLOAT", {
                    "default": default_scale,
                    "min": 0.0,
                    "max": 2.0,
                    "step": 0.05,
                    "display": "number",
                    "tooltip": "Influence strength for this ControlNet",
                }),
            },
            "optional": {
                "control_guidance_start": ("FLOAT", {
                    "default": 0.0,
                    "min": 0.0,
                    "max": 1.0,
                    "step": 0.05,
                    "display": "number",
                    "tooltip": "Normalized timestep to start applying control",
                }),
                "control_guidance_end": ("FLOAT", {
                    "default": 1.0,
                    "min": 0.0,
                    "max": 1.0,
                    "step": 0.05,
                    "display": "number",
                    "tooltip": "Normalized timestep to stop applying control",
                }),
                "preprocessor_params": ("STRING", {
                    "default": "{}",
                    "multiline": True,
                    "placeholder": "{\"low_threshold\": 100}",
                    "tooltip": "Additional preprocessor configuration (JSON)",
                    "label": "Preprocessor Params",
                }),
                "enabled": ("BOOLEAN", {
                    "default": True,
                    "label_on": "Enabled",
                    "label_off": "Disabled",
                    "tooltip": "Toggle this ControlNet on/off without disconnecting",
                }),
                "controlnets": (cls.LINK_TYPE, {
                    "tooltip": "Connect another ControlNet to chain multiple ControlNets together",
                }),
            },
        }

    def create_controlnet(
        self,
        model_id: str,
        preprocessor: str,
        conditioning_scale: float,
        control_guidance_start: float = 0.0,
        control_guidance_end: float = 1.0,
        preprocessor_params: str = "{}",
        enabled: bool = True,
        controlnets: Any = None,
    ) -> Tuple[Any]:
        """
        Emit a validated ControlNet configuration.
        
        If controlnets is provided, this node returns a list containing
        the chained controlnet(s) plus this controlnet. Otherwise, returns
        just this controlnet's config dictionary.
        """
        definition = CONTROLNET_REGISTRY.get(model_id)
        if definition is None:
            raise ValueError(f"Unsupported ControlNet model '{model_id}'")

        if preprocessor not in definition["preprocessors"]:
            raise ValueError(
                f"Preprocessor '{preprocessor}' is not valid for ControlNet '{model_id}'. "
                f"Choose one of: {', '.join(definition['preprocessors'])}"
            )

        if conditioning_scale < 0:
            raise ValueError("conditioning_scale must be non-negative")

        if not 0.0 <= control_guidance_start <= 1.0:
            raise ValueError("control_guidance_start must be within [0, 1]")
        if not 0.0 <= control_guidance_end <= 1.0:
            raise ValueError("control_guidance_end must be within [0, 1]")
        if control_guidance_start > control_guidance_end:
            raise ValueError("control_guidance_start cannot exceed control_guidance_end")

        params_dict: Dict[str, Any]
        try:
            params_dict = json.loads(preprocessor_params) if preprocessor_params else {}
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON for preprocessor_params: {exc}") from exc

        if not isinstance(params_dict, dict):
            raise ValueError("preprocessor_params must decode to a JSON object")

        # Apply default preprocessor params if none provided
        default_params = PREPROCESSOR_DEFAULTS.get(preprocessor, {}).get("preprocessor_params")
        if not params_dict and default_params:
            params_dict = dict(default_params)

        controlnet_config = {
            "enabled": bool(enabled),
            "model_id": model_id,
            "preprocessor": preprocessor,
            "conditioning_scale": float(conditioning_scale),
            "control_guidance_start": float(control_guidance_start),
            "control_guidance_end": float(control_guidance_end),
            "preprocessor_params": params_dict,
        }

        # Handle chaining
        if controlnets is not None:
            if isinstance(controlnets, dict):
                result = [controlnets, controlnet_config]
            elif isinstance(controlnets, list):
                result = controlnets + [controlnet_config]
            else:
                raise ValueError(
                    f"controlnets must be a CONTROLNET_CONFIG (dict or list), "
                    f"got {type(controlnets).__name__}"
                )
            return (result,)
        
        return (controlnet_config,)

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")


# ---------------------------------------------------------------------------
# SD-Turbo (SD2.1) ControlNet Node
# ---------------------------------------------------------------------------

class ControlNetSD21Node(ControlNetNodeBase):
    """
    ControlNet configuration for SD-Turbo (SD2.1-based) models.
    
    Available ControlNets:
    - OpenPose (pose_tensorrt)
    - HED/Soft Edge (soft_edge)
    - Canny (canny)
    - Depth (depth_tensorrt)
    - Color (passthrough)
    
    Shows only SD-Turbo compatible options. Use with stabilityai/sd-turbo model.
    """
    
    MODEL_CHOICES = SD21_MODEL_CHOICES
    PREPROCESSOR_CHOICES = SD21_PREPROCESSORS
    DEFAULT_MODEL = SD21_DEFAULT_MODEL
    DEFAULT_PREPROCESSOR = SD21_DEFAULT_PREPROCESSOR
    CATEGORY = "Daydream Live/ControlNet"


# ---------------------------------------------------------------------------
# SDXL-Turbo ControlNet Node
# ---------------------------------------------------------------------------

class ControlNetSDXLNode(ControlNetNodeBase):
    """
    ControlNet configuration for SDXL-Turbo models.
    
    Available ControlNets:
    - Depth (depth_tensorrt) - High-resolution depth guidance
    - Canny (canny) - SDXL-optimized edge detection
    - Tile (feedback) - Tile-based texture control
    
    Shows only SDXL compatible options. Use with stabilityai/sdxl-turbo model.
    """
    
    MODEL_CHOICES = SDXL_MODEL_CHOICES
    PREPROCESSOR_CHOICES = SDXL_PREPROCESSORS
    DEFAULT_MODEL = SDXL_DEFAULT_MODEL
    DEFAULT_PREPROCESSOR = SDXL_DEFAULT_PREPROCESSOR
    CATEGORY = "Daydream Live/ControlNet"


# ---------------------------------------------------------------------------
# Legacy node for backward compatibility
# ---------------------------------------------------------------------------

# Combined choices for backward-compatible node
ALL_MODEL_CHOICES = tuple(CONTROLNET_REGISTRY.keys())
ALL_PREPROCESSORS = tuple(
    sorted({
        preprocessor
        for definition in CONTROLNET_REGISTRY.values()
        for preprocessor in definition["preprocessors"]
    })
)


class ControlNetNode(ControlNetNodeBase):
    """
    Generic ControlNet configuration (all models).
    
    For cleaner workflows, consider using the model-specific nodes:
    - RTCStreamControlNetSD21: For SD-Turbo (SD2.1) models
    - RTCStreamControlNetSDXL: For SDXL-Turbo models
    
    These show only valid preprocessor options for each model family.
    """
    
    MODEL_CHOICES = ALL_MODEL_CHOICES
    PREPROCESSOR_CHOICES = ALL_PREPROCESSORS
    DEFAULT_MODEL = ALL_MODEL_CHOICES[0] if ALL_MODEL_CHOICES else ""
    DEFAULT_PREPROCESSOR = (
        CONTROLNET_REGISTRY[ALL_MODEL_CHOICES[0]]["default_preprocessor"]
        if ALL_MODEL_CHOICES else ""
    )
    CATEGORY = "Daydream Live/ControlNet"


# ---------------------------------------------------------------------------
# Node Registration
# ---------------------------------------------------------------------------

NODE_CLASS_MAPPINGS = {
    "RTCStreamControlNet": ControlNetNode,
    "RTCStreamControlNetSD21": ControlNetSD21Node,
    "RTCStreamControlNetSDXL": ControlNetSDXLNode,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "RTCStreamControlNet": "Daydream ControlNet (All)",
    "RTCStreamControlNetSD21": "Daydream ControlNet SD-Turbo",
    "RTCStreamControlNetSDXL": "Daydream ControlNet SDXL",
}
