"""
ControlNet configuration nodes for the Daydream StreamDiffusion integration.

These nodes produce validated payload fragments that attach to
`PipelineConfigNode` and guarantee compatibility with Daydream's
`stabilityai/sd-turbo` presets.
"""

import json
from typing import Any, Dict, Tuple

from .pipeline_config import CONTROLNET_REGISTRY

CONTROLNET_MODEL_CHOICES = tuple(CONTROLNET_REGISTRY.keys())
ALL_ALLOWED_PREPROCESSORS = tuple(
    sorted(
        {
            preprocessor
            for definition in CONTROLNET_REGISTRY.values()
            for preprocessor in definition["preprocessors"]
        }
    )
)
DEFAULT_CONTROLNET_MODEL = CONTROLNET_MODEL_CHOICES[0]
DEFAULT_PREPROCESSOR = CONTROLNET_REGISTRY[DEFAULT_CONTROLNET_MODEL]["default_preprocessor"]

def _build_preprocessor_defaults() -> Dict[str, Dict[str, Any]]:
    defaults: Dict[str, Dict[str, Any]] = {}
    for definition in CONTROLNET_REGISTRY.values():
        for preprocessor, values in definition.get("preprocessor_defaults", {}).items():
            defaults[preprocessor] = values
    return defaults

PREPROCESSOR_DEFAULTS = _build_preprocessor_defaults()
DEFAULT_PREPROCESSOR_CONDITIONING_SCALE = float(
    PREPROCESSOR_DEFAULTS.get(DEFAULT_PREPROCESSOR, {}).get("conditioning_scale", 0.5)
)
PREPROCESSOR_SCALE_HINTS = ", ".join(
    f"{preprocessor}={values.get('conditioning_scale')}"
    for preprocessor, values in PREPROCESSOR_DEFAULTS.items()
    if values.get("conditioning_scale") is not None
)


class ControlNetNode:
    """
    Configure a single ControlNet attachment for the Daydream pipeline.
    """

    RETURN_TYPES = ("CONTROLNET_CONFIG",)
    RETURN_NAMES = ("controlnet",)
    FUNCTION = "create_controlnet"
    CATEGORY = "Daydream Live/ControlNet"

    @classmethod
    def INPUT_TYPES(cls) -> Dict[str, Any]:
        return {
            "required": {
                "model_id": (CONTROLNET_MODEL_CHOICES, {
                    "default": DEFAULT_CONTROLNET_MODEL,
                    "tooltip": "Select a Daydream-supported ControlNet model",
                }),
                "preprocessor": (ALL_ALLOWED_PREPROCESSORS, {
                    "default": DEFAULT_PREPROCESSOR,
                    "tooltip": (
                        "Preprocessor used for this ControlNet (available choices depend "
                        "on the selected model). Recommended values: "
                        f"{PREPROCESSOR_SCALE_HINTS}"
                    ),
                }),
                "conditioning_scale": ("FLOAT", {
                    "default": DEFAULT_PREPROCESSOR_CONDITIONING_SCALE,
                    "min": 0.0,
                    "max": 2.0,
                    "step": 0.05,
                    "display": "number",
                    "tooltip": (
                        "Influence strength for this ControlNet. Leave the default unchanged "
                        "to use the recommended scale for the selected preprocessor."
                    ),
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
    ) -> Tuple[Dict[str, Any]]:
        """
        Emit a validated ControlNet configuration dictionary.
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

        return (controlnet_config,)

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")


NODE_CLASS_MAPPINGS = {
    "RTCStreamControlNet": ControlNetNode,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "RTCStreamControlNet": "Daydream ControlNet",
}
