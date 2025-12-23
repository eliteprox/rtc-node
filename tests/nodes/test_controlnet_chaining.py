"""
Tests for ControlNet chaining functionality.
"""

import pytest
from nodes.controlnet import (
    ControlNetNode,
    ControlNetSD21Node,
    ControlNetSDXLNode,
    SD21_MODEL_CHOICES,
    SD21_PREPROCESSORS,
    SDXL_MODEL_CHOICES,
    SDXL_PREPROCESSORS,
    CONTROLNET_CONFIG,
)
from nodes.preprocessing import (
    ProcessingBlockNode,
    ProcessingNode,
)
from nodes.pipeline_config import PipelineConfigNode


class TestControlNetChaining:
    """Test ControlNet chaining functionality."""

    def test_single_controlnet_returns_dict(self):
        """Test that a single ControlNet returns a dict (backward compatible)."""
        node = ControlNetNode()
        
        result = node.create_controlnet(
            model_id="thibaud/controlnet-sd21-openpose-diffusers",
            preprocessor="pose_tensorrt",
            conditioning_scale=0.7,
        )
        
        # Should return a tuple with one element
        assert isinstance(result, tuple)
        assert len(result) == 1
        
        # The element should be a dict
        config = result[0]
        assert isinstance(config, dict)
        assert config["model_id"] == "thibaud/controlnet-sd21-openpose-diffusers"
        assert config["preprocessor"] == "pose_tensorrt"
        assert config["conditioning_scale"] == 0.7
        assert config["enabled"] is True

    def test_chained_controlnets_returns_list(self):
        """Test that chaining two ControlNets returns a list."""
        node1 = ControlNetNode()
        node2 = ControlNetNode()
        
        # Create first controlnet
        result1 = node1.create_controlnet(
            model_id="thibaud/controlnet-sd21-openpose-diffusers",
            preprocessor="pose_tensorrt",
            conditioning_scale=0.7,
        )
        
        # Chain second controlnet to first
        result2 = node2.create_controlnet(
            model_id="thibaud/controlnet-sd21-canny-diffusers",
            preprocessor="canny",
            conditioning_scale=0.5,
            controlnets=result1[0],  # Pass the output of first
        )
        
        # Should return a tuple with one element
        assert isinstance(result2, tuple)
        assert len(result2) == 1
        
        # The element should be a list with 2 controlnets
        config_list = result2[0]
        assert isinstance(config_list, list)
        assert len(config_list) == 2
        
        # Verify first controlnet in chain
        assert config_list[0]["model_id"] == "thibaud/controlnet-sd21-openpose-diffusers"
        assert config_list[0]["preprocessor"] == "pose_tensorrt"
        
        # Verify second controlnet in chain
        assert config_list[1]["model_id"] == "thibaud/controlnet-sd21-canny-diffusers"
        assert config_list[1]["preprocessor"] == "canny"

    def test_multiple_chained_controlnets(self):
        """Test chaining three ControlNets together."""
        node1 = ControlNetNode()
        node2 = ControlNetNode()
        node3 = ControlNetNode()
        
        # Create first controlnet
        result1 = node1.create_controlnet(
            model_id="thibaud/controlnet-sd21-openpose-diffusers",
            preprocessor="pose_tensorrt",
            conditioning_scale=0.7,
        )
        
        # Chain second to first
        result2 = node2.create_controlnet(
            model_id="thibaud/controlnet-sd21-canny-diffusers",
            preprocessor="canny",
            conditioning_scale=0.5,
            controlnets=result1[0],
        )
        
        # Chain third to second (which already contains first)
        result3 = node3.create_controlnet(
            model_id="thibaud/controlnet-sd21-depth-diffusers",
            preprocessor="depth_tensorrt",
            conditioning_scale=0.4,
            controlnets=result2[0],
        )
        
        # Should have all 3 controlnets
        config_list = result3[0]
        assert isinstance(config_list, list)
        assert len(config_list) == 3
        
        # Verify order is maintained
        assert config_list[0]["model_id"] == "thibaud/controlnet-sd21-openpose-diffusers"
        assert config_list[1]["model_id"] == "thibaud/controlnet-sd21-canny-diffusers"
        assert config_list[2]["model_id"] == "thibaud/controlnet-sd21-depth-diffusers"


