#!/usr/bin/env python3
"""打包 / 安装 / 自检 DSH Bridge 插件（Zotero 7）。

为什么需要它：Zotero 运行时会长期持有 zotero.sqlite 写锁，外部程序无法安全写库；
Zotero 自带的本地 HTTP 服务只开放 /connector/*（抓网页文献用），没有写库接口。
装这个插件后，Zotero 开着也能通过 POST /dsh/bridge 挂 PDF、写笔记——不用每次关软件。

用法：
  python install_bridge.py --build          # 只打包出 dsh-bridge.xpi
  python install_bridge.py --install        # 打包 + 复制进 Zotero profile/extensions（需重启 Zotero）
  python install_bridge.py --check          # 探测 http://127.0.0.1:23119/dsh/bridge 是否已生效
  python install_bridge.py --uninstall      # 移除已安装的 xpi（需重启 Zotero）

装完必须**重启 Zotero**（不是退出登录，是彻底退出再打开）。
不用命令行也能装：Zotero → 工具 → 插件 → 右上齿轮 → Install Add-on From File… → 选 dsh-bridge.xpi
"""
import argparse
import json
import os
import shutil
import sys
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ID = "dsh-bridge@zengf.local"
BRIDGE_URL = "http://127.0.0.1:23119/dsh/bridge"
APP_DATA = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
ZOTERO_ROOT = os.path.join(APP_DATA, "Zotero", "Zotero")


def find_profile():
    """从 profiles.ini 里解析出默认 profile 的绝对路径。"""
    ini = os.path.join(ZOTERO_ROOT, "profiles.ini")
    if not os.path.isfile(ini):
        return None
    cur = None
    with open(ini, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if line.startswith("[Profile"):
                cur = {}
            elif "=" in line and cur is not None:
                k, v = line.split("=", 1)
                cur[k.strip()] = v.strip()
            elif not line and cur is not None:
                if cur.get("Default") == "1" and cur.get("Path"):
                    p = cur["Path"]
                    if cur.get("IsRelative") == "1":
                        p = os.path.join(ZOTERO_ROOT, p)
                    return os.path.normpath(p)
                cur = None
    # 兜底：取第一个带 Path 的 profile
    with open(ini, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.strip().startswith("Path="):
                p = line.strip().split("=", 1)[1]
                return os.path.normpath(os.path.join(ZOTERO_ROOT, p))
    return None


def build(out_dir=None):
    out_dir = out_dir or os.path.join(HERE, "dist")
    os.makedirs(out_dir, exist_ok=True)
    xpi = os.path.join(out_dir, "dsh-bridge.xpi")
    files = ["manifest.json", "bootstrap.js"]
    for fn in files:
        if not os.path.isfile(os.path.join(HERE, fn)):
            raise SystemExit("缺少源文件：%s" % fn)
    with zipfile.ZipFile(xpi, "w", zipfile.ZIP_DEFLATED) as z:
        for fn in files:
            z.write(os.path.join(HERE, fn), fn)
    return xpi


def install(xpi):
    profile = find_profile()
    if not profile:
        raise SystemExit("找不到 Zotero profile（profiles.ini 解析失败）")
    ext_dir = os.path.join(profile, "extensions")
    os.makedirs(ext_dir, exist_ok=True)
    dest = os.path.join(ext_dir, PLUGIN_ID + ".xpi")
    shutil.copyfile(xpi, dest)
    return dest, profile


def uninstall():
    profile = find_profile()
    if not profile:
        raise SystemExit("找不到 Zotero profile")
    dest = os.path.join(profile, "extensions", PLUGIN_ID + ".xpi")
    if os.path.isfile(dest):
        os.remove(dest)
        return dest, True
    return dest, False


def check():
    try:
        req = urllib.request.Request(
            BRIDGE_URL,
            data=json.dumps({"op": "ping"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            body = json.loads(r.read().decode("utf-8"))
        return {"ok": bool(body.get("ok")), "response": body}
    except Exception as e:
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--install", action="store_true")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if not any([a.build, a.install, a.uninstall, a.check]):
        a.check = True

    out = {}
    if a.check:
        out["check"] = check()
        if a.json:
            print(json.dumps(out, ensure_ascii=False, indent=1))
        else:
            c = out["check"]
            if c["ok"]:
                print("[OK] 桥接已生效：%s" % json.dumps(c["response"], ensure_ascii=False))
            else:
                print("[--] 桥接未生效：%s" % c.get("error", c))
        if not (a.build or a.install or a.uninstall):
            return 0 if out["check"]["ok"] else 1

    if a.uninstall:
        dest, existed = uninstall()
        out["uninstall"] = {"path": dest, "removed": existed}
        print(("[OK] 已移除 " if existed else "[--] 未安装 ") + dest)
        print("请彻底退出并重新打开 Zotero 使其生效。")
        return 0

    xpi = build()
    out["xpi"] = xpi
    print("[OK] 已打包 %s（%d 字节）" % (xpi, os.path.getsize(xpi)))

    if a.install:
        dest, profile = install(xpi)
        out["installed"] = {"xpi": dest, "profile": profile}
        print("[OK] 已复制到 %s" % dest)
        print("[!] 必须彻底退出 Zotero 再打开（托盘图标右键 → 退出），插件才会加载。")
        print("    启动后运行：python install_bridge.py --check")
    else:
        print("安装方式二选一：")
        print("  A) python install_bridge.py --install   然后重启 Zotero")
        print("  B) Zotero → 工具 → 插件 → 右上齿轮 → Install Add-on From File… → %s" % xpi)
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
