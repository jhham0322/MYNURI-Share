"""태스크 실행기 모음.

새 작업은 이 패키지에 파일을 추가하고 `NuriApp(task=...)` 로 주입한다.
"""

from nuri.tasks.base import CommandRunner, subprocess_runner
from nuri.tasks.paseo import PaseoTask

__all__ = ["CommandRunner", "PaseoTask", "subprocess_runner"]
