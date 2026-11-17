# -*- coding: utf-8 -*-
"""
启动本地 PostgreSQL（conda 环境 SggYourSpecialJobPG 中安装，数据目录 pgdata/）。

用法：python scripts/start_pg.py
- 数据目录不存在时自动 initdb（trust 本地认证）
- 已在运行时直接提示，不重复启动
"""
import os
import subprocess
import sys
import time
from pathlib import Path

# Windows 控制台默认 GBK，统一切换 UTF-8 输出避免乱码
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

#1.路径常量（conda 环境 SggYourSpecialJobPG 中的 PG 服务端）
BINDIR = Path(
    os.getenv("PG_BINDIR")
    or (Path.home() / ".conda" / "envs" / "SggYourSpecialJobPG" / "Library" / "bin")
)
DATADIR = Path(__file__).resolve().parent.parent / "pgdata"
LOGFILE = DATADIR / "server.log"


def run(args: list[str], **kw) -> subprocess.CompletedProcess:
    """运行 PG 服务端工具并返回结果（首元素为 exe 名，其余为参数）"""
    cmd = [str(BINDIR / args[0]), *args[1:]]
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", **kw)


def is_ready() -> bool:
    """检查 PG 是否已就绪"""
    return run(["pg_isready.exe", "-h", "localhost", "-p", "5432"]).returncode == 0


def main() -> int:
    #2.数据目录不存在则先初始化
    if not (DATADIR / "PG_VERSION").exists():
        print(f"数据目录不存在，正在 initdb: {DATADIR}")
        r = run(["initdb.exe", "-D", str(DATADIR), "-U", "postgres",
                 "--auth=trust", "--encoding=UTF8", "--locale=C"])
        if r.returncode != 0:
            print("initdb 失败:", r.stdout, r.stderr)
            return 1

    #3.已在运行则直接退出
    if is_ready():
        print("PostgreSQL 已在运行 (localhost:5432)")
        return 0

    #4.启动服务（输出重定向到文件避免管道 EOF 阻塞；BREAKAWAY 使 postgres.exe
    # 脱离父进程树，父进程退出/被杀时数据库不受影响）
    flags = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
             | getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0))
    with open(DATADIR / "pg_ctl.log", "a", encoding="utf-8") as ctl_log:
        try:
            r = subprocess.run(
                [str(BINDIR / "pg_ctl.exe"), "-D", str(DATADIR), "-l", str(LOGFILE), "start"],
                stdout=ctl_log, stderr=subprocess.STDOUT, creationflags=flags,
            )
        except OSError:
            # Job 不允许 breakaway 时退回普通 detached
            r = subprocess.run(
                [str(BINDIR / "pg_ctl.exe"), "-D", str(DATADIR), "-l", str(LOGFILE), "start"],
                stdout=ctl_log, stderr=subprocess.STDOUT,
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
            )
    if r.returncode != 0:
        print("pg_ctl start 失败，详见 pgdata/pg_ctl.log 与 pgdata/server.log")
        return 1

    #5.等待就绪（最多 15 秒）
    for _ in range(15):
        if is_ready():
            print("PostgreSQL 启动成功 (localhost:5432, 数据目录 pgdata/)")
            return 0
        time.sleep(1)
    print("PostgreSQL 启动超时，请查看 pgdata/server.log")
    return 1


if __name__ == "__main__":
    sys.exit(main())
