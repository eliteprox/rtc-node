from rtc_stream.state_store import RTC_STATE


def test_frames_roundtrip():
    RTC_STATE.set_input_frame("aaa")
    b64, meta, has = RTC_STATE.get_input_frame()
    assert has is True
    assert b64 == "aaa"
    assert meta["sequence"] >= 1

    RTC_STATE.set_output_frame("bbb")
    b64, meta, has = RTC_STATE.get_output_frame()
    assert has is True
    assert b64 == "bbb"
    assert meta["sequence"] >= 1


def test_desired_config_roundtrip():
    cfg = RTC_STATE.set_desired_config(
        stream_name="x",
        pipeline="p",
        pipeline_config={"pipeline": "p", "params": {"a": 1}},
        width=640,
        height=360,
        fps=24,
    )
    assert cfg["stream_name"] == "x"
    assert cfg["pipeline"] == "p"
    assert cfg["width"] == 640
    assert cfg["height"] == 360
    assert cfg["fps"] == 24


def test_session_update_and_clear():
    session = RTC_STATE.update_session(
        {
            "running": True,
            "status": "connecting",
            "streamId": "s1",
            "whipUrl": "whip",
            "whepUrl": "whep",
            "stopUrl": "stop",
        }
    )
    assert session["running"] is True
    assert session["stream_id"] == "s1"
    assert session["whip_url"] == "whip"
    assert session["whep_url"] == "whep"
    assert session["stop_url"] == "stop"

    cleared = RTC_STATE.clear_session()
    assert cleared["running"] is False
    assert cleared["stream_id"] == ""