class TestPipelineConfigWithControlNets:
    """Test PipelineConfig with new controlnets input."""

    def test_pipeline_config_no_controlnets(self):
        """Test PipelineConfig with no controlnets."""
        node = PipelineConfigNode()
        
        result = node.create_pipeline_config(
            pipeline="streamdiffusion",
            model_id="stabilityai/sd-turbo",
            prompt="test prompt",
            guidance_scale=7.5,
            delta=1.0,
            num_inference_steps=50,
            width=512,
            height=512,
            seed=42,
        )
        
        config, json_str, width, height = result
        
        # Should not have controlnets in params
        assert "controlnets" not in config["params"]

    def test_pipeline_config_single_controlnet_dict(self):
        """Test PipelineConfig with a single controlnet dict."""
        cn_node = ControlNetNode()
        pipeline_node = PipelineConfigNode()
        
        # Create single controlnet
        cn_result = cn_node.create_controlnet(
            model_id="thibaud/controlnet-sd21-openpose-diffusers",
            preprocessor="pose_tensorrt",
            conditioning_scale=0.7,
        )
        
        # Pass to pipeline config
        result = pipeline_node.create_pipeline_config(
            pipeline="streamdiffusion",
            model_id="stabilityai/sd-turbo",
            prompt="test prompt",
            guidance_scale=7.5,
            delta=1.0,
            num_inference_steps=50,
            width=512,
            height=512,
            seed=42,
            controlnets=cn_result[0],  # Single dict
        )
        
        config, json_str, width, height = result
        
        # Should have controlnets list with 1 item
        assert "controlnets" in config["params"]
        assert isinstance(config["params"]["controlnets"], list)
        assert len(config["params"]["controlnets"]) == 1
        assert config["params"]["controlnets"][0]["model_id"] == "thibaud/controlnet-sd21-openpose-diffusers"

    def test_pipeline_config_chained_controlnets(self):
        """Test PipelineConfig with chained controlnets."""
        cn_node1 = ControlNetNode()
        cn_node2 = ControlNetNode()
        cn_node3 = ControlNetNode()
        pipeline_node = PipelineConfigNode()
        
        # Create chain of 3 controlnets
        cn1 = cn_node1.create_controlnet(
            model_id="thibaud/controlnet-sd21-openpose-diffusers",
            preprocessor="pose_tensorrt",
            conditioning_scale=0.7,
        )
        
        cn2 = cn_node2.create_controlnet(
            model_id="thibaud/controlnet-sd21-canny-diffusers",
            preprocessor="canny",
            conditioning_scale=0.5,
            controlnets=cn1[0],
        )
        
        cn3 = cn_node3.create_controlnet(
            model_id="thibaud/controlnet-sd21-depth-diffusers",
            preprocessor="depth_tensorrt",
            conditioning_scale=0.4,
            controlnets=cn2[0],
        )
        
        # Pass chained controlnets to pipeline config
        result = pipeline_node.create_pipeline_config(
            pipeline="streamdiffusion",
            model_id="stabilityai/sd-turbo",
            prompt="test prompt",
            guidance_scale=7.5,
            delta=1.0,
            num_inference_steps=50,
            width=512,
            height=512,
            seed=42,
            controlnets=cn3[0],  # List of 3
        )
        
        config, json_str, width, height = result
        
        # Should have controlnets list with 3 items
        assert "controlnets" in config["params"]
        assert isinstance(config["params"]["controlnets"], list)
        assert len(config["params"]["controlnets"]) == 3
        
        # Verify order
        assert config["params"]["controlnets"][0]["model_id"] == "thibaud/controlnet-sd21-openpose-diffusers"
        assert config["params"]["controlnets"][1]["model_id"] == "thibaud/controlnet-sd21-canny-diffusers"
        assert config["params"]["controlnets"][2]["model_id"] == "thibaud/controlnet-sd21-depth-diffusers"

    def test_pipeline_config_validates_more_than_5_controlnets(self):
        """Test that PipelineConfig can handle more than 5 controlnets (old limit)."""
        pipeline_node = PipelineConfigNode()
        
        # Create a chain of 6 controlnets manually
        controlnets_list = []
        models = [
            "thibaud/controlnet-sd21-openpose-diffusers",
            "thibaud/controlnet-sd21-canny-diffusers",
            "thibaud/controlnet-sd21-depth-diffusers",
            "thibaud/controlnet-sd21-hed-diffusers",
            "thibaud/controlnet-sd21-color-diffusers",
            "thibaud/controlnet-sd21-openpose-diffusers",  # 6th one
        ]
        
        for model in models:
            cn_node = ControlNetNode()
            result = cn_node.create_controlnet(
                model_id=model,
                preprocessor=("pose_tensorrt" if "openpose" in model 
                             else "canny" if "canny" in model
                             else "depth_tensorrt" if "depth" in model
                             else "soft_edge" if "hed" in model
                             else "passthrough"),
                conditioning_scale=0.5,
            )
            
            if controlnets_list:
                # Chain to previous
                result = cn_node.create_controlnet(
                    model_id=model,
                    preprocessor=("pose_tensorrt" if "openpose" in model 
                                 else "canny" if "canny" in model
                                 else "depth_tensorrt" if "depth" in model
                                 else "soft_edge" if "hed" in model
                                 else "passthrough"),
                    conditioning_scale=0.5,
                    controlnets=controlnets_list[-1] if len(controlnets_list) == 1 else controlnets_list,
                )
            
            controlnets_list = result[0] if isinstance(result[0], list) else [result[0]]
        
        # Pass 6 chained controlnets to pipeline config
        result = pipeline_node.create_pipeline_config(
            pipeline="streamdiffusion",
            model_id="stabilityai/sd-turbo",
            prompt="test prompt",
            guidance_scale=7.5,
            delta=1.0,
            num_inference_steps=50,
            width=512,
            height=512,
            seed=42,
            controlnets=controlnets_list,
        )
        
        config, json_str, width, height = result
        
        # Should have 6 controlnets
        assert "controlnets" in config["params"]
        assert len(config["params"]["controlnets"]) == 6


