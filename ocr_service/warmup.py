"""构建期模型预热：把 OCR 模型缓存烘焙进镜像，避免运行期首次调用才下载。

PaddleOCR 的模型在构建期下载（需联网）；其运行时若在容器内不可用（会段错误，
Python 抓不住），改为子进程探测后用 RapidOCR —— RapidOCR 的模型随 wheel 分发，
无需下载。任何异常都不阻断构建。
"""
import subprocess
import sys

PROBE = (
    "from paddleocr import PaddleOCR;"
    "PaddleOCR(use_angle_cls=False, lang='ch', show_log=False);"
    "print('ok')"
)


def main() -> None:
    try:
        r = subprocess.run([sys.executable, "-c", PROBE], capture_output=True, timeout=600)
        if r.returncode == 0:
            print("PaddleOCR 模型已下载并烘焙进镜像")
            return
        print(f"PaddleOCR 预热跳过（退出码 {r.returncode}）：{r.stderr.decode('utf-8', 'ignore')[-200:]}", file=sys.stderr)
    except subprocess.TimeoutExpired:
        print("PaddleOCR 预热超时", file=sys.stderr)

    try:
        from rapidocr_onnxruntime import RapidOCR

        RapidOCR()
        print("RapidOCR 就绪（PP-OCR 模型随包分发，无需下载）")
    except Exception as e:  # noqa: BLE001
        print(f"RapidOCR 预热跳过：{e}", file=sys.stderr)


if __name__ == "__main__":
    main()
