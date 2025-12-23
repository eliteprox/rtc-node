"""
Pipeline Configuration Node for ComfyUI.

Generates validated StreamDiffusion payloads that match the Daydream
`POST /v1/streams` schema. The node returns both the Python dict form (used by
downstream nodes) and a JSON preview so users can inspect the exact payload.
"""

import hashlib
import json
import logging
import threading
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests

from rtc_stream.local_api import build_local_api_url

# ---------------------------------------------------------------------------
# Daydream compatibility registry
# ---------------------------------------------------------------------------

# Shared ControlNet metadata. Each entry records the preprocessor(s) allowed
# for that ControlNet as well as the pipeline/model combinations it supports.
CONTROLNET_REGISTRY: Dict[str, Dict[str, Any]] = {
    "thibaud/controlnet-sd21-openpose-diffusers": {
        "label": "SD2.1 OpenPose",
        "preprocessors": ("pose_tensorrt",),
        "default_preprocessor": "pose_tensorrt",
        "preprocessor_defaults": {
            "pose_tensorrt": {
                "conditioning_scale": 0.711,
                "preprocessor_params": {},
            }
        },
        "pipelines": {
            "streamdiffusion": ("stabilityai/sd-turbo",),
        },
    },
    "thibaud/controlnet-sd21-hed-diffusers": {
        "label": "SD2.1 HED",
        "preprocessors": ("soft_edge",),
        "default_preprocessor": "soft_edge",
        "preprocessor_defaults": {
            "soft_edge": {
                "conditioning_scale": 0.2,
                "preprocessor_params": {},
            }
        },
        "pipelines": {
            "streamdiffusion": ("stabilityai/sd-turbo",),
        },
    },
    "thibaud/controlnet-sd21-canny-diffusers": {
        "label": "SD2.1 Canny",
        "preprocessors": ("canny",),
        "default_preprocessor": "canny",
        "preprocessor_defaults": {
            "canny": {
                "conditioning_scale": 0.2,
                "preprocessor_params": {
                    "low_threshold": 100,
                    "high_threshold": 200,
                },
            }
        },
        "pipelines": {
            "streamdiffusion": ("stabilityai/sd-turbo",),
        },
    },
    "thibaud/controlnet-sd21-depth-diffusers": {
        "label": "SD2.1 Depth",
        "preprocessors": ("depth_tensorrt",),
        "default_preprocessor": "depth_tensorrt",
        "preprocessor_defaults": {
            "depth_tensorrt": {
                "conditioning_scale": 0.5,
                "preprocessor_params": {},
            }
        },
        "pipelines": {
            "streamdiffusion": ("stabilityai/sd-turbo",),
        },
    },
    "thibaud/controlnet-sd21-color-diffusers": {
        "label": "SD2.1 Color",
        "preprocessors": ("passthrough",),
        "default_preprocessor": "passthrough",
        "preprocessor_defaults": {
            "passthrough": {
                "conditioning_scale": 0.2,
                "preprocessor_params": {},
            }
        },
        "pipelines": {
            "streamdiffusion": ("stabilityai/sd-turbo",),
        },
    },
    "xinsir/controlnet-depth-sdxl-1.0": {
        "label": "SDXL Depth",
        "preprocessors": ("depth_tensorrt",),
        "default_preprocessor": "depth_tensorrt",
        "preprocessor_defaults": {
            "depth_tensorrt": {
                "conditioning_scale": 0.4,
                "preprocessor_params": {},
            }
        },
        "pipelines": {
            "streamdiffusion": ("stabilityai/sdxl-turbo",),
        },
    },
    "xinsir/controlnet-canny-sdxl-1.0": {
        "label": "SDXL Canny",
        "preprocessors": ("canny",),
        "default_preprocessor": "canny",
        "preprocessor_defaults": {
            "canny": {
                "conditioning_scale": 0.1,
                "preprocessor_params": {},
            }
        },
        "pipelines": {
            "streamdiffusion": ("stabilityai/sdxl-turbo",),
        },
    },
    "xinsir/controlnet-tile-sdxl-1.0": {
        "label": "SDXL Tile",
        "preprocessors": ("feedback",),
        "default_preprocessor": "feedback",
        "preprocessor_defaults": {
            "feedback": {
                "conditioning_scale": 0.1,
                "preprocessor_params": {},
            }
        },
        "pipelines": {
            "streamdiffusion": ("stabilityai/sdxl-turbo",),
        },
    },
}