class TestSDXLControlNetConfig:
    """Test SDXL-specific controlnet compatibility."""

    def test_pipeline_config_accepts_sdxl_controlnets(self):
        """PipelineConfig should accept SDXL controlnets when using SDXL model."""
        controlnet_node = ControlNetNode()
        pipeline_node = PipelineConfigNode()

        depth_cn = controlnet_node.create_controlnet(
            model_id="xinsir/controlnet-depth-sdxl-1.0",
            preprocessor="depth_tensorrt",
            conditioning_scale=0.4,
        )
        canny_cn = controlnet_node.create_controlnet(
            model_id="xinsir/controlnet-canny-sdxl-1.0",
            preprocessor="canny",
            conditioning_scale=0.1,
            controlnets=depth_cn[0],
        )
        tile_cn = controlnet_node.create_controlnet(
            model_id="xinsir/controlnet-tile-sdxl-1.0",
            preprocessor="feedback",
            conditioning_scale=0.1,
            controlnets=canny_cn[0],
        )

        payload, *_ = pipeline_node.create_pipeline_config(
            pipeline="streamdiffusion",
            model_id="stabilityai/sdxl-turbo",
            prompt="sdxl test prompt",
            guidance_scale=1.0,
            delta=0.7,
            num_inference_steps=50,
            width=512,
            height=512,
            seed=123,
            controlnets=tile_cn[0],
        )

        controlnets_config = payload["params"]["controlnets"]
        assert len(controlnets_config) == 3
        assert {cn["model_id"] for cn in controlnets_config} == {
            "xinsir/controlnet-depth-sdxl-1.0",
            "xinsir/controlnet-canny-sdxl-1.0",
            "xinsir/controlnet-tile-sdxl-1.0",
        }

    def test_sdxl_controlnet_invalid_for_sd21_model(self):
        """SDXL controlnets should be rejected for SD 2.1 models."""
        controlnet_node = ControlNetNode()
        pipeline_node = PipelineConfigNode()

        depth_cn = controlnet_node.create_controlnet(
            model_id="xinsir/controlnet-depth-sdxl-1.0",
            preprocessor="depth_tensorrt",
            conditioning_scale=0.4,
        )

        with pytest.raises(ValueError):
            pipeline_node.create_pipeline_config(
                pipeline="streamdiffusion",
                model_id="stabilityai/sd-turbo",
                prompt="invalid combo",
                guidance_scale=7.5,
                delta=1.0,
                num_inference_steps=50,
                width=704,
                height=704,
                seed=42,
                controlnets=depth_cn[0],
            )


