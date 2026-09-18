from __future__ import annotations

import argparse
import os
import threading
import time

import frida


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--pid', type=int, default=0)
    ap.add_argument('--name', default='cloudmusic.exe')
    ap.add_argument('--script', default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ncm_capture.js'))
    ap.add_argument('--seconds', type=float, default=60.0)
    ap.add_argument('--guard', type=float, default=20.0)
    ap.add_argument('--watch', action='store_true')
    ap.add_argument('--log-dir', default=os.path.dirname(os.path.abspath(__file__)))
    args = ap.parse_args()

    with open(args.script, encoding='utf-8') as f:
        code = f.read()

    sessions: dict[int, object] = {}

    def attach(pid: int) -> None:
        if pid in sessions:
            return
        log_path = os.path.join(args.log_dir, 'capture.%d.jsonl' % pid)
        src = code.replace('__LOG_PATH__', log_path.replace('\\', '/'))
        try:
            session = frida.attach(pid)
            script = session.create_script(src)
        except Exception as exc:
            print('[attach-fail] pid=%d %s' % (pid, exc), flush=True)
            return

        def on_message(message, data, _pid=pid):
            if message.get('type') == 'error':
                print('[script-error] pid=%d %s' % (_pid, message.get('description')), flush=True)

        script.on('message', on_message)
        script.load()
        sessions[pid] = session
        print('[attached] pid=%d log=%s' % (pid, log_path), flush=True)

        def on_detached(reason=None, *extra, _pid=pid):
            print('[detached] pid=%d reason=%s' % (_pid, reason), flush=True)
            sessions.pop(_pid, None)

        session.on('detached', on_detached)

    if args.watch:
        print('watch mode: waiting for %s (no timeout, Ctrl+C / kill to stop)' % args.name, flush=True)
        while True:
            try:
                for proc in frida.get_local_device().enumerate_processes():
                    if proc.name == args.name:
                        attach(proc.pid)
            except Exception as exc:
                print('[enumerate-fail] %s' % exc, flush=True)
            time.sleep(2)

    target = args.pid if args.pid else args.name
    guard = threading.Timer(args.guard, lambda: os._exit(3))
    guard.daemon = True
    guard.start()
    try:
        session = frida.attach(target)
    except Exception:
        guard.cancel()
        raise
    pid_for_log = args.pid or (session.pid if hasattr(session, 'pid') else 0)
    log_path = os.path.join(args.log_dir, 'capture.%d.jsonl' % pid_for_log)
    script = session.create_script(code.replace('__LOG_PATH__', log_path.replace('\\', '/')))

    def on_message(message, data) -> None:
        if message.get('type') == 'error':
            print('[script-error] %s' % message.get('description'), flush=True)

    script.on('message', on_message)
    script.load()
    guard.cancel()
    print('capture running for %.0fs on %s -> %s' % (args.seconds, target, log_path), flush=True)
    try:
        time.sleep(args.seconds)
    except KeyboardInterrupt:
        pass
    session.detach()


if __name__ == '__main__':
    main()
