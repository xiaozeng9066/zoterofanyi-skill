/* DSH Bridge —— Zotero 7 插件
 *
 * 作用：在 Zotero 自带的本地 HTTP 服务（127.0.0.1:23119）上注册 /dsh/bridge 端点，
 * 让外部工具（zoterofanyi 的 zotero_mcp.py）在 Zotero 运行期间也能写入：
 *   POST /dsh/bridge  {"op":"ping"}
 *   POST /dsh/bridge  {"op":"attach","itemKey":"XXXX","file":"C:/path/a.pdf","title":"中英对照"}
 *   POST /dsh/bridge  {"op":"note","itemKey":"XXXX","title":"翻译项目备注","html":"<p>...</p>"}
 *
 * 设计要点：
 * - 不用直接写 zotero.sqlite（Zotero 长期持有写锁，外部写入会被拒/损坏库）；
 *   走 Zotero 自己的数据层 API，条目树、同步状态、全文索引都会正常更新。
 * - 任何异常都返回 JSON 错误，不抛到 Zotero 主线程，避免插件把软件搞崩。
 */

var DSHBridge = {
	id: "dsh-bridge@zengf.local",
	version: "1.0.0",
	rootURI: null,
	path: "/dsh/bridge",
};

function log(msg) {
	try { Zotero.debug("[DSH Bridge] " + msg); } catch (e) {}
}

function ok(obj) {
	return [200, "application/json", JSON.stringify(Object.assign({ ok: true }, obj || {}))];
}

function fail(msg, code) {
	return [code || 400, "application/json", JSON.stringify({ ok: false, error: String(msg) })];
}

/* 在所有 library 里按 key 找条目（个人库优先） */
function findItem(key) {
	if (!key) return null;
	try {
		let libs = [Zotero.Libraries.userLibraryID];
		for (let lib of Zotero.Libraries.getAll()) {
			if (lib.libraryID !== Zotero.Libraries.userLibraryID) libs.push(lib.libraryID);
		}
		for (let lid of libs) {
			let it = Zotero.Items.getByLibraryAndKey(lid, key);
			if (it) return it;
		}
	}
	catch (e) { log("findItem failed: " + e); }
	return null;
}

async function opPing() {
	return ok({ version: DSHBridge.version, zotero: Zotero.version });
}

async function opAttach(req) {
	let item = findItem(req.itemKey);
	if (!item) return fail("item not found: " + req.itemKey, 404);
	if (!req.file) return fail("missing 'file'");
	if (!(await IOUtils.exists(req.file))) return fail("file not found: " + req.file, 404);

	let title = (req.title || "").trim();
	// 幂等：同一父条目下已存在同名附件则直接返回，避免重复挂接
	try {
		let existing = item.getAttachments();
		for (let id of existing) {
			let a = Zotero.Items.get(id);
			if (!a) continue;
			let p = a.getFilePath && a.getFilePath();
			if (p && PathUtils.filename(p) === (req.filename || PathUtils.filename(req.file))) {
				if (title && a.getField("title") !== title) { a.setField("title", title); await a.saveTx(); }
				return ok({ attachmentKey: a.key, title: a.getField("title"), skipped: "already attached" });
			}
		}
	}
	catch (e) { log("idempotency check failed (ignored): " + e); }

	let att = await Zotero.Attachments.importFromFile({
		file: req.file,
		parentItemID: item.id,
		libraryID: item.libraryID,
	});
	if (title) {
		att.setField("title", title);
		await att.saveTx();
	}
	let got = att.getField("title");
	if (!got) return fail("attachment title ended up empty (would show blank in Zotero)", 500);
	log("attached " + att.key + " -> " + req.itemKey);
	return ok({ attachmentKey: att.key, title: got, parent: req.itemKey });
}

async function opNote(req) {
	let item = findItem(req.itemKey);
	if (!item) return fail("item not found: " + req.itemKey, 404);
	let html = req.html || "";
	if (!html.trim()) return fail("empty note");
	let title = (req.title || "").trim();
	// Zotero 笔记没有独立 title 字段，标题靠正文首行呈现；首行没有就补一个 h1
	if (title && html.indexOf("<h1") === -1) {
		html = "<h1>" + title.replace(/[&<>]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c])) + "</h1>" + html;
	}
	let note = new Zotero.Item("note");
	note.libraryID = item.libraryID;
	note.parentID = item.id;
	note.setNote(html);
	await note.saveTx();
	log("noted " + note.key + " -> " + req.itemKey);
	return ok({ noteKey: note.key, parent: req.itemKey, title: title });
}

async function opDelete(req) {
	let item = findItem(req.key);
	if (!item) return fail("item not found: " + req.key, 404);
	await item.eraseTx();
	return ok({ deleted: req.key });
}

function register() {
	Zotero.Server.Endpoints[DSHBridge.path] = function () {};
	Zotero.Server.Endpoints[DSHBridge.path].prototype = {
		supportedMethods: ["POST"],
		init: async function (options) {
			try {
				let raw = options.data || "{}";
				if (typeof raw !== "string") raw = JSON.stringify(raw);
				let req = JSON.parse(raw);
				switch (req.op) {
					case "ping": return await opPing();
					case "attach": return await opAttach(req);
					case "note": return await opNote(req);
					case "delete": return await opDelete(req);
					default: return fail("unknown op: " + req.op, 400);
				}
			}
			catch (e) {
				log("handler error: " + (e && e.stack || e));
				return fail((e && e.message) || e, 500);
			}
		},
	};
	log("endpoint registered at " + DSHBridge.path);
}

function unregister() {
	try { delete Zotero.Server.Endpoints[DSHBridge.path]; } catch (e) {}
}

function install() {}
function uninstall() {}

function startup({ id, version, rootURI }, reason) {
	DSHBridge.id = id || DSHBridge.id;
	DSHBridge.version = version || DSHBridge.version;
	DSHBridge.rootURI = rootURI || null;
	register();
}

function shutdown(data, reason) {
	unregister();
}