class TestModelSpecificControlNetNodes:
    """Test model-specific ControlNet node types."""

    def test_sd21_node_only_has_sd21_models(self):
        """ControlNetSD21Node should only expose SD2.1 models."""
        assert len(SD21_MODEL_CHOICES) > 0
        
        # All models should be SD2.1 variants
        for model_id in SD21_MODEL_CHOICES:
            assert "sd21" in model_id.lower() or "thibaud" in model_id.lower()
        
        # Should NOT include SDXL models
        for model_id in SD21_MODEL_CHOICES:
            assert "sdxl" not in model_id.lower()

    def test_sdxl_node_only_has_sdxl_models(self):
        """ControlNetSDXLNode should only expose SDXL models."""
        assert len(SDXL_MODEL_CHOICES) > 0
        
        # All models should be SDXL variants
        for model_id in SDXL_MODEL_CHOICES:
            assert "sdxl" in model_id.lower()
        
        # Should NOT include SD2.1 models
        for model_id in SDXL_MODEL_CHOICES:
            assert "sd21" not in model_id.lower()

    def test_sd21_node_preprocessors_match_models(self):
        """SD21 node preprocessors should be valid for SD21 models."""
        # These are the expected preprocessors for SD21 models
        expected = {"pose_tensorrt", "soft_edge", "canny", "depth_tensorrt", "passthrough"}
        assert set(SD21_PREPROCESSORS) == expected

    def test_sdxl_node_preprocessors_match_models(self):
        """SDXL node preprocessors should be valid for SDXL models."""
        # These are the expected preprocessors for SDXL models
        expected = {"depth_tensorrt", "canny", "feedback"}
        assert set(SDXL_PREPROCESSORS) == expected

    def test_sd21_node_creates_valid_config(self):
        """ControlNetSD21Node should create valid configs."""
        node = ControlNetSD21Node()
        
        result = node.create_controlnet(
            model_id="thibaud/controlnet-sd21-openpose-diffusers",
            preprocessor="pose_tensorrt",
            conditioning_scale=0.711,
        )
        
        config = result[0]
        assert config["model_id"] == "thibaud/controlnet-sd21-openpose-diffusers"
        assert config["preprocessor"] == "pose_tensorrt"
        assert config["conditioning_scale"] == 0.711

    def test_sdxl_node_creates_valid_config(self):
        """ControlNetSDXLNode should create valid configs."""
        node = ControlNetSDXLNode()
        
        result = node.create_controlnet(
            model_id="xinsir/controlnet-depth-sdxl-1.0",
            preprocessor="depth_tensorrt",
            conditioning_scale=0.4,
        )
        
        config = result[0]
        assert config["model_id"] == "xinsir/controlnet-depth-sdxl-1.0"
        assert config["preprocessor"] == "depth_tensorrt"
        assert config["conditioning_scale"] == 0.4

    def test_sd21_node_rejects_invalid_preprocessor(self):
        """SD21 node should reject invalid preprocessor for model."""
        node = ControlNetSD21Node()
        
        # feedback is only valid for SDXL tile, not SD21
        with pytest.raises(ValueError, match="not valid for ControlNet"):
            node.create_controlnet(
                model_id="thibaud/controlnet-sd21-openpose-diffusers",
                preprocessor="feedback",  # Invalid for openpose
                conditioning_scale=0.5,
            )

    def test_sdxl_node_rejects_invalid_preprocessor(self):
        """SDXL node should reject invalid preprocessor for model."""
        node = ControlNetSDXLNode()
        
        # pose_tensorrt is only valid for SD21 openpose
        with pytest.raises(ValueError, match="not valid for ControlNet"):
            node.create_controlnet(
                model_id="xinsir/controlnet-depth-sdxl-1.0",
                preprocessor="pose_tensorrt",  # Invalid for SDXL depth
                conditioning_scale=0.5,
            )

    def test_sd21_and_sdxl_nodes_can_chain(self):
        """Model-specific nodes should support chaining with same model family."""
        sd21_node1 = ControlNetSD21Node()
        sd21_node2 = ControlNetSD21Node()
        
        result1 = sd21_node1.create_controlnet(
            model_id="thibaud/controlnet-sd21-openpose-diffusers",
            preprocessor="pose_tensorrt",
            conditioning_scale=0.7,
        )
        
        result2 = sd21_node2.create_controlnet(
            model_id="thibaud/controlnet-sd21-canny-diffusers",
            preprocessor="canny",
            conditioning_scale=0.2,
            controlnets=result1[0],
        )
        
        config_list = result2[0]
        assert isinstance(config_list, list)
        assert len(config_list) == 2
        assert config_list[0]["model_id"] == "thibaud/controlnet-sd21-openpose-diffusers"
        assert config_list[1]["model_id"] == "thibaud/controlnet-sd21-canny-diffusers"

    def test_sdxl_nodes_chain_correctly(self):
        """SDXL-specific nodes should chain together correctly."""
        sdxl_node1 = ControlNetSDXLNode()
        sdxl_node2 = ControlNetSDXLNode()
        sdxl_node3 = ControlNetSDXLNode()
        
        result1 = sdxl_node1.create_controlnet(
            model_id="xinsir/controlnet-depth-sdxl-1.0",
            preprocessor="depth_tensorrt",
            conditioning_scale=0.4,
        )
        
        result2 = sdxl_node2.create_controlnet(
            model_id="xinsir/controlnet-canny-sdxl-1.0",
            preprocessor="canny",
            conditioning_scale=0.1,
            controlnets=result1[0],
        )
        
        result3 = sdxl_node3.create_controlnet(
            model_id="xinsir/controlnet-tile-sdxl-1.0",
            preprocessor="feedback",
            conditioning_scale=0.1,
            controlnets=result2[0],
        )
        
        config_list = result3[0]
        assert isinstance(config_list, list)
        assert len(config_list) == 3
        assert config_list[0]["model_id"] == "xinsir/controlnet-depth-sdxl-1.0"
        assert config_list[1]["model_id"] == "xinsir/controlnet-canny-sdxl-1.0"
        assert config_list[2]["model_id"] == "xinsir/controlnet-tile-sdxl-1.0"

    def test_sdxl_chain_with_pipeline_config(self):
        """SDXL ControlNets should work with SDXL PipelineConfig."""
        sdxl_node1 = ControlNetSDXLNode()
        sdxl_node2 = ControlNetSDXLNode()
        pipeline_node = PipelineConfigNode()
        
        cn1 = sdxl_node1.create_controlnet(
            model_id="xinsir/controlnet-depth-sdxl-1.0",
            preprocessor="depth_tensorrt",
            conditioning_scale=0.4,
        )
        
        cn2 = sdxl_node2.create_controlnet(
            model_id="xinsir/controlnet-tile-sdxl-1.0",
            preprocessor="feedback",
            conditioning_scale=0.1,
            controlnets=cn1[0],
        )
        
        result = pipeline_node.create_pipeline_config(
            pipeline="streamdiffusion",
            model_id="stabilityai/sdxl-turbo",
            prompt="SDXL test",
            guidance_scale=1.0,
            delta=0.7,
            num_inference_steps=50,
            width=512,
            height=512,
            seed=42,
            controlnets=cn2[0],
        )
        
        config, json_str, width, height = result
        assert len(config["params"]["controlnets"]) == 2
        assert config["params"]["model_id"] == "stabilityai/sdxl-turbo"


