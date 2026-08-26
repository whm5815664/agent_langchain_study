"""本机摄像头拍照工具。"""

import json
import time
from pathlib import Path

import cv2
from langchain_core.tools import tool

# planweb/media/camera（路径含中文时不能用 cv2.imwrite）
_CAMERA_DIR = Path(__file__).resolve().parents[3] / "media" / "camera"
# 固定暂存文件名，每次拍照覆盖
_LATEST_NAME = "latest.jpg"


@tool
def capture_camera(camera_index: int = 0) -> str:
    """
    功能:调用本机摄像头拍照并返回图片。
        用户说拍照/打开摄像头/看一下现场时使用。
    Args:
        camera_index:摄像头编号，默认0（本机主摄像头）；打不开时会自动尝试邻近编号
    return:
        含 image_url 与 markdown 的JSON字符串（含 ![摄像头](url)，须原样写入最终回答以便前端展示）
    """
    preferred = max(0, int(camera_index))
    # 优先用户指定，再扫 0~3
    candidates = [preferred] + [i for i in range(4) if i != preferred]

    last_err = ""
    for idx in candidates:
        cap = None
        for backend in (cv2.CAP_DSHOW, cv2.CAP_ANY):
            cap = cv2.VideoCapture(idx, backend)
            if cap.isOpened():
                break
            cap.release()
            cap = None
        if cap is None:
            last_err = f"index={idx} 无法打开"
            continue
        try:
            frame = None
            for _ in range(5):
                ok, frame = cap.read()
                if not ok:
                    frame = None
                    break
            if frame is None:
                last_err = f"index={idx} 已打开但读帧失败"
                continue

            ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
            if not ok:
                return "拍照成功但保存图片失败。"
            filepath = _CAMERA_DIR / _LATEST_NAME
            filepath.parent.mkdir(parents=True, exist_ok=True)
            filepath.write_bytes(buf.tobytes())

            # 固定路径覆盖旧图；加时间戳避免浏览器缓存
            image_url = f"/media/camera/{_LATEST_NAME}?t={int(time.time())}"
            return json.dumps(
                {
                    "camera_index": idx,
                    "saved_path": str(filepath),
                    "image_url": image_url,
                    "markdown": f"![摄像头截图]({image_url})",
                },
                ensure_ascii=False,
            )
        except Exception as e:
            last_err = f"index={idx} 异常：{e}"
        finally:
            cap.release()

    return (
        "摄像头无法打开，请检查设备是否已连接或被其他程序占用。"
        f"（详情：{last_err}）"
    )
