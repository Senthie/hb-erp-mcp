"""PyInstaller 打包入口（等价于 console_scripts 的 erp 命令）。"""

import sys

from erp_tools.cli import main

if __name__ == "__main__":
    sys.exit(main())
