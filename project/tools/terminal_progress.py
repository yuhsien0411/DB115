"""僅在互動終端機顯示等待動畫；不改動 stdout 的結果資料。"""
import os
import sys
import threading
import time


class Spinner:
    def __init__(self, label, stream=None):
        self.label = label
        self.stream = sys.stderr if stream is None else stream
        self.enabled = self.stream.isatty() and os.environ.get("TERM") != "dumb"
        self._stop = threading.Event()
        self._thread = None

    def _write(self, text):
        try:
            self.stream.write(text)
            self.stream.flush()
        except OSError:
            # 終端機關閉時，進度顯示不能影響實驗與結果寫入。
            self.enabled = False

    def _animate(self):
        frames = "-\\|/"
        frame = 0
        while self.enabled and not self._stop.is_set():
            elapsed = int(time.monotonic() - self._started)
            minutes, seconds = divmod(elapsed, 60)
            self._write(f"\r\033[2K{frames[frame % len(frames)]} {self.label}"
                        f"  已用 {minutes:02d}:{seconds:02d}")
            frame += 1
            if self._stop.wait(0.2):
                break

    def __enter__(self):
        if self.enabled:
            self._started = time.monotonic()
            self._thread = threading.Thread(target=self._animate, daemon=True)
            self._thread.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
            if self.enabled:
                self._write("\r\033[2K")
