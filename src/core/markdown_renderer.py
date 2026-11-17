from __future__ import annotations

import base64
import logging
import re
from functools import lru_cache
from html import escape
from typing import Any, Sequence

from markdown_it import MarkdownIt
from markdown_it.rules_inline.state_inline import StateInline
from markdown_it.token import Token
from ziafont import config as ziafont_config
from ziamath import Latex

_MATH_PATTERN = re.compile(
    r'(?s)(\$\$.*?\$\$|(?<!\\)\$[^$\n]+?(?<!\\)\$|\\\[.*?\\\]|\\\(.*?\\\))'
)
_MARKDOWN = MarkdownIt('commonmark', {'html': False, 'breaks': True}).enable([
    'table',
    'strikethrough',
])
_DEFAULT_FENCE_RENDERER = _MARKDOWN.renderer.rules['fence']
_LATEX_FENCE_LANGUAGES = frozenset({'latex', 'math', 'tex'})
logging.getLogger('markdown_it').setLevel(logging.WARNING)
ziafont_config.svg2 = False


def _formulaParts(value: str) -> tuple[str, bool]:
    if value.startswith('$$') and value.endswith('$$'):
        return value[2:-2].strip(), True
    if value.startswith('\\[') and value.endswith('\\]'):
        return value[2:-2].strip(), True
    if value.startswith('$') and value.endswith('$'):
        return value[1:-1].strip(), False
    if value.startswith('\\(') and value.endswith('\\)'):
        return value[2:-2].strip(), False
    return value.strip(), False


def _parseMath(state: StateInline, silent: bool) -> bool:
    match = _MATH_PATTERN.match(state.src, state.pos, state.posMax)
    if match is None or not _formulaParts(match.group(0))[0]:
        return False
    if not silent:
        state.push('math', '', 0).content = match.group(0)
    state.pos = match.end()
    return True


@lru_cache(maxsize=128)
def _renderLatex(formula: str, is_block: bool, color: str) -> str:
    formula_class = 'math-block' if is_block else 'math-inline'
    try:
        svg = Latex(
            formula,
            size=15,
            color=color,
            inline=not is_block,
        ).svg()
        encoded_svg = base64.b64encode(svg.encode('utf-8')).decode('ascii')
        return (
            f'<img class="{formula_class}" '
            f'src="data:image/svg+xml;base64,{encoded_svg}" '
            f'alt="{escape(formula, quote=True)}">'
        )
    except Exception:
        logging.getLogger(__name__).warning(
            'Could not render LaTeX formula: %s', formula, exc_info=True
        )
        return f'<span class="{formula_class}">{escape(formula)}</span>'


def renderMarkdown(markdown: str, math_color: str = '#ffffff') -> str:
    return _MARKDOWN.render(markdown, {'math_color': math_color})


def _splitBlocks(tokens: Sequence[Token]) -> list[list[Token]]:
    groups: list[list[Token]] = []
    current: list[Token] = []
    depth = 0
    for token in tokens:
        current.append(token)
        depth += token.nesting
        if depth <= 0:
            groups.append(current)
            current = []
            depth = 0
    if current:
        groups.append(current)
    return groups


def renderMarkdownBlocks(markdown: str, math_color: str = '#ffffff') -> list[str]:
    env = {'math_color': math_color}
    tokens = _MARKDOWN.parse(markdown)
    return [
        _MARKDOWN.renderer.render(group, _MARKDOWN.options, env)
        for group in _splitBlocks(tokens)
    ]


def _renderMath(
    tokens: Sequence[Token],
    idx: int,
    options: dict[str, Any],
    env: dict[str, Any],
) -> str:
    formula, is_block = _formulaParts(tokens[idx].content)
    return _renderLatex(formula, is_block, str(env.get('math_color', '#ffffff')))


def _renderImage(
    tokens: Sequence[Token],
    idx: int,
    options: dict[str, Any],
    env: dict[str, Any],
) -> str:
    for child in tokens[idx].children or []:
        if child.type == 'math':
            child.type = 'text'
    return _MARKDOWN.renderer.image(tokens, idx, options, env)


def _renderFence(
    tokens: Sequence[Token],
    idx: int,
    options: dict[str, Any],
    env: dict[str, Any],
) -> str:
    token = tokens[idx]
    info = (token.info or '').strip()
    language = info.split(maxsplit=1)[0].lower() if info else ''
    if language not in _LATEX_FENCE_LANGUAGES:
        return _DEFAULT_FENCE_RENDERER(tokens, idx, options, env)
    math_color = str(env.get('math_color', '#ffffff'))
    return (
        f'<div class="latex-output">{renderMarkdown(token.content, math_color)}</div>'
    )


_MARKDOWN.inline.ruler.before('escape', 'math', _parseMath)
_MARKDOWN.renderer.rules['math'] = _renderMath
_MARKDOWN.renderer.rules['image'] = _renderImage
_MARKDOWN.renderer.rules['fence'] = _renderFence
