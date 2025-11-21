import { app } from "../../../scripts/app.js";

const PREVIEW_PATH = "/extensions/rtc-node/rtc_preview.html";

app.registerExtension({
  name: "RTC.StreamPreviewNode",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData?.name !== "RTCStreamUIPreview") {
      return;
    }

    nodeType.size = nodeType.size || [560, 560];

    const originalOnNodeCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function (...args) {
      this.title = "RTC Stream Preview";
      this.color = "#4b9cd3";
      this.resizable = true;
      this.flags = this.flags || {};
      this.flags.resizable = true;

      const result = originalOnNodeCreated?.apply(this, args);

      this.__rtcIframe = document.createElement("iframe");
      this.__rtcIframe.style.width = "100%";
      this.__rtcIframe.style.height = "100%";
      this.__rtcIframe.style.border = "none";
      this.__rtcIframe.style.borderRadius = "8px";
      this.__rtcIframe.allow = "autoplay; fullscreen";
      this.__rtcIframe.src = `${PREVIEW_PATH}?ts=${Date.now()}`;

      const widgetHeight = Math.max(160, this.size[1] - 48);
      this.__rtcWidget = this.addDOMWidget("iframe", "Preview", this.__rtcIframe, {
        serialize: false,
        width: this.size[0],
        height: widgetHeight,
      });

      this.addWidget("button", "Reload Preview", null, () => {
        if (this.__rtcIframe) {
          const current = this.__rtcIframe.src.split("?")[0];
          this.__rtcIframe.src = `${current}?ts=${Date.now()}`;
        }
      });

      this._updateRtcIframeSize();
      return result;
    };

    nodeType.prototype._updateRtcIframeSize = function () {
      if (!this.__rtcWidget || !this.__rtcIframe) {
        return;
      }
      const width = Math.max(360, this.size[0]);
      const height = Math.max(180, this.size[1] - 48);
      // Update iframe element directly
      this.__rtcIframe.style.width = `${width}px`;
      this.__rtcIframe.style.height = `${height}px`;
      this.setDirtyCanvas(true, true);
    };

    const originalOnResize = nodeType.prototype.onResize;
    nodeType.prototype.onResize = function (...args) {
      originalOnResize?.apply(this, args);
      this._updateRtcIframeSize();
    };
  },
});

