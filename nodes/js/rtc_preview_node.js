
import { app } from "../../../scripts/app.js";
import { rtcManager } from "./rtc_manager.js";

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

      this.__video = document.createElement("video");
      this.__video.style.width = "100%";
      this.__video.style.height = "100%";
      this.__video.style.objectFit = "contain";
      this.__video.style.backgroundColor = "#000";
      this.__video.style.borderRadius = "8px";
      this.__video.autoplay = true;
      this.__video.muted = true;
      this.__video.playsInline = true;

      if (rtcManager.stream) {
          this.__video.srcObject = rtcManager.stream;
      }

      this.__unsubscribe = rtcManager.subscribe((event) => {
          if (event.type === "started") {
              this.__video.srcObject = event.stream;
          } else if (event.type === "stopped") {
              this.__video.srcObject = null;
          }
      });

      const widgetHeight = Math.max(160, this.size[1] - 48);
      this.__rtcWidget = this.addDOMWidget("video", "Preview", this.__video, {
        serialize: false,
        width: this.size[0],
        height: widgetHeight,
      });

      this._updateSize();
      return result;
    };

    nodeType.prototype._updateSize = function () {
      if (!this.__rtcWidget || !this.__video) {
        return;
      }
      const width = Math.max(360, this.size[0]);
      const height = Math.max(180, this.size[1] - 48);
      this.__video.style.width = `${width}px`;
      this.__video.style.height = `${height}px`;
      this.setDirtyCanvas(true, true);
    };

    const originalOnResize = nodeType.prototype.onResize;
    nodeType.prototype.onResize = function (...args) {
      originalOnResize?.apply(this, args);
      this._updateSize();
    };
    
    const originalOnRemoved = nodeType.prototype.onRemoved;
    nodeType.prototype.onRemoved = function() {
        if (this.__unsubscribe) this.__unsubscribe();
        if (originalOnRemoved) originalOnRemoved.apply(this, arguments);
    };
  },
});
