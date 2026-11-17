from __future__ import annotations

import argparse
import json
import os
import time
import traceback
from pathlib import Path
from typing import Any

import psutil

from harness.catalog import CASES, scenarioFor
from harness.reports import writeReports


def selectCases(
    groups: list[str] | None, names: list[str] | None
) -> list[dict[str, str]]:
    known_groups = {case['group'] for case in CASES}
    known_names = {case['id'] for case in CASES}
    if set(groups or []) - known_groups:
        raise ValueError(
            'Unknown groups: ' + ', '.join(set(groups or []) - known_groups)
        )
    if set(names or []) - known_names:
        raise ValueError('Unknown cases: ' + ', '.join(set(names or []) - known_names))
    if names:
        return [case for case in CASES if case['id'] in names]
    return [case for case in CASES if case['group'] in (groups or ['core'])]


def sourceAppRunning(root: Path) -> bool:
    target = str(root.resolve()).lower()
    for process in psutil.process_iter(['pid', 'cmdline', 'cwd']):
        if process.pid == os.getpid():
            continue
        try:
            cwd = process.cwd()
            command = process.cmdline()
        except (psutil.Error, OSError):
            continue
        if cwd.lower() != target:
            continue
        if any(
            argument == 'main' or argument.endswith('main.py') for argument in command
        ):
            return True
    return False


def checkGateway(case_id: str, gateway: Any) -> None:
    if gateway.errors:
        raise AssertionError('\n'.join(gateway.errors))
    if not case_id.startswith('llm_'):
        return
    bodies = [
        request['body']
        for request in gateway.requests
        if request['path'] != '/v1/models'
    ]
    assert bodies, 'Assistant did not contact gateway'
    outputs = []
    for body in bodies:
        for message in body.get('messages', []):
            if message.get('role') == 'tool':
                outputs.append(str(message.get('content', '')))
        for message in body.get('input', []):
            if (
                isinstance(message, dict)
                and message.get('type') == 'function_call_output'
            ):
                outputs.append(str(message.get('output', '')))
    combined = '\n'.join(outputs)
    required = {
        'llm_explain_code': ['AppContext', 'addScheduledTask'],
        'llm_responses_tools': ['AppContext', 'addScheduledTask'],
        'llm_search': ['90001', '90036'],
        'llm_no_results': ['"count": 0'],
        'llm_organize': ['Harness Mix', '90002'],
        'llm_usage_guard': ['requires_tool_usage'],
        'llm_unknown_tool': ['Unknown tool'],
        'llm_lyrics': ['Harness edited'],
    }.get(case_id, [])
    for fragment in required:
        assert fragment in combined, (
            f'Missing tool result {fragment!r}: {combined[:2000]}'
        )
    if case_id == 'llm_read_boundary':
        payloads = [json.loads(output) for output in outputs]
        assert any(payload.get('path') == 'src' for payload in payloads)
        assert all(
            'config.json' not in payload.get('items', []) for payload in payloads
        )
    if case_id == 'llm_history':
        assert len(bodies) == 3
        assert any(
            message.get('content') == 'First answer.'
            for message in bodies[1]['messages']
        )
        assert not any(
            message.get('content') == 'First answer.'
            for message in bodies[2]['messages']
        )