class TestProcessingNodes:
    """Tests for Daydream preprocessing nodes."""

    def test_processor_node_returns_list(self):
        node = ProcessingNode()

        result = node.create_processor_chain(
            processor_type="blur",
            processor_enabled=True,
            processor_params='{"kernel": 5}',
        )

        processors = result[0]
        assert isinstance(processors, list)
        assert processors[0]["type"] == "blur"
        assert processors[0]["params"]["kernel"] == 5

    def test_processor_node_chain_order(self):
        node1 = ProcessingNode()
        node2 = ProcessingNode()

        first = node1.create_processor_chain(
            processor_type="blur",
            processor_params="{}",
        )
        chained = node2.create_processor_chain(
            processor_type="canny",
            processor_params="{}",
            processors=first[0],
        )

        processors = chained[0]
        assert len(processors) == 2
        assert processors[0]["type"] == "blur"
        assert processors[1]["type"] == "canny"

    def test_processing_block_requires_processors_when_enabled(self):
        block_node = ProcessingBlockNode()

        with pytest.raises(ValueError, match="At least one processor"):
            block_node.create_processing_block(
                enabled=True,
                processors_json="[]",
            )

    def test_processing_block_accepts_json(self):
        block_node = ProcessingBlockNode()
        block = block_node.create_processing_block(
            enabled=True,
            processors_json='[{"type": "blur", "enabled": true, "params": {}}]',
        )[0]

        assert block["enabled"] is True
        assert block["processors"][0]["type"] == "blur"


