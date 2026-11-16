from __future__ import annotations

import html
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


def writeReports(directory: Path, results: list[dict[str, Any]]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    counts = {
        status: sum(result['status'] == status for result in results)
        for status in ('passed', 'failed', 'skipped')
    }
    (directory / 'results.json').write_text(
        json.dumps({'counts': counts, 'cases': results}, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    suite = ET.Element(
        'testsuite',
        name='SouthsideMusic harness',
        tests=str(len(results)),
        failures=str(counts['failed']),
        skipped=str(counts['skipped']),
        time=str(sum(result['duration'] for result in results)),
    )
    rows = []
    for result in results:
        case = ET.SubElement(
            suite,
            'testcase',
            classname='harness.' + result['group'],
            name=result['id'],
            time=f'{result["duration"]:.3f}',
        )
        if result['status'] != 'passed':
            element = ET.SubElement(
                case, 'failure' if result['status'] == 'failed' else 'skipped'
            )
            element.text = result.get('detail', '')
        rows.append(
            '<tr><td>'
            + html.escape(result['id'])
            + '</td><td>'
            + result['status']
            + '</td><td>'
            + f'{result["duration"]:.2f}s'
            + '</td><td><pre>'
            + html.escape(result.get('detail', ''))
            + '</pre></td></tr>'
        )
    ET.ElementTree(suite).write(
        directory / 'junit.xml', encoding='utf-8', xml_declaration=True
    )
    (directory / 'index.html').write_text(
        '<!doctype html><meta charset="utf-8"><title>SouthsideMusic harness</title>'
        '<style>body{background:#17191d;color:#ddd;font:15px system-ui;margin:32px}'
        'td,th{padding:10px;border-bottom:1px solid #444;text-align:left}pre{white-space:pre-wrap}</style>'
        '<h1>SouthsideMusic harness</h1><p>' + html.escape(str(counts)) + '</p>'
        '<p><a href="results.json">JSON</a> · <a href="junit.xml">JUnit</a> · '
        '<a href="sessions/">Logs, screenshots and transcripts</a></p>'
        '<table><tr><th>Case</th><th>Status</th><th>Time</th><th>Detail</th></tr>'
        + ''.join(rows)
        + '</table>',
        encoding='utf-8',
    )
    (directory / 'summary.md').write_text(
        f'SouthsideMusic harness: {counts["passed"]} passed, {counts["failed"]} failed, {counts["skipped"]} skipped.\n\n'
        + '\n'.join(
            f'- {result["id"]}: {result["status"]}'
            + (
                ': ' + result.get('detail', '').splitlines()[0]
                if result.get('detail')
                else ''
            )
            for result in results
        ),
        encoding='utf-8',
    )
