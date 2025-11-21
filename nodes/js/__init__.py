"""
Register the Web UI preview node so ComfyUI loads the accompanying JS extension.
"""


class RTCStreamUIPreview:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {}, "optional": {}}

    RETURN_TYPES = ()
    FUNCTION = "run"
    CATEGORY = "RTC Stream"

    def run(self):
        return ()


NODE_CLASS_MAPPINGS = {"RTCStreamUIPreview": RTCStreamUIPreview}
NODE_DISPLAY_NAME_MAPPINGS = {"RTCStreamUIPreview": "RTC Stream UI"}

