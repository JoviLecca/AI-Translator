"""构建脚本：安装 pyinstaller（如缺）并按 tools/AITranslator.spec 打包。"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm",
           "--clean", str(ROOT / "tools" / "AITranslator.spec")]
    print(">", " ".join(cmd))
    return subprocess.call(cmd, cwd=str(ROOT))


if __name__ == "__main__":
    raise SystemExit(main())