def runSuite(root: Path, directory: Path, cases: list[dict[str, str]]) -> int:
    from harness.environment import userFingerprint
    from harness.gateway import Gateway
    from harness.session import ApplicationSession

    results: list[dict[str, Any]] = []
    source_in_use = sourceAppRunning(root)
    before = userFingerprint(root)
    session = None
    gateway = None
    mode = None
    index = 0
    try:
        for case in cases:
            started = time.monotonic()
            result: dict[str, Any] = {**case, 'status': 'passed', 'detail': ''}
            print('RUN ' + case['id'], flush=True)
            try:
                native = case['group'] == 'native'
                if session is None or mode != native:
                    if session:
                        session.close()
                        session = None
                    if gateway:
                        gateway.stop()
                    location = directory / 'sessions' / str(index)
                    index += 1
                    gateway = Gateway(location / 'gateway')
                    url = gateway.start()
                    session = ApplicationSession(root, url, location, native=native)
                    mode = native
                assert gateway is not None
                gateway.setScenario(scenarioFor(case['id']))
                state = session.request('run_case', case=case['id'])
                if isinstance(state, dict) and 'skipped' in state:
                    result.update(status='skipped', detail=state['skipped'])
                else:
                    checkGateway(case['id'], gateway)
                result['artifacts'] = str(session.output.relative_to(directory))
            except Exception:
                result.update(status='failed', detail=traceback.format_exc())
                if session:
                    session.close()
                    session = None
            result['duration'] = time.monotonic() - started
            results.append(result)
            print(
                result['status'].upper()
                + ' '
                + case['id']
                + (
                    ': ' + result['detail'].splitlines()[-1] if result['detail'] else ''
                ),
                flush=True,
            )
            writeReports(directory, results)
    finally:
        if session:
            try:
                session.close()
            except Exception:
                results.append({
                    'id': 'cleanup',
                    'group': 'harness',
                    'status': 'failed',
                    'duration': 0,
                    'detail': traceback.format_exc(),
                })
        if gateway:
            gateway.stop()
        after = userFingerprint(root)
        if before != after:
            changed = sorted(
                key
                for key in set(before) | set(after)
                if before.get(key) != after.get(key)
            )
            if source_in_use or sourceAppRunning(root):
                results.append({
                    'id': 'user_data_isolation',
                    'group': 'harness',
                    'status': 'skipped',
                    'duration': 0,
                    'detail': 'Source workspace is in use by a running app: '
                    + ', '.join(changed),
                })
            else:
                results.append({
                    'id': 'user_data_isolation',
                    'group': 'harness',
                    'status': 'failed',
                    'duration': 0,
                    'detail': 'Source workspace config/favorites/data changed: '
                    + ', '.join(changed),
                })
        writeReports(directory, results)
    return int(any(result['status'] == 'failed' for result in results))


def main() -> int:
    parser = argparse.ArgumentParser(
        description='SouthsideMusic real application harness'
    )
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('prepare-worker')
    sub.add_parser('list')
    app = sub.add_parser('_app')
    for option in ('root', 'address', 'token', 'media-url', 'report-dir'):
        app.add_argument('--' + option, required=True)
    app.add_argument('--native', action='store_true')
    unit = sub.add_parser('unit')
    unit.add_argument('--report-dir', default='.harness-results/unit')
    run = sub.add_parser('run')
    run.add_argument('--group', action='append')
    run.add_argument('--case', action='append')
    run.add_argument('--report-dir', default='.harness-results/run')
    manual = sub.add_parser('manual')
    manual.add_argument('--report-dir', default='.harness-results/manual')
    manual.add_argument('--smoke', action='store_true')
    gateway_parser = sub.add_parser('gateway')
    gateway_parser.add_argument('--scenario', type=Path)
    gateway_parser.add_argument('--report-dir', default='.harness-results/gateway')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    if args.command == '_app':
        from harness.application import runApplication

        runApplication(
            Path(args.root),
            args.address,
            args.token,
            args.media_url,
            Path(args.report_dir),
            args.native,
        )
        return 0
    if args.command == 'list':
        for case in CASES:
            print(f'{case["group"]:8} {case["id"]:40} {case["description"]}')
        return 0
    if args.command == 'prepare-worker':
        from harness.environment import prepareWorker

        print(prepareWorker(root))
        return 0
    if args.command == 'unit':
        from harness.unit_runner import runUnitSuite

        return runUnitSuite(root, Path(args.report_dir).resolve())
    if args.command == 'manual':
        from harness.manual import runManual

        return runManual(root, Path(args.report_dir).resolve(), args.smoke)
    if args.command == 'gateway':
        from harness.gateway import Gateway

        gateway = Gateway(Path(args.report_dir).resolve())
        gateway.setScenario(
            json.loads(args.scenario.read_text(encoding='utf-8'))
            if args.scenario
            else {'rounds': [{'text': '# Local gateway\n\nSouthsideMusic harness.'}]}
        )
        try:
            print(gateway.start(), flush=True)
            while True:
                time.sleep(0.2)
        except KeyboardInterrupt:
            return 0
        finally:
            gateway.stop()
    try:
        cases = selectCases(args.group, args.case)
    except ValueError as error:
        parser.error(str(error))
    return runSuite(root, Path(args.report_dir).resolve(), cases)
