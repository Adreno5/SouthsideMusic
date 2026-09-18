'use strict';

const LOG = '__LOG_PATH__';
const MAXBODY = 400000;
const MAXRESP = 800000;

let logFile = null;

function emit(obj) {
    try {
        if (logFile === null) {
            logFile = new File(LOG, 'a');
        }
        logFile.write(JSON.stringify(obj) + '\n');
        logFile.flush();
    } catch (e) {
        try {
            console.log('[emit-fail] ' + e);
        } catch (e2) {}
    }
}

function resolve(modName, expName) {
    try {
        if (typeof Module.findGlobalExportByName === 'function') {
            const a = Module.findGlobalExportByName(expName);
            if (a !== null) {
                return a;
            }
        }
    } catch (e) {}
    try {
        const m = Process.getModuleByName(modName);
        const b = m.findExportByName(expName);
        if (b !== null) {
            return b;
        }
    } catch (e) {}
    try {
        const c = Module.getExportByName(modName, expName);
        if (c !== null) {
            return c;
        }
    } catch (e) {}
    return null;
}

function readSlist(p) {
    const out = [];
    try {
        let node = p;
        for (let i = 0; i < 200; i++) {
            if (node.isNull()) {
                break;
            }
            const data = node.add(Process.pointerSize).readPointer();
            if (!data.isNull()) {
                out.push(data.readUtf8String());
            }
            node = node.readPointer();
        }
    } catch (e) {}
    return out;
}

const OPT = {
    WRITEDATA: 10001,
    URL: 10002,
    POSTFIELDS: 10015,
    COOKIE: 10022,
    HTTPHEADER: 10023,
    CUSTOMREQUEST: 10036,
    POST: 10047,
    WRITEFUNCTION: 20011,
};

const handles = new Map();
const wdToHandle = new Map();
const hookedWrite = new Set();
const respBuf = new Map();

function state(h) {
    let rec = handles.get(h);
    if (rec === undefined) {
        rec = { id: h, headers: [] };
        handles.set(h, rec);
    }
    return rec;
}

function hookWriteCallback(addr) {
    const key = addr.toString();
    if (hookedWrite.has(key)) {
        return;
    }
    hookedWrite.add(key);
    try {
        Interceptor.attach(addr, {
            onEnter: function (args) {
                try {
                    const userdata = args[3].toString();
                    const h = wdToHandle.get(userdata);
                    if (h === undefined) {
                        return;
                    }
                    const n = args[1].toInt32() * args[2].toInt32();
                    if (n <= 0) {
                        return;
                    }
                    let cur = respBuf.get(h);
                    if (cur === undefined) {
                        cur = [];
                        respBuf.set(h, cur);
                    }
                    if (cur.length < MAXRESP) {
                        const bytes = new Uint8Array(args[0].readByteArray(n));
                        for (let i = 0; i < bytes.length; i++) {
                            cur.push(bytes[i]);
                        }
                    }
                } catch (e) {}
            },
        });
    } catch (e) {
        emit({ ev: 'hook-write-fail', addr: key, err: String(e) });
    }
}

function toHex(arr) {
    let out = '';
    for (let i = 0; i < arr.length; i++) {
        out += ('0' + arr[i].toString(16)).slice(-2);
    }
    return out;
}

const setopt = resolve('libcurl.dll', 'curl_easy_setopt');
if (setopt !== null) {
    Interceptor.attach(setopt, {
        onEnter: function (args) {
            let opt = -1;
            try {
                opt = args[1].toInt32();
            } catch (e) {
                return;
            }
            const h = args[0].toString();
            try {
                if (opt === OPT.URL) {
                    state(h).url = args[2].readUtf8String();
                } else if (opt === OPT.POSTFIELDS) {
                    const p = args[2];
                    if (!p.isNull()) {
                        state(h).body = p.readUtf8String().slice(0, MAXBODY);
                    }
                } else if (opt === OPT.COOKIE) {
                    state(h).cookie = args[2].readUtf8String();
                } else if (opt === OPT.HTTPHEADER) {
                    state(h).headers = readSlist(args[2]);
                } else if (opt === OPT.CUSTOMREQUEST) {
                    state(h).method = args[2].readUtf8String();
                } else if (opt === OPT.POST) {
                    state(h).isPost = args[2].toInt32() !== 0;
                } else if (opt === OPT.WRITEDATA) {
                    wdToHandle.set(args[2].toString(), h);
                } else if (opt === OPT.WRITEFUNCTION) {
                    hookWriteCallback(args[2]);
                }
            } catch (e) {}
        },
    });
    emit({ ev: 'installed', target: 'curl_easy_setopt', addr: setopt.toString() });
} else {
    emit({ ev: 'install-fail', target: 'curl_easy_setopt' });
}

function flush(ev, h) {
    const rec = handles.get(h);
    if (rec === undefined || rec.url === undefined) {
        return;
    }
    const out = {
        ev: ev,
        ts: Date.now(),
        url: rec.url,
        method: rec.method !== undefined ? rec.method : (rec.isPost === true ? 'POST' : 'GET'),
        headers: rec.headers,
        cookie: rec.cookie !== undefined ? rec.cookie : null,
        body: rec.body !== undefined ? rec.body : null,
    };
    const chunks = respBuf.get(h);
    if (chunks !== undefined && chunks.length > 0) {
        out.responseBytes = chunks.length;
        out.responseHex = toHex(chunks);
    }
    emit(out);
}

const fwritePtr = resolve(null, 'fwrite');

if (fwritePtr !== null) {
    try {
        Interceptor.attach(fwritePtr, {
            onEnter: function (args) {
                try {
                    const stream = args[3].toString();
                    const h = wdToHandle.get(stream);
                    if (h === undefined) {
                        return;
                    }
                    const n = args[1].toInt32() * args[2].toInt32();
                    if (n <= 0) {
                        return;
                    }
                    let cur = respBuf.get(h);
                    if (cur === undefined) {
                        cur = [];
                        respBuf.set(h, cur);
                    }
                    if (cur.length < MAXRESP) {
                        const bytes = new Uint8Array(args[0].readByteArray(n));
                        for (let i = 0; i < bytes.length; i++) {
                            cur.push(bytes[i]);
                        }
                    }
                } catch (e) {}
            },
        });
        emit({ ev: 'installed', target: 'fwrite', addr: fwritePtr.toString() });
    } catch (e) {
        emit({ ev: 'install-fail', target: 'fwrite' });
    }
}

const perform = resolve('libcurl.dll', 'curl_easy_perform');
if (perform !== null) {
    Interceptor.attach(perform, {
        onEnter: function (args) {
            this.h = args[0].toString();
            respBuf.set(this.h, []);
        },
        onLeave: function () {
            flush('perform', this.h);
            handles.delete(this.h);
            respBuf.delete(this.h);
        },
    });
}

const addHandle = resolve('libcurl.dll', 'curl_multi_add_handle');
if (addHandle !== null) {
    Interceptor.attach(addHandle, {
        onEnter: function (args) {
            this.h = args[1].toString();
            respBuf.set(this.h, []);
        },
    });
}

const removeHandle = resolve('libcurl.dll', 'curl_multi_remove_handle');
if (removeHandle !== null) {
    Interceptor.attach(removeHandle, {
        onEnter: function (args) {
            this.h = args[1].toString();
        },
        onLeave: function () {
            flush('multi_remove', this.h);
            handles.delete(this.h);
            respBuf.delete(this.h);
        },
    });
}

emit({ ev: 'ready', pid: Process.id, arch: Process.arch });