# Pipeline -> model registry with control net compatibility baked in.
PIPELINE_MODEL_REGISTRY: Dict[str, Dict[str, Any]] = {
    "streamdiffusion": {
        "label": "StreamDiffusion",
        "models": {
            "stabilityai/sd-turbo": {
                "label": "SD Turbo (SD2.1)",
                "default_width": 704,
                "default_height": 704,
                "supported_controlnets": (
                    "thibaud/controlnet-sd21-openpose-diffusers",
                    "thibaud/controlnet-sd21-hed-diffusers",
                    "thibaud/controlnet-sd21-canny-diffusers",
                    "thibaud/controlnet-sd21-depth-diffusers",
                    "thibaud/controlnet-sd21-color-diffusers",
                ),
            },
            "stabilityai/sdxl-turbo": {
                "label": "SDXL Turbo",
                "default_width": 512,
                "default_height": 512,
                "supported_controlnets": (
                    "xinsir/controlnet-depth-sdxl-1.0",
                    "xinsir/controlnet-canny-sdxl-1.0",
                    "xinsir/controlnet-tile-sdxl-1.0",
                ),
            },
        },
    },
}


LOGGER = logging.getLogger("rtc_stream.pipeline_config")


def hash_pipeline_config(pipeline_config: Dict[str, Any]) -> str:
    """
    Compute a stable hash for a pipeline configuration payload.

    The payload is serialized with sorted keys so downstream nodes can
    reliably detect when the configuration truly changes.
    """
    serialized = json.dumps(pipeline_config or {}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _unique(seq: Iterable[str]) -> Tuple[str, ...]:
    """Preserve order while removing duplicates."""
    seen = {}
    for item in seq:
        seen.setdefault(item, None)
    return tuple(seen.keys())


PIPELINE_CHOICES: Tuple[str, ...] = tuple(PIPELINE_MODEL_REGISTRY.keys())
MODEL_CHOICES: Tuple[str, ...] = _unique(
    model_id
    for pipeline in PIPELINE_MODEL_REGISTRY.values()
    for model_id in pipeline["models"].keys()
)
ACCELERATION_OPTIONS: Tuple[str, ...] = ("none", "xformers", "sfast", "tensorrt")
INTERPOLATION_METHODS: Tuple[str, ...] = ("linear", "slerp")
IP_ADAPTER_TYPES: Tuple[str, ...] = ("none", "regular", "faceid")
IP_ADAPTER_WEIGHT_TYPES: Tuple[str, ...] = ("linear", "ease_in", "ease_out", "ease_in_out")


class PipelineConfigNode:
    """
    Pipeline configuration entry-point.

    Validates model/controlnet compatibility, enforces sane parameter types,
    and returns a payload ready for `POST /v1/streams`.
    """

    _CACHE_LOCK = threading.Lock()
    _LAST_DIGEST: Optional[str] = None
    _REQUEST_TIMEOUT = 3.0

    RETURN_TYPES = ("PIPELINE_CONFIG", "STRING", "INT", "INT")
    RETURN_NAMES = ("pipeline_config", "config_json", "width", "height")
    FUNCTION = "create_pipeline_config"
    CATEGORY = "Daydream Live/Config"

    @classmethod
    def INPUT_TYPES(cls) -> Dict[str, Any]:
        return {
            "required": {
                "pipeline": (PIPELINE_CHOICES, {
                    "default": PIPELINE_CHOICES[0],
                    "tooltip": "Daydream pipeline identifier",
                }),
                "model_id": (MODEL_CHOICES, {
                    "default": MODEL_CHOICES[0],
                    "tooltip": "Model to execute within the selected pipeline",
                }),
                "prompt": ("STRING", {
                    "default": "A beautiful landscape",
                    "multiline": True,
                    "placeholder": "Enter your prompt here",
                }),
                "guidance_scale": ("FLOAT", {
                    "default": 7.5,
                    "min": 0.0,
                    "max": 256.0,
                    "step": 0.5,
                    "display": "number",
                }),
                "delta": ("FLOAT", {
                    "default": 1.0,
                    "min": 0.0,
                    "max": 256.0,
                    "step": 0.5,
                    "display": "number",
                    "tooltip": "StreamDiffusion delta parameter",
                }),
                "num_inference_steps": ("INT", {
                    "default": 50,
                    "min": 1,
                    "max": 200,
                    "step": 1,
                    "display": "number",
                }),
                "width": ("INT", {
                    "default": 704,
                    "min": 256,
                    "max": 2048,
                    "step": 8,
                    "display": "number",
                }),
                "height": ("INT", {
                    "default": 704,
                    "min": 256,
                    "max": 2048,
                    "step": 8,
                    "display": "number",
                }),
                "seed": ("INT", {
                    "default": 0,
                    "min": 0,
                    "max": 2**32 - 1,
                    "step": 1,
                    "display": "number",
                }),
            },
            "optional": {
                "negative_prompt": ("STRING", {
                    "default": "",
                    "multiline": True,
                    "placeholder": "Negative prompt (optional)",
                }),
                "prompt_interpolation_method": (INTERPOLATION_METHODS, {
                    "default": "linear",
                }),
                "seed_interpolation_method": (INTERPOLATION_METHODS, {
                    "default": "linear",
                }),
                "normalize_prompt_weights": ("BOOLEAN", {
                    "default": True,
                    "label_on": "Enabled",
                    "label_off": "Disabled",
                }),
                "normalize_seed_weights": ("BOOLEAN", {
                    "default": True,
                    "label_on": "Enabled",
                    "label_off": "Disabled",
                }),
                "use_safety_checker": ("BOOLEAN", {
                    "default": True,
                    "label_on": "Enabled",
                    "label_off": "Disabled",
                }),
                "use_lcm_lora": ("BOOLEAN", {
                    "default": True,
                    "label_on": "Enabled",
                    "label_off": "Disabled",
                }),
                "lcm_lora_id": ("STRING", {
                    "default": "",
                    "multiline": False,
                    "placeholder": "LCM LoRA ID (required when LoRA enabled)",
                }),
                "lora_dict": ("STRING", {
                    "default": "{}",
                    "multiline": True,
                    "placeholder": "{\"example_lora\": 0.5}",
                }),
                "acceleration": (ACCELERATION_OPTIONS, {
                    "default": "none",
                    "tooltip": "Hardware acceleration preference",
                }),
                "use_denoising_batch": ("BOOLEAN", {
                    "default": True,
                    "label_on": "Enabled",
                    "label_off": "Disabled",
                }),
                "do_add_noise": ("BOOLEAN", {
                    "default": True,
                    "label_on": "Enabled",
                    "label_off": "Disabled",
                }),
                "enable_similar_image_filter": ("BOOLEAN", {
                    "default": True,
                    "label_on": "Enabled",
                    "label_off": "Disabled",
                }),
                "similar_image_filter_threshold": ("FLOAT", {
                    "default": 0.5,
                    "min": 0.0,
                    "max": 1.0,
                    "step": 0.01,
                    "display": "number",
                }),
                "similar_image_filter_max_skip_frame": ("INT", {
                    "default": 10,
                    "min": 0,
                    "max": 4503599627370495,
                    "step": 1,
                    "display": "number",
                }),
                "t_index_list": ("STRING", {
                    "default": "0",
                    "multiline": False,
                    "placeholder": "Comma-separated t-indices (e.g. 0,4,8)",
                }),
                # Single controlnets input - model compatibility validated at runtime
                "controlnets": ("CONTROLNET_CONFIG", {
                    "tooltip": "Connect ControlNet chain from any Daydream ControlNet node",
                }),
                "image_preprocessing": ("PREPROCESSING_CONFIG", {
                    "tooltip": "Connect Daydream Processing Block nodes for image pre-processing",
                }),
                "image_postprocessing": ("PREPROCESSING_CONFIG", {
                    "tooltip": "Connect Daydream Processing Block nodes for image post-processing",
                }),
                "latent_preprocessing": ("PREPROCESSING_CONFIG", {
                    "tooltip": "Connect Daydream Processing Block nodes for latent pre-processing",
                }),
                "latent_postprocessing": ("PREPROCESSING_CONFIG", {
                    "tooltip": "Connect Daydream Processing Block nodes for latent post-processing",
                }),
                "enable_ip_adapter": ("BOOLEAN", {
                    "default": False,
                    "label_on": "Enabled",
                    "label_off": "Disabled",
                    "tooltip": "Enable IP-Adapter payload",
                }),
                "ip_adapter_type": (IP_ADAPTER_TYPES, {
                    "default": "none",
                }),
                "ip_adapter_scale": ("FLOAT", {
                    "default": 0.5,
                    "min": 0.0,
                    "max": 2.0,
                    "step": 0.05,
                    "display": "number",
                }),
                "ip_adapter_weight_type": (IP_ADAPTER_WEIGHT_TYPES, {
                    "default": "linear",
                }),
                "ip_adapter_style_image_url": ("STRING", {
                    "default": "",
                    "multiline": False,
                    "placeholder": "Optional style image URL",
                }),
            },
        }

    def create_pipeline_config(
        self,
        pipeline: str,
        model_id: str,
        prompt: str,
        guidance_scale: float,
        delta: float,
        num_inference_steps: int,
        width: int,
        height: int,
        seed: int,
        negative_prompt: str = "",
        prompt_interpolation_method: str = "linear",
        seed_interpolation_method: str = "linear",
        normalize_prompt_weights: bool = True,
        normalize_seed_weights: bool = True,
        use_safety_checker: bool = True,
        use_lcm_lora: bool = True,
        lcm_lora_id: str = "",
        lora_dict: str = "{}",
        acceleration: str = "none",
        use_denoising_batch: bool = True,
        do_add_noise: bool = True,
        enable_similar_image_filter: bool = True,
        similar_image_filter_threshold: float = 0.5,
        similar_image_filter_max_skip_frame: int = 10,
        t_index_list: str = "0",
        controlnets: Any = None,
        image_preprocessing: Any = None,
        image_postprocessing: Any = None,
        latent_preprocessing: Any = None,
        latent_postprocessing: Any = None,
        enable_ip_adapter: bool = False,
        ip_adapter_type: str = "none",
        ip_adapter_scale: float = 0.5,
        ip_adapter_weight_type: str = "linear",
        ip_adapter_style_image_url: str = "",
    ) -> Tuple[Dict[str, Any], str]:
        """
        Build validated Daydream pipeline configuration payload.
        """

        pipeline_id = pipeline.strip()
        model_id = model_id.strip()
        prompt = prompt.strip()
        negative_prompt = negative_prompt.strip()

        self._validate_pipeline_model_combo(pipeline_id, model_id)
        width = self._validate_dimension(width, "width", minimum=256)
        height = self._validate_dimension(height, "height", minimum=256)
        guidance_scale = self._validate_non_negative_float(guidance_scale, "guidance_scale")
        delta = self._validate_non_negative_float(delta, "delta")
        similar_image_filter_threshold = self._validate_fraction(
            similar_image_filter_threshold,
            "similar_image_filter_threshold",
        )
        similar_image_filter_max_skip_frame = int(similar_image_filter_max_skip_frame)
        if similar_image_filter_max_skip_frame < 0:
            raise ValueError("similar_image_filter_max_skip_frame must be >= 0")

        if acceleration not in ACCELERATION_OPTIONS:
            raise ValueError(f"Unsupported acceleration option '{acceleration}'")
        if prompt_interpolation_method not in INTERPOLATION_METHODS:
            raise ValueError(f"Unsupported prompt interpolation method '{prompt_interpolation_method}'")
        if seed_interpolation_method not in INTERPOLATION_METHODS:
            raise ValueError(f"Unsupported seed interpolation method '{seed_interpolation_method}'")

        lora_dict_payload = self._parse_lora_dict(lora_dict)
        t_indices = self._parse_t_indices(t_index_list)
        num_inference_steps = int(num_inference_steps)
        if num_inference_steps <= 0:
            raise ValueError("num_inference_steps must be greater than zero")

        seed_value = int(seed)
        if seed_value < 0:
            raise ValueError("seed must be >= 0")

        # Collect and validate controlnets
        controlnets_list = self._collect_controlnets(
            pipeline_id,
            model_id,
            controlnets,
        )

        params: Dict[str, Any] = {
            "model_id": model_id,
            "prompt": prompt,
            "prompt_interpolation_method": prompt_interpolation_method,
            "normalize_prompt_weights": bool(normalize_prompt_weights),
            "normalize_seed_weights": bool(normalize_seed_weights),
            "negative_prompt": negative_prompt,
            "guidance_scale": float(guidance_scale),
            "delta": float(delta),
            "num_inference_steps": num_inference_steps,
            "t_index_list": t_indices,
            "use_safety_checker": bool(use_safety_checker),
            "width": width,
            "height": height,
            "lora_dict": lora_dict_payload,
            "use_lcm_lora": bool(use_lcm_lora),
            "lcm_lora_id": lcm_lora_id if use_lcm_lora else "",
            "acceleration": acceleration,
            "use_denoising_batch": bool(use_denoising_batch),
            "do_add_noise": bool(do_add_noise),
            "seed": seed_value,
            "seed_interpolation_method": seed_interpolation_method,
            "enable_similar_image_filter": bool(enable_similar_image_filter),
            "similar_image_filter_threshold": float(similar_image_filter_threshold),
            "similar_image_filter_max_skip_frame": similar_image_filter_max_skip_frame,
        }

        if controlnets_list:
            params["controlnets"] = controlnets_list

        processing_blocks = {
            "image_preprocessing": image_preprocessing,
            "image_postprocessing": image_postprocessing,
            "latent_preprocessing": latent_preprocessing,
            "latent_postprocessing": latent_postprocessing,
        }
        for block_name, block_value in processing_blocks.items():
            if block_value is None:
                continue
            normalized_block = self._validate_processing_block(block_value, block_name)
            if normalized_block["enabled"] or normalized_block["processors"]:
                params[block_name] = normalized_block

        if enable_ip_adapter and ip_adapter_type != "none":
            if ip_adapter_type not in IP_ADAPTER_TYPES:
                raise ValueError(f"Unsupported IP adapter type '{ip_adapter_type}'")
            if ip_adapter_weight_type not in IP_ADAPTER_WEIGHT_TYPES:
                raise ValueError(f"Unsupported IP adapter weight type '{ip_adapter_weight_type}'")
            params["ip_adapter"] = {
                "enabled": True,
                "type": ip_adapter_type,
                "scale": float(ip_adapter_scale),
                "weight_type": ip_adapter_weight_type,
            }
            if ip_adapter_style_image_url:
                params["ip_adapter_style_image_url"] = ip_adapter_style_image_url

        payload = {
            "pipeline": pipeline_id,
            "params": params,
        }
        config_json = json.dumps(payload, indent=2)
        self._cache_and_notify(payload)
        return payload, config_json, width, height

    @staticmethod
    def _validate_pipeline_model_combo(pipeline: str, model_id: str) -> Dict[str, Any]:
        try:
            pipeline_info = PIPELINE_MODEL_REGISTRY[pipeline]
        except KeyError as exc:
            raise ValueError(f"Unsupported pipeline '{pipeline}'") from exc

        try:
            return pipeline_info["models"][model_id]
        except KeyError as exc:
            raise ValueError(
                f"Model '{model_id}' is not available for pipeline '{pipeline}'",
            ) from exc

    @staticmethod
    def _validate_dimension(value: int, name: str, *, minimum: int) -> int:
        if value < minimum:
            raise ValueError(f"{name.capitalize()} must be >= {minimum}")
        if value % 8 != 0:
            raise ValueError(f"{name.capitalize()} must be divisible by 8")
        return int(value)

    @staticmethod
    def _validate_non_negative_float(value: float, name: str) -> float:
        if value < 0:
            raise ValueError(f"{name} must be non-negative")
        return float(value)

    @staticmethod
    def _validate_fraction(value: float, name: str) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"{name} must be between 0.0 and 1.0 inclusive")
        return float(value)

    @staticmethod
    def _parse_lora_dict(value: Any) -> Dict[str, Any]:
        if isinstance(value, dict):
            return dict(value)

        if not value:
            return {}

        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return {}
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid LoRA dictionary JSON: {exc}") from exc
            if not isinstance(parsed, dict):
                raise ValueError("LoRA configuration must decode to a JSON object")
            return parsed

        raise ValueError("LoRA configuration must be a dict or JSON string")

    @staticmethod
    def _parse_t_indices(value: Any) -> List[int]:
        if isinstance(value, str):
            tokens: Iterable[Any] = value.split(",")
        elif isinstance(value, Iterable):
            tokens = value
        else:
            raise ValueError("t_index_list must be a string or iterable")

        indices: List[int] = []
        for token in tokens:
            if isinstance(token, int):
                indices.append(token)
                continue
            token_str = str(token).strip()
            if not token_str:
                continue
            try:
                indices.append(int(token_str))
            except ValueError as exc:
                raise ValueError(f"Invalid t_index value '{token}'") from exc

        if not indices:
            indices.append(0)
        return indices

    @classmethod
    def _collect_controlnets(
        cls,
        pipeline: str,
        model_id: str,
        controlnets_input: Any,
    ) -> List[Dict[str, Any]]:
        """
        Collect and validate ControlNet configurations.
        
        Args:
            pipeline: Pipeline identifier
            model_id: Model identifier
            controlnets_input: Can be:
                - None: no controlnets
                - dict: single controlnet config
                - list: multiple controlnet configs (from chaining)
        
        Returns:
            List of validated controlnet configurations
        """
        if controlnets_input is None:
            return []
        
        # Normalize input to a list
        if isinstance(controlnets_input, dict):
            controlnet_list = [controlnets_input]
        elif isinstance(controlnets_input, list):
            controlnet_list = controlnets_input
        else:
            raise ValueError(
                f"controlnets must be a dict or list of dicts, "
                f"got {type(controlnets_input).__name__}"
            )
        
        # Validate each controlnet
        validated_controlnets: List[Dict[str, Any]] = []
        for idx, controlnet in enumerate(controlnet_list):
            if not isinstance(controlnet, dict):
                raise ValueError(
                    f"controlnet at index {idx} must be a dictionary, "
                    f"got {type(controlnet).__name__}"
                )
            slot_name = f"controlnet_{idx + 1}"
            normalized = cls._validate_controlnet(pipeline, model_id, controlnet, slot_name)
            validated_controlnets.append(normalized)
        
        return validated_controlnets

    @staticmethod
    def _validate_controlnet(
        pipeline: str,
        model_id: str,
        controlnet: Dict[str, Any],
        slot_name: str,
    ) -> Dict[str, Any]:
        controlnet_model_id = controlnet.get("model_id")
        if not isinstance(controlnet_model_id, str):
            raise ValueError(f"{slot_name} is missing a valid 'model_id'")

        try:
            definition = CONTROLNET_REGISTRY[controlnet_model_id]
        except KeyError as exc:
            raise ValueError(f"{slot_name} has unsupported ControlNet '{controlnet_model_id}'") from exc

        supported_models = definition["pipelines"].get(pipeline, ())
        if model_id not in supported_models:
            raise ValueError(
                f"ControlNet '{controlnet_model_id}' is not supported for pipeline '{pipeline}' and model '{model_id}'",
            )

        preprocessor = controlnet.get("preprocessor") or definition["default_preprocessor"]
        if preprocessor not in definition["preprocessors"]:
            raise ValueError(
                f"{slot_name} preprocessor '{preprocessor}' is not valid for ControlNet '{controlnet_model_id}'",
            )

        conditioning_scale = float(controlnet.get("conditioning_scale", 0.0))
        if conditioning_scale < 0:
            raise ValueError(f"{slot_name} conditioning_scale must be non-negative")

        control_guidance_start = float(controlnet.get("control_guidance_start", 0.0))
        control_guidance_end = float(controlnet.get("control_guidance_end", 1.0))
        if not 0.0 <= control_guidance_start <= 1.0:
            raise ValueError(f"{slot_name} control_guidance_start must be between 0 and 1")
        if not 0.0 <= control_guidance_end <= 1.0:
            raise ValueError(f"{slot_name} control_guidance_end must be between 0 and 1")
        if control_guidance_start > control_guidance_end:
            raise ValueError(f"{slot_name} control_guidance_start cannot exceed control_guidance_end")

        params = controlnet.get("preprocessor_params", {})
        if isinstance(params, str):
            params = params.strip()
            if params:
                try:
                    params = json.loads(params)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{slot_name} preprocessor_params contains invalid JSON") from exc
            else:
                params = {}
        if not isinstance(params, dict):
            raise ValueError(f"{slot_name} preprocessor_params must be a JSON object")

        normalized = {
            "enabled": bool(controlnet.get("enabled", True)),
            "model_id": controlnet_model_id,
            "preprocessor": preprocessor,
            "conditioning_scale": conditioning_scale,
            "control_guidance_start": control_guidance_start,
            "control_guidance_end": control_guidance_end,
            "preprocessor_params": params,
        }
        return normalized

    @staticmethod
    def _validate_processing_block(
        block: Any,
        slot_name: str,
    ) -> Dict[str, Any]:
        if not isinstance(block, dict):
            raise ValueError(f"{slot_name} must be a PREPROCESSING_CONFIG dictionary")

        enabled = bool(block.get("enabled", False))
        processors = block.get("processors", [])
        if processors is None:
            processors = []

        if isinstance(processors, dict):
            processors = [processors]
        elif isinstance(processors, list):
            processors = processors
        else:
            raise ValueError(
                f"{slot_name} processors must be a list or dict, "
                f"got {type(processors).__name__}"
            )

        normalized_processors: List[Dict[str, Any]] = []
        for idx, processor in enumerate(processors):
            normalized_processors.append(
                PipelineConfigNode._normalize_processor_entry(processor, slot_name, idx)
            )

        if enabled and not normalized_processors:
            raise ValueError(f"{slot_name} requires at least one processor when enabled")

        return {
            "enabled": enabled,
            "processors": normalized_processors,
        }

    @staticmethod
    def _normalize_processor_entry(
        processor: Dict[str, Any],
        slot_name: str,
        index: int,
    ) -> Dict[str, Any]:
        if not isinstance(processor, dict):
            raise ValueError(
                f"{slot_name} processor at index {index} must be a dictionary, "
                f"got {type(processor).__name__}"
            )

        processor_type = processor.get("type")
        if not isinstance(processor_type, str) or not processor_type.strip():
            raise ValueError(
                f"{slot_name} processor at index {index} is missing a valid 'type'"
            )

        params = processor.get("params", {})
        if isinstance(params, str):
            params = params.strip()
            if params:
                try:
                    params = json.loads(params)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"{slot_name} processor at index {index} has invalid params JSON"
                    ) from exc
            else:
                params = {}
        if not isinstance(params, dict):
            raise ValueError(
                f"{slot_name} processor at index {index} must have params as dict/JSON"
            )

        return {
            "type": processor_type.strip(),
            "enabled": bool(processor.get("enabled", True)),
            "params": params,
        }

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")

    @classmethod
    def _compute_payload_digest(cls, payload: Dict[str, Any]) -> str:
        return hash_pipeline_config(payload)

    @classmethod
    def _mark_if_changed(cls, digest: str) -> bool:
        with cls._CACHE_LOCK:
            if cls._LAST_DIGEST == digest:
                return False
            cls._LAST_DIGEST = digest
            return True

    @classmethod
    def _cache_and_notify(cls, payload: Dict[str, Any]) -> None:
        digest = cls._compute_payload_digest(payload)
        if not cls._mark_if_changed(digest):
            return
        try:
            url = build_local_api_url("/pipeline/cache")
            response = requests.post(
                url,
                json={"pipeline_config": payload},
                timeout=cls._REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            LOGGER.info(
                "Updated local RTC pipeline config (pipeline=%s)",
                payload.get("pipeline", ""),
            )
        except Exception as exc:  # pragma: no cover - runtime interactions
            LOGGER.warning("Failed to update local RTC pipeline config: %s", exc)


NODE_CLASS_MAPPINGS = {
    "RTCStreamPipelineConfig": PipelineConfigNode,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "RTCStreamPipelineConfig": "Daydream Pipeline Config",
}
