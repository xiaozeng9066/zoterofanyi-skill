#!/usr/bin/env python3
"""zotero_api.py — Zotero 挂回工具（Zotero 10 官方 Local API 优先，标准库零依赖）。

为什么又写一个：旧挂回依赖两个都不可靠的通道——
  * `~/.dsh/mcp/zotero_mcp.py` 直接写 sqlite，前提是「Zotero 未运行」，一开软件就锁库；
  * `zotero_bridge` 插件注册 /dsh/bridge，但 Zotero 7+ 会主动禁用「自己出现在 profile
    目录里」的插件，且 strict_max_version 一越界（如升到 Zotero 10）就被 appDisabled。
  Zotero 10 起提供了官方本地写 API（localhost:23119/api/），读无需认证、写只需用户在
  设置里勾选「允许其他应用与本机 Zotero 通信」+ 首次写入点一次授权，官方维护、升级不破。

写路径（按可用性自动降级）：
  1. Local API（Zotero 10+，Zotero 开着也能写）—— 首选，本工具主路径。
  2. sqlite 直写（Zotero 未运行时）—— 兜底。
  3. 旧 bridge 插件（若用户已装且生效）—— 兼容旧环境。

Local API 写授权流程（官方 v3/local_api）：
  * 先 GET /api/ 读 Zotero-Server-ID 头（写请求必须带，缺失返回 428）。
  * POST /api/local/authorize {appName} → Zotero 弹「Allow / Always Allow / Deny」，
    批准后返回 {key, remember}；Always Allow 的 key 可复用，否则一次性（写一次耗一个）。
  * 每次写带 Zotero-API-Key: <key>；收到 401 就再 authorize 一次。
  * 替换已有附件 = 三阶段上传（If-Match: <旧md5> 触发替换）：
      ① POST /users/0/items/<key>/file?md5=&filename=&filesize=&mtime= + If-Match → 得 uploadKey
      ② POST /api/local/uploads/<uploadKey> 传文件字节 → 201
      ③ POST /users/0/items/<key>/file?upload=<uploadKey> + If-Match → 204

用法：
  python scripts/zotero_api.py cli search "school context"
  python scripts/zotero_api.py cli item <KEY>
  python scripts/zotero_api.py cli attach <KEY> "<pdf路径>" --title "中英对照" [--replace]
  python scripts/zotero_api.py cli note <KEY> --title "翻译项目备注" --text-file note.md
  python scripts/zotero_api.py cli status           # 探测 Local API / sqlite / bridge 可用性
  python scripts/zotero_api.py cli authorize        # 手动触发一次写授权（Always Allow）
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid

ZOTERO_DIR = os.environ.get("ZOTERO_DIR") or os.path.join(os.path.expanduser("~"), "Zotero")
DB_PATH = os.path.join(ZOTERO_DIR, "zotero.sqlite")
STORAGE = os.path.join(ZOTERO_DIR, "storage")
LOCAL_API_BASE = "http://127.0.0.1:23119/api/"
BRIDGE_URL = "http://127.0.0.1:23119/dsh/bridge"
APP_NAME = "zoterofanyi"
KEYS = "23456789ABCDEFGHIJKLMNPQRSTUVWXYZ"
CONTENT_TYPES = {
    ".pdf": "application/pdf", ".epub": "application/epub+zip", ".html": "text/html",
    ".txt": "text/plain", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
}
TYPE_ATTACHMENT, TYPE_NOTE = 3, 28


class ZoteroError(Exception):
    pass


# ─────────────────────────────── Local API 底层 ───────────────────────────────
def _http(method, path, body=None, headers=None, timeout=20):
    """对 Local API 发请求。path 相对 LOCAL_API_BASE（不带开头的 /api/）。"""
    url = LOCAL_API_BASE + path.lstrip("/")
    data = None
    if body is not None:
        if isinstance(body, (dict, list)):
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers = dict(headers or {})
            headers.setdefault("Content-Type", "application/json")
        else:
            data = body
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            hdrs = dict(r.headers)
            return r.status, hdrs, raw
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def _server_id():
    """读 Zotero-Server-ID（写请求必须带）。返回 None 表示 Local API 未开启。"""
    status, hdrs, body = _http("GET", "/", timeout=5)
    if status == 403:
        return None, "Local API is not enabled"
    if status != 200:
        return None, "GET /api/ -> HTTP %d" % status
    return hdrs.get("Zotero-Server-ID"), None


def local_api_status():
    sid, err = _server_id()
    if err:
        return {"ok": False, "reason": err}
    return {"ok": True, "server_id": sid}


def _authorize_once(server_id):
    """POST /api/local/authorize 弹 Zotero 授权对话框，返回 (key, remember)。"""
    status, hdrs, body = _http(
        "POST", "local/authorize",
        body={"appName": APP_NAME},
        headers={"Zotero-Server-ID": server_id}, timeout=60)
    try:
        obj = json.loads(body.decode("utf-8"))
    except Exception:
        obj = {}
    if status == 200 and obj.get("key"):
        return obj["key"], bool(obj.get("remember"))
    if status == 403 and obj.get("denied"):
        raise ZoteroError("用户在 Zotero 对话框里拒绝了写授权（denied）")
    raise ZoteroError("授权失败：HTTP %d %s" % (status, obj.get("error") or body[:200]))


def _authorize():
    """授权并尽量复用：优先读持久化 key 文件（含 server_id），否则现场弹窗。"""
    sid, err = _server_id()
    if err:
        raise ZoteroError(err + "。请先到 Zotero 设置 → 高级 → 勾选「允许其他应用与本机 Zotero 通信」。")
    # 持久化 key（按 server_id 分区，避免换库后误用旧 key）
    keyfile = os.path.join(os.path.expanduser("~"), ".workbuddy", "zotero_local_key.json")
    cached = {}
    if os.path.isfile(keyfile):
        try:
            cached = json.load(open(keyfile, encoding="utf-8"))
        except Exception:
            cached = {}
    if cached.get("server_id") == sid and cached.get("key"):
        return sid, cached["key"]
    key, remember = _authorize_once(sid)
    if remember:
        os.makedirs(os.path.dirname(keyfile), exist_ok=True)
        json.dump({"server_id": sid, "key": key}, open(keyfile, "w", encoding="utf-8"))
    return sid, key


def _local_get(path, timeout=15):
    status, hdrs, body = _http("GET", path, timeout=timeout)
    if status != 200:
        raise ZoteroError("GET %s -> HTTP %d" % (path, status))
    return json.loads(body.decode("utf-8"))


def _local_write(method, path, server_id, key, body=None, retry=True):
    headers = {"Zotero-Server-ID": server_id, "Zotero-API-Key": key}
    if isinstance(body, (dict, list)):
        body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    status, hdrs, raw = _http(method, path, body=body, headers=headers)
    if status == 401 and retry:
        # 一次性 key 被消耗 → 重新授权一次再试
        key, _ = _authorize_once(server_id)
        headers["Zotero-API-Key"] = key
        status, hdrs, raw = _http(method, path, body=body, headers=headers)
    return status, hdrs, raw


# ─────────────────────────────── sqlite 兜底（Zotero 未运行时）───────────────────────────────
def _rw(timeout=20):
    try:
        con = sqlite3.connect(DB_PATH, timeout=timeout, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("BEGIN IMMEDIATE")
        return con
    except sqlite3.OperationalError as e:
        raise ZoteroError("Zotero 资料库被占用（%s）。Zotero 正在运行，但 Local API 不可用，"
                          "请勾选 Local API 设置或退出 Zotero 后重试。" % e)


def _ro():
    fd, path = tempfile.mkstemp(suffix=".sqlite", prefix="zot-")
    os.close(fd)
    shutil.copyfile(DB_PATH, path)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    return con


def _new_key(con):
    while True:
        k = "".join(KEYS[int(uuid.uuid4().hex[i:i + 2], 16) % 32] for i in range(0, 16, 2))
        if not con.execute("SELECT 1 FROM items WHERE key=?", (k,)).fetchone():
            return k


def _item_by_key_sql(con, key):
    r = con.execute("SELECT * FROM items WHERE key=?", (key,)).fetchone()
    if not r:
        raise ZoteroError("item not found: %s" % key)
    return r


# ─────────────────────────────── 读：Local API 优先，sqlite 兜底 ───────────────────────────────
def search_items(query, limit=20):
    if local_api_status()["ok"]:
        try:
            out = _local_get("users/0/items?q=%s&limit=%d&format=json" %
                             (urllib.request.quote(query), limit))
            rows = []
            for it in out:
                d = it.get("data", {})
                rows.append({
                    "key": d.get("key"), "type": d.get("itemType"),
                    "title": d.get("title"), "doi": d.get("DOI"),
                    "date": d.get("date"),
                })
            return [r for r in rows if r["key"]]
        except ZoteroError:
            pass
    # sqlite 兜底
    con = _ro()
    try:
        q = "%" + query + "%"
        rows = con.execute(
            "SELECT DISTINCT i.key, i.itemID FROM items i "
            "JOIN itemData d ON d.itemID=i.itemID "
            "JOIN itemDataValues v ON v.valueID=d.valueID "
            "WHERE v.value LIKE ? LIMIT ?", (q, limit * 3)).fetchall()
        out = []
        for r in rows:
            t = con.execute(
                "SELECT v.value FROM itemData d JOIN itemDataValues v ON v.valueID=d.valueID "
                "JOIN fields f ON f.fieldID=d.fieldID "
                "WHERE d.itemID=? AND f.fieldName='title'", (r["itemID"],)).fetchone()
            doi = con.execute(
                "SELECT v.value FROM itemData d JOIN itemDataValues v ON v.valueID=d.valueID "
                "JOIN fields f ON f.fieldID=d.fieldID "
                "WHERE d.itemID=? AND f.fieldName='DOI'", (r["itemID"],)).fetchone()
            out.append({"key": r["key"], "title": t["value"] if t else "",
                        "doi": doi["value"] if doi else ""})
        return out
    finally:
        con.close()


def _get_item_local(key):
    """通过 Local API 取条目（含附件/笔记清单）。"""
    it = _local_get("users/0/items/%s?format=json" % key)
    children = _local_get("users/0/items/%s/children?format=json" % key)
    return it, children


def get_item(key):
    if local_api_status()["ok"]:
        try:
            it, children = _get_item_local(key)
            d = it.get("data", {})
            out = {"key": key, "type": d.get("itemType"), "title": d.get("title"),
                   "doi": d.get("DOI"), "date": d.get("date"), "fields": d,
                   "attachments": [], "notes": []}
            for c in children:
                cd = c.get("data", {})
                if cd.get("itemType") == "attachment":
                    out["attachments"].append({"key": cd.get("key"),
                                               "contentType": cd.get("contentType"),
                                               "filename": cd.get("filename"),
                                               "title": cd.get("title")})
                elif cd.get("itemType") == "note":
                    out["notes"].append({"key": cd.get("key"), "title": cd.get("title")})
            return out
        except ZoteroError:
            pass
    # sqlite 兜底
    con = _ro()
    try:
        it = _item_by_key_sql(con, key)
        out = {"key": key, "type": "?", "fields": {}, "attachments": [], "notes": []}
        # 字段
        for r in con.execute(
                "SELECT f.fieldName, v.value FROM itemData d "
                "JOIN fields f ON f.fieldID=d.fieldID "
                "JOIN itemDataValues v ON v.valueID=d.valueID WHERE d.itemID=?",
                (it["itemID"],)):
            out["fields"][r["fieldName"]] = r["value"]
        out["title"] = out["fields"].get("title")
        out["doi"] = out["fields"].get("DOI")
        # 附件
        for r in con.execute(
                "SELECT i.key, a.contentType, a.path FROM itemAttachments a "
                "JOIN items i ON i.itemID=a.itemID WHERE a.parentItemID=?", (it["itemID"],)):
            out["attachments"].append({"key": r["key"], "contentType": r["contentType"],
                                       "filename": (r["path"] or "").split(":", 1)[-1]})
        # 笔记
        for r in con.execute(
                "SELECT i.key, n.title FROM itemNotes n JOIN items i ON i.itemID=n.itemID "
                "WHERE n.parentItemID=?", (it["itemID"],)):
            out["notes"].append({"key": r["key"], "title": r["title"]})
        return out
    finally:
        con.close()


# ─────────────────────────────── 写：Local API 优先 ───────────────────────────────
def attach_file(item_key, file_path, filename=None, title=None, replace=False):
    """挂回：Local API 优先，sqlite 兜底。"""
    if not os.path.isfile(file_path):
        raise ZoteroError("文件不存在：%s" % file_path)
    filename = filename or os.path.basename(file_path)

    # 1) Local API
    if local_api_status()["ok"]:
        return _attach_via_local_api(item_key, file_path, filename, title, replace)

    # 2) sqlite 直写（Zotero 未运行）—— 锁库时给出明确指引
    try:
        return _attach_via_sqlite(item_key, file_path, filename, title)
    except ZoteroError as e:
        raise ZoteroError(
            "%s\n\n无法写回 Zotero。两条路任选其一：\n"
            "  A) 到 Zotero 设置 → 高级 → 勾选「允许其他应用与本机 Zotero 通信」，"
            "再重跑本命令（会弹一次授权，选 Always Allow）。\n"
            "  B) 彻底退出 Zotero，再重跑本命令（走 sqlite 直写）。" % e)


def _attach_via_local_api(item_key, file_path, filename, title, replace):
    md5 = hashlib.md5(open(file_path, "rb").read()).hexdigest()
    fsize = os.path.getsize(file_path)
    mtime = int(os.path.getmtime(file_path) * 1000)
    sid, key = _authorize()

    # 定位目标附件（替换）或决定新建
    target_key, old_md5 = None, None
    if replace:
        try:
            _, children = _get_item_local(item_key)
            for c in children:
                cd = c.get("data", {})
                if cd.get("itemType") == "attachment" and cd.get("filename") == filename:
                    target_key, old_md5 = cd.get("key"), cd.get("md5")
                    break
        except ZoteroError:
            pass

    attach_key = target_key
    if not target_key:
        # 新建 attachment item
        status, hdrs, raw = _local_write("POST", "users/0/items", sid, key,
                                         body=[{"itemType": "attachment",
                                                "parentItem": item_key,
                                                "linkMode": "imported_file",
                                                "title": (title or filename),
                                                "contentType": CONTENT_TYPES.get(
                                                    os.path.splitext(filename)[1].lower(),
                                                    "application/octet-stream"),
                                                "filename": filename}])
        if status not in (200, 204):
            raise ZoteroError("建附件失败 HTTP %d：%s" % (status, raw[:300]))
        try:
            obj = json.loads(raw.decode("utf-8"))
            if isinstance(obj, dict) and obj.get("successful"):
                attach_key = obj["successful"][0]["key"]
            elif isinstance(obj, list):
                attach_key = obj[0]["key"]
            else:
                attach_key = obj["key"]
        except Exception:
            raise ZoteroError("解析新附件 key 失败：%s" % raw[:300])

    # 阶段①：POST /file 拿 uploadKey（参数在 form body，不是 query string，否则 Zotero 10 返回裸 500）
    import urllib.parse as _up
    form1 = _up.urlencode({"md5": md5, "filename": filename, "filesize": fsize,
                           "mtime": mtime}).encode("utf-8")
    hdr1 = {"Zotero-Server-ID": sid, "Zotero-API-Key": key,
            "If-Match" if target_key else "If-None-Match": old_md5 or "*",
            "Content-Type": "application/x-www-form-urlencoded"}
    status, hdrs, raw = _http("POST", "users/0/items/%s/file" % attach_key,
                              body=form1, headers=hdr1)
    if status == 401:
        key, _ = _authorize_once(sid)
        hdr1["Zotero-API-Key"] = key
        status, hdrs, raw = _http("POST", "users/0/items/%s/file" % attach_key,
                                  body=form1, headers=hdr1)
    if status != 200:
        raise ZoteroError("阶段①授权上传失败 HTTP %d：%s" % (status, raw[:300]))
    auth = json.loads(raw.decode("utf-8"))
    if auth.get("exists") == 1:
        return {"ok": True, "via": "local-api", "attachment_key": attach_key,
                "parent": item_key, "title": title or filename, "skipped": "exists"}

    # 阶段②：上传字节
    up_url = auth["url"]
    if up_url.startswith("/"):
        up_url = "http://127.0.0.1:23119" + up_url
    data = open(file_path, "rb").read()
    req = urllib.request.Request(up_url, data=data,
                                 headers={"Content-Type": "application/octet-stream"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            if r.status != 201:
                raise ZoteroError("阶段②上传失败 HTTP %d" % r.status)
    except urllib.error.HTTPError as e:
        raise ZoteroError("阶段②上传失败 HTTP %d：%s" % (e.code, e.read()[:300]))

    # 阶段③：登记 uploadKey（upload 参数同样放 form body）
    form3 = _up.urlencode({"upload": auth["uploadKey"]}).encode("utf-8")
    hdr3 = {"Zotero-Server-ID": sid, "Zotero-API-Key": key,
            "If-Match" if target_key else "If-None-Match": old_md5 or "*",
            "Content-Type": "application/x-www-form-urlencoded"}
    status, hdrs, raw = _http("POST", "users/0/items/%s/file" % attach_key,
                              body=form3, headers=hdr3)
    if status not in (200, 204):
        raise ZoteroError("阶段③登记失败 HTTP %d：%s" % (status, raw[:300]))
    return {"ok": True, "via": "local-api", "attachment_key": attach_key,
            "parent": item_key, "title": title or filename, "replaced": bool(target_key)}


def _attach_via_sqlite(item_key, file_path, filename, title):
    ext = os.path.splitext(file_path)[1].lower()
    con = _rw()
    try:
        parent = _item_by_key_sql(con, item_key)
        # 幂等：同名同大小 → 跳过
        for r in con.execute(
                "SELECT i.key FROM itemAttachments a JOIN items i ON i.itemID=a.itemID "
                "WHERE a.parentItemID=? AND a.path=?", (parent["itemID"], "storage:" + filename)):
            disk = os.path.join(STORAGE, r["key"], filename)
            if os.path.exists(disk) and os.path.getsize(disk) == os.path.getsize(file_path):
                con.execute("ROLLBACK")
                return {"ok": True, "skipped": "已存在同名同大小附件", "attachment_key": r["key"],
                        "parent": item_key}
        key = _new_key(con)
        item_id = con.execute("INSERT INTO items (itemTypeID, libraryID, key, dateAdded, dateModified, "
                              "clientDateModified, clientDateAdded) VALUES (?,?,?,?,?,?,?)",
                              (TYPE_ATTACHMENT, parent["libraryID"], key,
                               int(time.time() * 1000), int(time.time() * 1000),
                               int(time.time() * 1000), int(time.time() * 1000))).lastrowid
        dest_dir = os.path.join(STORAGE, key)
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, filename)
        shutil.copyfile(file_path, dest)
        md5 = hashlib.md5(open(dest, "rb").read()).hexdigest()
        mtime = int(os.path.getmtime(dest) * 1000)
        con.execute(
            "INSERT INTO itemAttachments (itemID, parentItemID, linkMode, contentType, charsetID, path, "
            "syncState, storageModTime, storageHash) VALUES (?,?,0,?,NULL,?,0,?,?)",
            (item_id, parent["itemID"], CONTENT_TYPES.get(ext, "application/octet-stream"),
             "storage:" + filename, mtime, md5))
        title = (title or filename).strip()
        fid = con.execute("SELECT fieldID FROM fields WHERE fieldName='title'").fetchone()
        if title and fid:
            con.execute("INSERT OR IGNORE INTO itemDataValues (value) VALUES (?)", (title,))
            vid = con.execute("SELECT valueID FROM itemDataValues WHERE value=?", (title,)).fetchone()[0]
            con.execute("INSERT OR REPLACE INTO itemData (itemID, fieldID, valueID) VALUES (?,?,?)",
                        (item_id, fid["fieldID"], vid))
        con.execute("COMMIT")
        return {"ok": True, "attachment_key": key, "parent": item_key, "file": dest,
                "title": title}
    except Exception:
        try:
            con.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        con.close()


def create_note(item_key, title, text):
    """建笔记：Local API 优先，sqlite 兜底。"""
    if local_api_status()["ok"]:
        sid, key = _authorize()
        note_html = _wrap_note(_md_to_html(text))
        body = [{"itemType": "note", "parentItem": item_key,
                 "note": note_html, "title": title or ""}]
        status, hdrs, raw = _local_write("POST", "users/0/items", sid, key, body=body)
        if status not in (200, 204):
            raise ZoteroError("建笔记失败 HTTP %d：%s" % (status, raw[:300]))
        try:
            obj = json.loads(raw.decode("utf-8"))
            nk = obj["successful"][0]["key"] if isinstance(obj, dict) else obj[0]["key"]
        except Exception:
            nk = None
        return {"ok": True, "via": "local-api", "note_key": nk, "parent": item_key,
                "title": title}
    try:
        con = _rw()
    except ZoteroError as e:
        raise ZoteroError(
            "%s\n\n无法写回 Zotero。两条路任选其一：\n"
            "  A) 到 Zotero 设置 → 高级 → 勾选「允许其他应用与本机 Zotero 通信」，"
            "再重跑本命令（会弹一次授权，选 Always Allow）。\n"
            "  B) 彻底退出 Zotero，再重跑本命令（走 sqlite 直写）。" % e)
    try:
        parent = _item_by_key_sql(con, item_key)
        key = _new_key(con)
        item_id = con.execute("INSERT INTO items (itemTypeID, libraryID, key, dateAdded, dateModified, "
                              "clientDateModified, clientDateAdded) VALUES (?,?,?,?,?,?,?)",
                              (TYPE_NOTE, parent["libraryID"], key,
                               int(time.time() * 1000), int(time.time() * 1000),
                               int(time.time() * 1000), int(time.time() * 1000))).lastrowid
        con.execute("INSERT INTO itemNotes (itemID, parentItemID, note, title) VALUES (?,?,?,?)",
                    (item_id, parent["itemID"], _wrap_note(_md_to_html(text)), title or ""))
        con.execute("COMMIT")
        return {"ok": True, "note_key": key, "parent": item_key, "title": title}
    except Exception:
        try:
            con.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        con.close()


def _wrap_note(body):
    return '<div class="zotero-note znv1">%s</div>' % body


def _md_to_html(md):
    """够用的 Markdown 子集（同 zotero_mcp.py）。"""
    lines = (md or "").replace("\r\n", "\n").split("\n")
    out, i = [], 0
    def esc(s):
        s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"`(.+?)`", r"<code>\1</code>", s)
        return s
    while i < len(lines):
        ln = lines[i]
        s = ln.strip()
        if not s:
            i += 1
            continue
        if s.startswith("|") and i + 1 < len(lines) and re.match(r"^\|[\s:|-]+\|$", lines[i + 1].strip()):
            def cells(line):
                return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", line.strip().strip("|"))]
            head = cells(s)
            i += 2
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(cells(lines[i])); i += 1
            out.append("<table><thead><tr>" + "".join("<th>%s</th>" % esc(c) for c in head) +
                       "</tr></thead><tbody>" + "".join("<tr>" + "".join("<td>%s</td>" % esc(c) for c in r) +
                                                        "</tr>" for r in rows) + "</tbody></table>")
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", s)
        if m:
            lvl = min(len(m.group(1)) + 1, 6)
            out.append("<h%d>%s</h%d>" % (lvl, esc(m.group(2)), lvl)); i += 1; continue
        if s.startswith(("- ", "* ", "• ")):
            items = []
            while i < len(lines) and lines[i].strip().startswith(("- ", "* ", "• ")):
                items.append("<li>%s</li>" % esc(lines[i].strip()[2:].strip())); i += 1
            out.append("<ul>%s</ul>" % "".join(items)); continue
        out.append("<p>%s</p>" % esc(s)); i += 1
    return "\n".join(out)


# ─────────────────────────────── CLI ───────────────────────────────
def _status():
    la = local_api_status()
    sql_ok = os.path.isfile(DB_PATH)
    br = {"ok": False}
    try:
        import urllib.request as _u
        req = _u.Request(BRIDGE_URL, data=json.dumps({"op": "ping"}).encode(),
                         headers={"Content-Type": "application/json"}, method="POST")
        with _u.urlopen(req, timeout=3) as r:
            br = {"ok": True, "resp": r.read().decode()[:200]}
    except Exception as e:
        br = {"ok": False, "error": str(e)[:120]}
    return {"local_api": la, "sqlite_exists": sql_ok, "bridge": br,
            "db": DB_PATH}


def main():
    ap = argparse.ArgumentParser(description="Zotero 挂回（Local API 优先）")
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("status")
    sub.add_parser("authorize")
    p = sub.add_parser("search"); p.add_argument("query"); p.add_argument("--limit", type=int, default=20)
    p = sub.add_parser("item"); p.add_argument("key")
    p = sub.add_parser("attach"); p.add_argument("key"); p.add_argument("file")
    p.add_argument("--title"); p.add_argument("--filename"); p.add_argument("--replace", action="store_true")
    p = sub.add_parser("note"); p.add_argument("key"); p.add_argument("--title"); p.add_argument("--text-file")

    a = ap.parse_args()
    try:
        if a.cmd == "status":
            print(json.dumps(_status(), ensure_ascii=False, indent=2)); return 0
        if a.cmd == "authorize":
            sid, key = _authorize()
            print(json.dumps({"ok": True, "server_id": sid, "key_len": len(key)}, ensure_ascii=False, indent=2))
            return 0
        if a.cmd == "search":
            print(json.dumps(search_items(a.query, a.limit), ensure_ascii=False, indent=2)); return 0
        if a.cmd == "item":
            print(json.dumps(get_item(a.key), ensure_ascii=False, indent=2)); return 0
        if a.cmd == "attach":
            print(json.dumps(attach_file(a.key, a.file, a.filename, a.title, a.replace),
                             ensure_ascii=False, indent=2)); return 0
        if a.cmd == "note":
            body = open(a.text_file, encoding="utf-8").read() if a.text_file else sys.stdin.read()
            print(json.dumps(create_note(a.key, a.title or "", body), ensure_ascii=False, indent=2)); return 0
    except ZoteroError as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False, indent=2))
        return 1
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