class TestLinkTypeAndPipelineIntegration:
    """Test that all ControlNet nodes use a single link type and work with PipelineConfig."""

    def test_all_nodes_use_same_link_type(self):
        """All ControlNet nodes should use CONTROLNET_CONFIG link type."""
        # All nodes use the same link type for universal compatibility
        assert ControlNetNode.LINK_TYPE == CONTROLNET_CONFIG
        assert ControlNetSD21Node.LINK_TYPE == CONTROLNET_CONFIG
        assert ControlNetSDXLNode.LINK_TYPE == CONTROLNET_CONFIG
        
        # Verify RETURN_TYPES (tuple attribute, not method)
        assert ControlNetNode.RETURN_TYPES == (CONTROLNET_CONFIG,)
        assert ControlNetSD21Node.RETURN_TYPES == (CONTROLNET_CONFIG,)
        assert ControlNetSDXLNode.RETURN_TYPES == (CONTROLNET_CONFIG,)

    def test_pipeline_config_accepts_controlnet_config(self):
        """PipelineConfig should accept CONTROLNET_CONFIG type."""
        input_types = PipelineConfigNode.INPUT_TYPES()
        
        # Check controlnets input exists and accepts CONTROLNET_CONFIG
        assert "controlnets" in input_types["optional"]
        assert input_types["optional"]["controlnets"][0] == "CONTROLNET_CONFIG"

    def test_sd21_controlnets_work_with_pipeline(self):
        """SD21 ControlNets should work with PipelineConfig."""
        sd21_node = ControlNetSD21Node()
        pipeline_node = PipelineConfigNode()
        
        cn = sd21_node.create_controlnet(
            model_id="thibaud/controlnet-sd21-openpose-diffusers",
            preprocessor="pose_tensorrt",
            conditioning_scale=0.7,
        )
        
        result = pipeline_node.create_pipeline_config(
            pipeline="streamdiffusion",
            model_id="stabilityai/sd-turbo",
            prompt="SD21 test",
            guidance_scale=7.5,
            delta=1.0,
            num_inference_steps=50,
            width=512,
            height=512,
            seed=42,
            controlnets=cn[0],
        )
        
        config, json_str, width, height = result
        assert len(config["params"]["controlnets"]) == 1
        assert config["params"]["controlnets"][0]["model_id"] == "thibaud/controlnet-sd21-openpose-diffusers"

    def test_sdxl_controlnets_work_with_pipeline(self):
        """SDXL ControlNets should work with PipelineConfig."""
        sdxl_node = ControlNetSDXLNode()
        pipeline_node = PipelineConfigNode()
        
        cn = sdxl_node.create_controlnet(
            model_id="xinsir/controlnet-depth-sdxl-1.0",
            preprocessor="depth_tensorrt",
            conditioning_scale=0.4,
        )
        
        result = pipeline_node.create_pipeline_config(
            pipeline="streamdiffusion",
            model_id="stabilityai/sdxl-turbo",
            prompt="SDXL test",
            guidance_scale=1.0,
            delta=0.7,
            num_inference_steps=50,
            width=512,
            height=512,
            seed=42,
            controlnets=cn[0],
        )
        
        config, json_str, width, height = result
        assert len(config["params"]["controlnets"]) == 1
        assert config["params"]["controlnets"][0]["model_id"] == "xinsir/controlnet-depth-sdxl-1.0"

    def test_any_controlnet_nodes_can_chain(self):
        """Any ControlNet nodes can chain together (validated at runtime)."""
        sd21_node = ControlNetSD21Node()
        sdxl_node = ControlNetSDXLNode()
        generic_node = ControlNetNode()
        
        # All nodes can chain because they use the same link type
        cn1 = sd21_node.create_controlnet(
            model_id="thibaud/controlnet-sd21-openpose-diffusers",
            preprocessor="pose_tensorrt",
            conditioning_scale=0.7,
        )
        
        # This chains - compatibility is checked at PipelineConfig, not link time
        cn2 = generic_node.create_controlnet(
            model_id="thibaud/controlnet-sd21-canny-diffusers",
            preprocessor="canny",
            conditioning_scale=0.2,
            controlnets=cn1[0],
        )
        
        config_list = cn2[0]
        assert isinstance(config_list, list)
        assert len(config_list) == 2

    def test_pipeline_accepts_processing_blocks(self):
        """PipelineConfig should accept preprocessing blocks."""
        processor_node = ProcessingNode()
        block_node = ProcessingBlockNode()
        pipeline_node = PipelineConfigNode()

        processors = processor_node.create_processor_chain(
            processor_type="blur",
            processor_params="{}",
        )
        block = block_node.create_processing_block(
            enabled=True,
            processors=processors[0],
        )

        payload, *_ = pipeline_node.create_pipeline_config(
            pipeline="streamdiffusion",
            model_id="stabilityai/sd-turbo",
            prompt="test",
            guidance_scale=7.5,
            delta=1.0,
            num_inference_steps=20,
            width=512,
            height=512,
            seed=1,
            image_preprocessing=block[0],
        )

        assert "image_preprocessing" in payload["params"]
        block_payload = payload["params"]["image_preprocessing"]
        assert block_payload["enabled"] is True
        assert block_payload["processors"][0]["type"] == "blur"
